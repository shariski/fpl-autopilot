import json
import re
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_captain_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "captain.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_captain_template_calls_it_recommended_not_the_captain():
    """The pane describes a RECOMMENDATION that may not be applied — the model
    must not state 'is the captain' as fact (observed 2026-08-20: the pane said
    'Dubravka is the captain this week' while the live team had Thiago as
    captain because the lineup submit was rejected by FPL)."""
    template = (PROMPTS_DIR / "captain.txt").read_text()
    assert "recommended captain" in template.lower()
    assert 'not "the captain"' in template


def test_examples_contain_no_player_names():
    """Few-shot examples must not contain real player names — the model mimics
    them, and a moved player (Salah → Turkish league, 2026) would leak into
    panes as a hallucination the number-grounding gate cannot catch. Chip names
    (Triple Captain / Bench Boost / Free Hit / Wildcard) are the only allowed
    Title-Case words; sentence-initial words are skipped (not names)."""
    allow = {"Triple", "Captain", "Bench", "Boost", "Free", "Hit", "Wildcard"}
    for f in ("captain_examples.json", "transfer_examples.json",
              "chip_examples.json", "audit_examples.json", "deadguard_examples.json"):
        examples = json.loads((PROMPTS_DIR / f).read_text())
        for i, ex in enumerate(examples):
            for key in ("output", "input"):
                text = json.dumps(ex[key], ensure_ascii=False) if key == "input" else ex[key]
                sentences = re.split(r"(?<=[.!?])\s+", text)
                mid = []
                for s in sentences:
                    words = s.split()
                    mid.extend(words[1:] if words else [])
                bad = [t for t in re.findall(r"\b[A-Z][a-z]+\b", " ".join(mid))
                       if t not in allow]
                assert not bad, f"{f}[{i}] {key} contains Title-Case tokens {bad}"


def test_captain_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "captain_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 2
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "captain_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"example {i} prose contains ungrounded numbers: {ungrounded}"
