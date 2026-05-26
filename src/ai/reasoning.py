"""Per-pane LLM reasoning: payload + prompt builders, render (read) and
generate (write) functions. The AI sub-layer's only public surface for
consumers in src/interface and src/scheduler.

Task 6 lands the captain payload + prompt builders. Tasks 7-8 add the
render (read path) and generate (write path) functions.
"""
import json
import logging
from pathlib import Path

from src.ai import cache, grounding
from src.ai.provider import OllamaError

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _build_captain_payload(captain_decision: dict) -> dict | None:
    """Narrow, closed-shape payload built from get_captain_picks() output.

    Returns None if no picks (the LLM has nothing to render).
    """
    picks = captain_decision.get("picks", [])
    if not picks:
        return None
    top = picks[0]
    vice = picks[1] if len(picks) > 1 else None
    # Round xp values to 1dp so the payload is internally self-consistent
    # (matches the few-shot exemplar style). With unrounded xp + a pre-rounded
    # gap, the model recomputes the gap from the precise xps and produces a
    # number not in the payload — fails grounding. Rounding both keeps the
    # math consistent (captain_xp - vice_xp == gap).
    captain_xp = round(top["xp"], 1)
    vice_xp = round(vice["xp"], 1) if vice is not None else None
    gap = round(captain_xp - vice_xp, 1) if vice_xp is not None else None
    return {
        "captain": {
            "web_name": top["web_name"],
            "xp": captain_xp,
            "fixture": top["fixture"],
        },
        "vice": ({"web_name": vice["web_name"], "xp": vice_xp}
                 if vice is not None else None),
        "alternative_gap": gap,
        "confidence": captain_decision.get("confidence"),
    }


def _build_captain_prompt(payload: dict) -> str:
    """Render captain.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "captain.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "captain_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)


def render_captain_reasoning(conn, gw: int, captain_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source) where source ∈ {'ai', 'classic'}.

    Never calls a provider — only reads from the cache. Falls back to the
    deterministic engine's existing `reason` string when nothing is cached.
    """
    payload = _build_captain_payload(captain_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "captain", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    return (captain_decision["picks"][0]["reason"], "classic")


def generate_captain_prose(conn, gw: int, captain_decision: dict, *,
                           provider, model_id: str,
                           max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).

    Called by the scheduler. Provider errors are caught and logged — never
    bubble. Ungrounded prose is not cached; the grounding violation is logged.
    """
    payload = _build_captain_payload(captain_decision)
    if payload is None:
        logger.info("ai.captain.skipped_empty_picks", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "captain", rec_hash) is not None:
        return True
    prompt = _build_captain_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.captain.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.captain.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.captain.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "captain", rec_hash, prose, model_id)
    return True


def _next_gw(conn) -> int | None:
    """Return next unfinished gameweek id, or None."""
    row = conn.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def _status_for(conn, player_id: int) -> str:
    """Player status flag (a/d/i/s/u). Defaults to 'a' when player is missing."""
    row = conn.execute(
        "SELECT status FROM players WHERE id=?", (player_id,)).fetchone()
    return row["status"] if row is not None else "a"


def _fixtures_for(conn, player_id: int, next_gw: int, horizon: int) -> list[dict]:
    """Up to `horizon` fixtures for the player's team starting at next_gw.

    Each item: {opponent: short_name, home: bool, fdr_attack: int}.
    BGW (no fixture for a given gw) is silently skipped.
    DGW (multiple fixtures for the same gw) is surfaced as multiple list entries.
    """
    team_row = conn.execute(
        "SELECT team_id FROM players WHERE id=?", (player_id,)).fetchone()
    if team_row is None:
        return []
    team_id = team_row["team_id"]
    rows = conn.execute(
        """SELECT f.gw, f.home_team_id, f.away_team_id,
                  th.short_name AS home_short, ta.short_name AS away_short,
                  fdr.fdr_attack AS fdr_attack
           FROM fixtures f
           JOIN teams th ON th.id = f.home_team_id
           JOIN teams ta ON ta.id = f.away_team_id
           LEFT JOIN fdr ON fdr.team_id = ? AND fdr.gw = f.gw
           WHERE f.gw BETWEEN ? AND ?
             AND (f.home_team_id = ? OR f.away_team_id = ?)
           ORDER BY f.gw, f.id""",
        (team_id, next_gw, next_gw + horizon - 1, team_id, team_id),
    ).fetchall()
    out = []
    for r in rows:
        is_home = r["home_team_id"] == team_id
        opp = r["away_short"] if is_home else r["home_short"]
        fdr_a = r["fdr_attack"] if r["fdr_attack"] is not None else 3
        out.append({"opponent": opp, "home": is_home, "fdr_attack": fdr_a})
    return out


def _build_transfer_payload(conn, transfer_decision: dict) -> dict | None:
    """Closed-shape payload for the TOP transfer suggestion, with rich fixture context.

    Returns None when:
    - no suggestions (LLM has nothing to render)
    - no next gw (post-season state)
    """
    suggestions = transfer_decision.get("suggestions", [])
    if not suggestions:
        return None
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    top = suggestions[0]
    return {
        "out": {
            "web_name": top["out"]["web_name"],
            "price": top["out"]["price"],
            "status": _status_for(conn, top["out"]["player_id"]),
            "fixtures_3gw": _fixtures_for(conn, top["out"]["player_id"], next_gw, horizon=3),
        },
        "in": {
            "web_name": top["in"]["web_name"],
            "price": top["in"]["price"],
            "status": _status_for(conn, top["in"]["player_id"]),
            "fixtures_3gw": _fixtures_for(conn, top["in"]["player_id"], next_gw, horizon=3),
        },
        "ep_delta_5gw": round(top["ep_delta_5gw"], 1),
        "hit_cost": top["hit_cost"],
        "confidence": top["confidence"],
        "free_transfers": transfer_decision.get("free_transfers"),
    }


def _build_transfer_prompt(payload: dict) -> str:
    """Render transfer.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "transfer.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "transfer_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)


