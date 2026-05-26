import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_deadguard_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "deadguard.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_deadguard_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "deadguard_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 3
    has_captain_only = any(ex["input"].get("transfer") is None and
                            not ex["input"].get("bench_changed", False) for ex in examples)
    has_bench = any(ex["input"].get("bench_changed") and ex["input"].get("transfer") is None
                    for ex in examples)
    has_transfer = any(ex["input"].get("transfer") is not None for ex in examples)
    assert has_captain_only and has_bench and has_transfer
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}


def test_every_deadguard_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "deadguard_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"deadguard example {i} prose contains ungrounded numbers: {ungrounded}"
