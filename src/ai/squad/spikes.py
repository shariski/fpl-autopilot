"""Spike-signal generation: the LLM labels which candidates will spike/drop.

The AI is a speculative operator here — it emits structured signals, never a
squad. The deterministic optimizer consumes the signals as one logged input.
Failures degrade gracefully to no signals (the squad is still deterministic).
"""
import json
import logging
import re
from pathlib import Path

from src.ai import cache, grounding
from src.ai.insight.runner import extract_json_object
from src.ai.squad.digest import build_squad_digest
from src.data import repository
from src.decisions.squad_builder import build_candidate_pool

logger = logging.getLogger(__name__)

PANE_TYPE = "squad_spikes"
MAX_ATTEMPTS = 3
LEVELS = {"high", "medium"}
# "GW2" / "gameweek 2" are fixture labels, not stats — their digits would
# otherwise trip the grounding check as ungrounded numbers (observed 2026-08-20
# with real evidence: "at home in GW2", and after the prompt fix "in gameweek
# 2", were both rejected for citing ['2']).
_GW_RE = re.compile(r"\b(?:gw|game\s?week)\s?\d+\b", re.IGNORECASE)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_spikes_prompt(digest: dict, insights=None) -> str:
    template = (_PROMPTS_DIR / "spikes.md").read_text()
    prompt = template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))
    if insights:
        prompt += ("\n\n## User insights (qualitative context only)\n"
                   "The user watches the matches and may hold manager/cohesion/trait "
                   "reads. You MAY reference an insight qualitatively in a reason, but "
                   "every number you cite must still come from the DIGEST JSON above "
                   "(copy verbatim).\n"
                   + json.dumps(insights, sort_keys=True, indent=2))
    return prompt


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
            if not reason:
                problems.append(f"{kind}[{i}]: reason missing")
                continue
            reason_nums = grounding.numbers_in(_GW_RE.sub("gameweek", reason))
            edge_nums = reason_nums & grounding.numbers_in(edge_text.get(pid, ""))
            stat_nums = reason_nums & grounding.numbers_in(stat_text.get(pid, ""))
            ungrounded = reason_nums - edge_nums - stat_nums
            if ungrounded:
                problems.append(
                    f"{kind}[{i}]: cites {sorted(ungrounded)} not in player data — "
                    f"copy digest numbers verbatim (e.g. '48.1' not '48')")
            elif not edge_nums:
                problems.append(
                    f"{kind}[{i}]: restatement — cite market/trend evidence "
                    f"(transfers, ownership, form, recent_gws, fixtures), "
                    f"not the projection")
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
    # v0.26: user-curated speculation insights are qualitative prompt context;
    # they participate in the cache key so new notes invalidate stale signals.
    notes = repository.list_speculation_notes(conn)
    insights = [{"note": n["note"], "team": n["team_short"], "player": n["player_name"]}
                for n in notes]
    rec_hash = cache.recommendation_hash({"digest": digest, "insights": insights})
    hit = cache.get(conn, gw, PANE_TYPE, rec_hash)
    if hit is not None:
        return extract_json_object(hit["prose"])
    prompt = build_spikes_prompt(digest, insights=insights)
    problems_seen = []
    attempts_log = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            logger.exception("ai.squad.spikes.provider_error", extra={"gw": gw})
            _log_failed(conn, gw, model_id, "provider", attempts=attempts_log)
            return None
        payload = extract_json_object(prose) if prose else None
        if payload is None:
            problems_seen = ["not valid JSON"]
        else:
            problems_seen = validate_signals(payload, pool, digest)
        # persist every attempt for auditability (B10): raw response + gate verdict
        attempts_log.append({"response": (prose or "")[:1500], "problems": problems_seen[:5]})
        if not problems_seen:
            cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(payload, sort_keys=True),
                      model_id)
            return payload
        logger.warning("ai.squad.spikes.attempt_rejected",
                       extra={"gw": gw, "attempt": attempt, "problems": problems_seen[:5]})
        if attempt < MAX_ATTEMPTS - 1:
            prompt = f"{prompt}\n\nPrevious read was rejected: " \
                     f"{'; '.join(problems_seen[:5])}. Output ONLY the JSON."
    _log_failed(conn, gw, model_id, "gate", attempts=attempts_log)
    return None


def _log_failed(conn, gw, model_id, reason, attempts=None):
    from src.data import repository
    payload = {"gw": gw, "model_id": model_id, "result": "spikes_failed", "reason": reason}
    if attempts:
        payload["attempts"] = attempts
    repository.log_activity(conn, decision_type="squad", mode="ai",
                            action_taken="spikes failed", executed=False,
                            inputs=payload)