def render_transfer_reasoning(conn, gw: int, transfer_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> ('', 'classic').
    Empty suggestions or no next_gw -> ('', 'classic')."""
    payload = _build_transfer_payload(conn, transfer_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "transfer", rec_hash)
    return (hit["prose"], "ai") if hit is not None else ("", "classic")


def generate_transfer_prose(conn, gw: int, transfer_decision: dict, *,
                            provider, model_id: str,
                            max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors caught; empty/ungrounded prose not cached."""
    payload = _build_transfer_payload(conn, transfer_decision)
    if payload is None:
        logger.info("ai.transfer.skipped_empty", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "transfer", rec_hash) is not None:
        return True
    prompt = _build_transfer_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.transfer.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.transfer.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.transfer.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "transfer", rec_hash, prose, model_id)
    return True


def _build_chip_payload(conn, chip_decision: dict) -> dict | None:
    """Closed-shape payload for the chip recommendation.

    Returns None when:
    - no recommendation (LLM has nothing to render)
    - no next gw (post-season state)
    """
    rec = chip_decision.get("recommendation")
    if rec is None:
        return None
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    return {
        "chip": rec["chip"],
        "reason": rec["reason"],
        "next_gw": next_gw,
    }


def _build_chip_prompt(payload: dict) -> str:
    """Render chip.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "chip.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "chip_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)


def render_chip_reasoning(conn, gw: int, chip_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> (engine_reason, 'classic').
    No recommendation -> ('', 'classic')."""
    payload = _build_chip_payload(conn, chip_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "chip", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    return (chip_decision["recommendation"]["reason"], "classic")


def generate_chip_prose(conn, gw: int, chip_decision: dict, *,
                        provider, model_id: str,
                        max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors caught; empty/ungrounded prose not cached."""
    payload = _build_chip_payload(conn, chip_decision)
    if payload is None:
        logger.info("ai.chip.skipped_empty", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "chip", rec_hash) is not None:
        return True
    prompt = _build_chip_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.chip.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.chip.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.chip.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "chip", rec_hash, prose, model_id)
    return True
