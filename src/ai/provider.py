"""LLM provider interface + concrete providers.

The Protocol is intentionally tiny — one method, `generate`. Swapping providers
(e.g. Claude API later) is implementing this Protocol in a new class. Tests
inject `StubProvider`; production injects `OllamaProvider` (added in task 4).
"""
from typing import Protocol

import requests


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


class OllamaError(RuntimeError):
    """Raised when the Ollama call fails (network, non-200, malformed JSON)."""


class OllamaProvider:
    """Minimal HTTP client against Ollama's /api/generate endpoint.

    Single-shot completion, no streaming, no chat history. Session injection
    matches src/interface/telegram.py — tests pass a fake session, production
    gets a real requests.Session.
    """

    def __init__(self, host: str, model: str, timeout_seconds: float,
                 session: requests.Session | None = None):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout_seconds)
        except requests.RequestException as e:
            raise OllamaError(f"ollama request failed: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise OllamaError(f"ollama returned {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            raise OllamaError("ollama returned malformed json") from e
        text = body.get("response", "") if isinstance(body, dict) else ""
        return text.strip()
