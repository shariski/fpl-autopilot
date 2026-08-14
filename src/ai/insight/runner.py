"""Insight runner: digest -> prompt -> LLM -> JSON -> grounding gate -> cache.

Pattern follows src/ai/reasoning.py (generate_*_prose): cache-first, provider
errors swallowed + logged, ungrounded output never cached. Adds a retry-with-
feedback loop (kerf Coach pattern) capped at 3 attempts.
"""
import json
import logging

from src.ai import cache, grounding
from src.ai.insight.digest import build_player_digest
from src.ai.insight.prompt import build_analysis_prompt
from src.ai.provider import DeepSeekError, OllamaError

logger = logging.getLogger(__name__)

PANE_TYPE = "insight"
MAX_ATTEMPTS = 3
CATEGORIES = {"overperformance", "fixture_alignment", "minutes_role", "value_market"}


def extract_json_object(text: str) -> dict | None:
    """Strip markdown fences; slice first { .. last }; None when malformed."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def validate_payload(payload: dict) -> list[str]:
    """Return a list of problems; empty list = valid shape."""
    problems = []
    if not isinstance(payload, dict):
        return ["not an object"]
    insights = payload.get("insights")
    if not isinstance(insights, list) or not insights:
        return ["insights missing or empty"]
    for i, ins in enumerate(insights):
        if not isinstance(ins, dict):
            problems.append(f"insight {i}: not an object")
            continue
        if ins.get("category") not in CATEGORIES:
            problems.append(f"insight {i}: bad category")
        if not isinstance(ins.get("claim"), str) or not ins["claim"].strip():
            problems.append(f"insight {i}: claim missing")
        if not isinstance(ins.get("evidence_used"), list):
            problems.append(f"insight {i}: evidence_used must be a list")
        if ins.get("confidence") not in ("high", "medium", "low"):
            problems.append(f"insight {i}: bad confidence")
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        problems.append("summary missing")
    return problems


def _ungrounded_numbers(payload: dict, digest_text: str) -> set[str]:
    ungrounded = set()
    for ins in payload.get("insights", []):
        if not isinstance(ins, dict):
            continue
        for field in ("claim", "implication"):
            text = ins.get(field) or ""
            ok, bad = grounding.is_grounded(text, digest_text)
            if not ok:
                ungrounded |= bad
        for ev in ins.get("evidence_used", []):
            ok, bad = grounding.is_grounded(str(ev), digest_text)
            if not ok:
                ungrounded |= bad
    return ungrounded


def _log_activity(conn, player_id, gw, model_id, *, input_tokens, output_tokens,
                  gate_result, extra=None):
    from src.data import repository
    payload = {"player_id": player_id, "gw": gw, "model_id": model_id,
               "input_tokens": input_tokens, "output_tokens": output_tokens,
               "gate_result": gate_result}
    if extra:
        payload.update(extra)
    repository.log_activity(conn, decision_type="ai.insight", mode="ai",
                            action_taken="insight generate", inputs=payload,
                            executed=True)


def generate_player_insight(conn, player_id, *, provider, model_id,
                            max_tokens: int = 2000, temperature: float = 0.2) -> dict | None:
    """Generate (or fetch cached) insight payload for one player. Never raises."""
    digest = build_player_digest(conn, player_id)
    if digest is None:
        return None
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    gw = nxt["gw"] if nxt else None
    if gw is None:
        return None
    rec_hash = cache.recommendation_hash(digest)
    hit = cache.get(conn, gw, PANE_TYPE, rec_hash)
    if hit is not None:
        payload = extract_json_object(hit["prose"])
        return payload if payload is not None else None

    digest_text = json.dumps(digest, sort_keys=True)
    prompt = build_analysis_prompt(digest)
    problems_seen = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
        except (OllamaError, DeepSeekError):
            logger.exception("ai.insight.provider_error",
                             extra={"player_id": player_id, "gw": gw, "model_id": model_id})
            _log_activity(conn, player_id, gw, model_id, input_tokens=0, output_tokens=0,
                          gate_result="failed", extra={"attempt": attempt, "problem": "provider"})
            return None
        if not prose:
            logger.warning("ai.insight.empty_prose",
                           extra={"player_id": player_id, "gw": gw})
            problems_seen.append("empty response")
        else:
            payload = extract_json_object(prose)
            if payload is None:
                problems_seen.append("not valid JSON")
            else:
                shape = validate_payload(payload)
                if shape:
                    problems_seen.extend(shape[:3])
                else:
                    ungrounded = _ungrounded_numbers(payload, digest_text)
                    if ungrounded:
                        problems_seen.append(
                            f"ungrounded numbers: {sorted(ungrounded)}")
                    else:
                        cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(payload, sort_keys=True),
                                  model_id)
                        _log_activity(conn, player_id, gw, model_id, input_tokens=0,
                                      output_tokens=0, gate_result="passed")
                        return payload
        logger.warning("ai.insight.attempt_rejected",
                       extra={"player_id": player_id, "gw": gw, "attempt": attempt,
                              "problem": problems_seen[-1]})
        if attempt < MAX_ATTEMPTS - 1:
            prompt = f"{prompt}\n\nPrevious attempt was rejected by the quality gate: " \
                     f"{problems_seen[-1]}. Rewrite so every number appears verbatim " \
                     f"in the digest, and output ONLY the JSON."
    _log_activity(conn, player_id, gw, model_id, input_tokens=0, output_tokens=0,
                  gate_result="failed", extra={"problems": problems_seen[:3]})
    return None
