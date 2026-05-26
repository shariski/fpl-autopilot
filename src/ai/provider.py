"""LLM provider interface + concrete providers.

The Protocol is intentionally tiny — one method, `generate`. Swapping providers
(e.g. Claude API later) is implementing this Protocol in a new class. Tests
inject `StubProvider`; production injects `OllamaProvider` (added in task 4).
"""
from typing import Protocol


class LLMProvider(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        ...


class StubProvider:
    """Test/fixture provider — returns a canned response. Used everywhere a test
    needs an LLM (R3 — no live calls in tests)."""

    def __init__(self, fixed_response: str = "<stub prose>"):
        self.fixed_response = fixed_response

    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        return self.fixed_response
