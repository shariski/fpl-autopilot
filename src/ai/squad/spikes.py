"""Spike-signal generation: the LLM labels which candidates will spike/drop.

The AI is a speculative operator here — it emits structured signals, never a
squad. The deterministic optimizer consumes the signals as one logged input.
Failures degrade gracefully to no signals (the squad is still deterministic).
"""
import json
import logging
from pathlib import Path

from src.ai import cache, grounding
from src.ai.insight.runner import extract_json_object
from src.ai.squad.digest import build_squad_digest
from src.decisions.squad_builder import build_candidate_pool

logger = logging.getLogger(__name__)

PANE_TYPE = "squad_spikes"
MAX_ATTEMPTS = 3
LEVELS = {"high", "medium"}

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_spikes_prompt(digest: dict) -> str:
    template = (_PROMPTS_DIR / "spikes.md").read_text()
    return template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))


def _per_player_text(digest):
    return {p["player_id"]: json.dumps(p, sort_keys=True) for p in digest.get("players", [])}


# Fields the optimizer already sorts on — a reason citing ONLY these is a
# restatement, not speculation (the AI's edge must come from market/trend data).
_STAT_FIELDS = {"xp_next", "xp_6gw", "xg90", "xa90", "price", "value"}
_EDGE_FIELDS = {"transfers_in", "transfers_out", "net_momentum", "ownership_pct",
                "form", "recent_gws", "fixtures_3"}


def _split_blocks(digest):
    """player_id -> (edge_text, stat_text) for the restatement gate."""
    edge, stat = {}, {}
    for p in digest.get("players", []):
        full = json.dumps(p, sort_keys=True)
        edge[p["player_id"]] = json.dumps({k: p[k] for k in _EDGE_FIELDS if k in p},
                                          sort_keys=True)
        stat[p["player_id"]] = json.dumps({k: p[k] for k in _STAT_FIELDS if k in p},
                                          sort_keys=True)
    return edge, stat


def validate_signals(payload, pool, digest):
    """Return problems; empty = valid. ids ∈ pool, levels bounded, reasons
    grounded against the DIGEST the AI saw (per-player), no player twice, and
    each reason must cite at least one edge-field number (restatements of the
    projection are rejected — the AI's edge is market/trend evidence)."""
    problems = []
    if not isinstance(payload, dict):
        return ["not an object"]
    players = {p["player_id"] for p in pool}
    edge_text, stat_text = _split_blocks(digest)
    seen = set()
    for kind in ("spikes", "drops"):
        items = payload.get(kind)
        if not isinstance(items, list):
            return [f"{kind} missing or not a list"]
        for i, s in enumerate(items):
            pid = s.get("player_id")
            if pid not in players:
                problems.append(f"{kind}[{i}]: unknown player {pid}")
            if s.get("level") not in LEVELS:
                problems.append(f"{kind}[{i}]: bad level {s.get('level')!r}")
            if pid in seen:
                problems.append(f"player {pid} appears twice")
            seen.add(pid)
            reason = s.get("reason") or ""
            ok, bad = grounding.is_grounded(reason, edge_text.get(pid, ""))
            if not ok:
                # grounded only in projection stats = restatement, not a call
                ok2, _ = grounding.is_grounded(reason, stat_text.get(pid, ""))
                if ok2:
                    problems.append(
                        f"{kind}[{i}]: restatement — cite market/trend evidence "
                        f"(transfers, ownership, form, recent_gws, fixtures), "
                        f"not the projection")
                else:
                    problems.append(f"{kind}[{i}]: reason cites {sorted(bad)} "
                                    f"not in player data")
    if not isinstance(payload.get("market_read"), str) or not payload["market_read"].strip():
        problems.append("market_read missing")
    return problems


def generate_spike_signals(conn, *, provider, model_id, max_tokens: int = 2000,
                           temperature: float = 0.4) -> dict | None:
    """Generate (or fetch cached) spike/drop signals. Never raises; None on
    failure (the squad path then runs without speculation)."""
    pool = build_candidate_pool(conn)
    if not pool:
        return None
    digest = build_squad_digest(conn, pool=pool)
    gw = digest.get("next_gw")
    if gw is None:
        return None
    rec_hash = cache.recommendation_hash(digest)
    hit = cache.get(conn, gw, PANE_TYPE, rec_hash)
    if hit is not None:
        return extract_json_object(hit["prose"])
    prompt = build_spikes_prompt(digest)
    problems_seen = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            logger.exception("ai.squad.spikes.provider_error", extra={"gw": gw})
            _log_failed(conn, gw, model_id, "provider")
            return None
        payload = extract_json_object(prose) if prose else None
        if payload is None:
            problems_seen.append("not valid JSON")
        else:
            problems_seen = validate_signals(payload, pool, digest)
        if not problems_seen:
            cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(payload, sort_keys=True),
                      model_id)
            return payload
        logger.warning("ai.squad.spikes.attempt_rejected",
                       extra={"gw": gw, "attempt": attempt, "problems": problems_seen[:5]})
        if attempt < MAX_ATTEMPTS - 1:
            prompt = f"{prompt}\n\nPrevious read was rejected: " \
                     f"{'; '.join(problems_seen[:5])}. Output ONLY the JSON."
    _log_failed(conn, gw, model_id, "gate")
    return None


def _log_failed(conn, gw, model_id, reason):
    from src.data import repository
    repository.log_activity(conn, decision_type="squad", mode="ai",
                            action_taken="spikes failed", executed=False,
                            inputs={"gw": gw, "model_id": model_id, "result": "spikes_failed",
                                    "reason": reason})
