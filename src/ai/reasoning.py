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
