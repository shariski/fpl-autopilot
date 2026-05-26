import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_transfer_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "transfer.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_transfer_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "transfer_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 2
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_transfer_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "transfer_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"transfer example {i} prose contains ungrounded numbers: {ungrounded}"
