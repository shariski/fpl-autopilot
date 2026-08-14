"""Squad runner: digest -> prompt -> LLM picks -> validator -> cache/fallback.

The AI proposes; validate_squad is the law. Illegal proposals get retried with
feedback (<=3); on total failure the deterministic optimize_squad fallback
produces the squad, flagged source="optimizer". Never raises.
"""
import json
import logging

from src.ai import cache
from src.ai.insight.runner import extract_json_object
from src.ai.squad.digest import build_squad_digest
from src.ai.squad.prompt import build_squad_prompt
from src.decisions.squad_builder import build_candidate_pool
from src.decisions.squad_validator import optimize_squad, repair_budget, validate_squad

logger = logging.getLogger(__name__)

PANE_TYPE = "squad"
MAX_ATTEMPTS = 3


def _log(conn, gw, model_id, *, result, picks=None, extra=None):
    from src.data import repository
    payload = {"gw": gw, "model_id": model_id, "result": result}
    if picks is not None:
        payload["picks"] = picks
    if extra:
        payload.update(extra)
    repository.log_activity(conn, decision_type="squad", mode="ai",
                            action_taken="squad generate", inputs=payload, executed=True)


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
            payload["source"] = payload.get("source", "ai")
            return payload
    prompt = build_squad_prompt(digest)
    for attempt in range(MAX_ATTEMPTS):
        try:
            prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            logger.exception("ai.squad.provider_error", extra={"gw": gw})
            return None
        payload = extract_json_object(prose) if prose else None
        if payload is None:
            problems = ["not valid JSON"]
        else:
            picks = payload.get("picks")
            problems = validate_squad(picks, pool) if isinstance(picks, list) else \
                ["picks missing or not a list"]
        if not problems:
            payload["source"] = "ai"
            cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(payload, sort_keys=True),
                      model_id)
            _log(conn, gw, model_id, result="passed",
                 picks=[p["player_id"] for p in payload["picks"]])
            return payload
        logger.warning("ai.squad.attempt_rejected",
                       extra={"gw": gw, "attempt": attempt, "problems": problems[:5]})
        if attempt < MAX_ATTEMPTS - 1:
            prompt = f"{prompt}\n\nPrevious proposal was rejected by the validator: " \
                     f"{'; '.join(problems[:5])}. Output ONLY the JSON with a legal squad."
    # Deterministic budget repair of the last AI proposal (legal except budget)
    last_payload = locals().get("payload")
    if last_payload is not None and isinstance(last_payload.get("picks"), list):
        repaired = repair_budget(last_payload["picks"], pool)
        if repaired is not None:
            last_payload["picks"] = repaired
            last_payload["source"] = "ai"
            cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(last_payload, sort_keys=True),
                      model_id)
            _log(conn, gw, model_id, result="budget_repaired",
                 picks=[p["player_id"] for p in repaired],
                 extra={"problems": problems[:5]})
            return last_payload
    picks = optimize_squad(pool)
    fallback = {"picks": picks, "template_rationale": "Deterministic fallback: greedy "
                "value-optimized selection.", "risks": [], "source": "optimizer"}
    cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(fallback, sort_keys=True), model_id)
    _log(conn, gw, model_id, result="fallback",
         picks=[p["player_id"] for p in fallback["picks"]],
         extra={"problems": problems[:5] if "problems" in locals() else []})
    return fallback
