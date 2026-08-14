"""Prompt builder for the player insight analysis call."""
import json
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_analysis_prompt(digest: dict) -> str:
    template = (_PROMPTS_DIR / "analysis.md").read_text()
    return template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))
