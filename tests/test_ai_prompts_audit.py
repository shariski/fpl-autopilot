"""S-G T4: audit prompt + exemplars self-validation tests."""
import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_audit_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "audit.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_audit_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "audit_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 3
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}


def test_audit_examples_cover_three_scenarios():
    """Spec §6: one 'everything calibrated', one 'threshold needs raising', one 'DGW-induced miss'."""
    examples = json.loads((PROMPTS_DIR / "audit_examples.json").read_text())
    # At least one with proposals (threshold change)
    has_proposal = any(ex["input"].get("proposals") for ex in examples)
    # At least one with no proposals (calibrated case)
    has_no_proposal = any(not ex["input"].get("proposals") for ex in examples)
    # At least one mentioning DGW (variety check)
    has_dgw = any("dgw" in ex["output"].lower() or "double gameweek" in ex["output"].lower()
                  for ex in examples)
    assert has_proposal, "need an exemplar with at least one proposed adjustment"
    assert has_no_proposal, "need an exemplar with zero proposed adjustments"
    assert has_dgw, "need an exemplar covering a DGW residual"


def test_every_audit_example_output_is_grounded():
    """Every numeric token in each output appears in its input — self-validating."""
    examples = json.loads((PROMPTS_DIR / "audit_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"audit example {i} prose contains ungrounded numbers: {ungrounded}"
