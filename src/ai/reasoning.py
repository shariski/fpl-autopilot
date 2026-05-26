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
    gap = round(top["xp"] - vice["xp"], 1) if vice is not None else None
    return {
        "captain": {
            "web_name": top["web_name"],
            "xp": top["xp"],
            "fixture": top["fixture"],
        },
        "vice": ({"web_name": vice["web_name"], "xp": vice["xp"]}
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
