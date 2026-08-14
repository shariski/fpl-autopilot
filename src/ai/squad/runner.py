"""Squad runner: deterministic optimization with an AI speculation input.

The deterministic optimizer picks the squad (legal, xP-maximized); the AI
speculation layer (spikes.py) provides spike/drop signals that adjust the
optimizer's sort key by fixed constants (SPIKE_BONUS/DROP_BONUS). The AI never
touches legality — it only labels players. Signals failing → no speculation.
Never raises.
"""
import json
import logging

from src.ai import cache
from src.ai.insight.runner import extract_json_object
from src.ai.squad import spikes
from src.ai.squad.digest import build_squad_digest
from src.decisions.squad_builder import build_candidate_pool
from src.decisions.squad_validator import DROP_BONUS, SPIKE_BONUS, optimize_squad

logger = logging.getLogger(__name__)

PANE_TYPE = "squad"


def _log(conn, gw, model_id, *, result, picks=None, extra=None):
    from src.data import repository
    payload = {"gw": gw, "model_id": model_id, "result": result}
    if picks is not None:
        payload["picks"] = picks
    if extra:
        payload.update(extra)
    repository.log_activity(conn, decision_type="squad", mode="ai",
                            action_taken="squad generate", inputs=payload, executed=True)


def _bonus_map(signals):
    """player_id -> xp adjustment from the AI speculation labels."""
    bonus = {}
    if not signals:
        return bonus
    for s in signals.get("spikes", []):
        bonus[s["player_id"]] = SPIKE_BONUS[s["level"]]
    for s in signals.get("drops", []):
        bonus[s["player_id"]] = DROP_BONUS[s["level"]]
    return bonus


def generate_squad(conn, *, provider, model_id, max_tokens: int = 3000,
                   temperature: float = 0.2) -> dict | None:
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
        payload = extract_json_object(hit["prose"])
        if payload is not None:
            return payload
    # AI speculation layer (optional input; never blocks)
    signals = spikes.generate_spike_signals(conn, provider=provider, model_id=model_id)
    bonus = _bonus_map(signals)
    picks = optimize_squad(pool, bonus)
    by_id = {p["player_id"]: p for p in pool}
    budget_used = round(sum(by_id[pk["player_id"]]["price"] for pk in picks), 1)
    picked_ids = {pk["player_id"] for pk in picks}
    # differential calls: spiked players the optimizer still left out
    differentials = []
    if signals:
        for s in signals.get("spikes", []):
            if s["player_id"] not in picked_ids:
                differentials.append(s)
    result = {
        "picks": [{"player_id": pk["player_id"], "slot": pk["slot"],
                   "reason": (f"Highest projected {by_id[pk['player_id']]['position']} "
                              f"available: {by_id[pk['player_id']]['xp_6gw']} xP over 6 GWs "
                              f"at £{by_id[pk['player_id']]['price']}m."),
                   "spike_bonus": bonus.get(pk["player_id"])}
                  for pk in picks],
        "template_rationale": _rationale(picks, by_id, budget_used, signals, differentials),
        "risks": [],
        "source": "ai" if signals else "deterministic",
        "speculation": {
            "spikes": signals.get("spikes", []) if signals else [],
            "drops": signals.get("drops", []) if signals else [],
            "differentials": differentials,
            "market_read": signals.get("market_read", "") if signals else "",
        } if signals else None,
    }
    cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(result, sort_keys=True), model_id)
    logged_spec = None
    if signals:
        logged_spec = {"spikes": [(s["player_id"], s["level"]) for s in signals.get("spikes", [])],
                       "drops": [(s["player_id"], s["level"]) for s in signals.get("drops", [])]}
    _log(conn, gw, model_id, result="passed" if signals else "deterministic",
         picks=[p["player_id"] for p in picks],
         extra={"speculation": logged_spec})
    return result


def _rationale(picks, by_id, budget_used, signals, differentials=None):
    differentials = differentials or []
    best = max(picks, key=lambda pk: by_id[pk["player_id"]]["xp_6gw"])
    base = (f"Deterministic selection: {budget_used}m of 100m used, "
            f"top pick {by_id[best['player_id']]['web_name']} "
            f"({by_id[best['player_id']]['xp_6gw']} xP).")
    if not signals:
        return base + (" AI speculation unavailable this run — pure xP "
                       "optimization.")
    n_spikes = len(signals.get("spikes", []))
    n_drops = len(signals.get("drops", []))
    diff = (f" {len(differentials)} spike calls left out of the XI — "
            "see the differential section.") if differentials else ""
    return (f"{base} AI speculation active: {n_spikes} spike calls, "
            f"{n_drops} drop calls influenced the ranking.{diff}")
