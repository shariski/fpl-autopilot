"""Squad builder prompt builder."""
import json
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_squad_prompt(digest: dict) -> str:
    template = (_PROMPTS_DIR / "squad.md").read_text()
    return template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))
