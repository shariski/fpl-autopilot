"""Post-generation number-grounding check for LLM prose.

Every numeric token in the LLM's prose must appear verbatim in the input payload
text. Failures are treated as hallucinations — the prose is not cached and the
scheduler logs the offence. This is the practical guard for a small open-weight
model under a "do not invent numbers" instruction.
"""
import re

NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Return the set of numeric tokens (ints + decimals) appearing in text."""
    return set(NUMERIC_RE.findall(text))


def is_grounded(prose: str, input_payload_text: str) -> tuple[bool, set[str]]:
    """Every number in prose must appear in input_payload_text.

    Returns (ok, set_of_ungrounded_numbers). ok=True iff ungrounded is empty.
    """
    ungrounded = numbers_in(prose) - numbers_in(input_payload_text)
    return (not ungrounded, ungrounded)
