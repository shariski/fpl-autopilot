"""Squad runner: digest -> prompt -> LLM picks -> validator -> cache/fallback.

The AI proposes; validate_squad is the law. Illegal proposals get retried with
feedback (<=3); on total failure the deterministic optimize_squad fallback
produces the squad, flagged source="optimizer". Never raises.
"""
import json
import logging

from src.ai import cache, grounding
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


def _per_player_text(digest):
    """player_id -> JSON text of that player's digest entry (for per-pick grounding)."""
    return {p["player_id"]: json.dumps(p, sort_keys=True) for p in digest.get("players", [])}


def _reason_problems(payload, digest):
    """Every number in a pick's reason must appear in THAT player's digest entry —
    otherwise the AI misattributed another player's stats (observed 2026-08-14:
    'Evanilson ... 39.12' was Haaland's projection)."""
    per_player = _per_player_text(digest)
    problems = []
    for pick in payload.get("picks", []):
        pid = pick.get("player_id")
        reason = pick.get("reason") or ""
        block = per_player.get(pid, "")
        ok, bad = grounding.is_grounded(reason, block)
        if not ok:
            problems.append(f"reason for player {pid} cites numbers not in their data: "
                            f"{sorted(bad)}")
    return problems


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
                problems = _reason_problems(payload, digest)
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
            budget_hint = ""
            budget_probs = [p for p in problems if "budget exceeded" in p]
            if budget_probs:
                try:
                    over = float(budget_probs[0].split(":")[1].split("m")[0].strip())
                    budget_hint = (f" You are over budget by "
                                   f"{max(0.1, round(over - 100.0, 1))}m — "
                                   f"remove that much price from your picks.")
                except (IndexError, ValueError):
                    pass
            prompt = f"{prompt}\n\nPrevious proposal was rejected by the validator: " \
                     f"{'; '.join(problems[:5])}.{budget_hint} " \
                     f"Output ONLY the JSON with a legal squad."
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
    try:
        picks = optimize_squad(pool)
    except Exception:
        logger.exception("ai.squad.optimizer_failed", extra={"gw": gw})
        return None
    fallback = {"picks": picks, "template_rationale": "Deterministic fallback: greedy "
                "value-optimized selection.", "risks": [], "source": "optimizer"}
    cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(fallback, sort_keys=True), model_id)
    _log(conn, gw, model_id, result="fallback",
         picks=[p["player_id"] for p in fallback["picks"]],
         extra={"problems": problems[:5] if "problems" in locals() else []})
    return fallback
