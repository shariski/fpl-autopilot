import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_chip_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "chip.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_chip_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "chip_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 4
    chip_types_covered = {ex["input"]["chip"] for ex in examples}
    assert chip_types_covered == {"wildcard", "free_hit", "bench_boost", "triple_captain"}
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_chip_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "chip_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"chip example {i} ({ex['input']['chip']}) prose contains ungrounded numbers: {ungrounded}"
