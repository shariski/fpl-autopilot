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



class ClaudeError(RuntimeError):
    """Raised when the Claude API call fails or is refused before send (privacy gate)."""


class ClaudeRateLimitError(ClaudeError):
    """Raised when the per-day quota set on the provider has been exhausted."""


# Credential-shaped patterns that must NEVER appear in a prompt sent to Claude (B7).
# Conservative regex set — extend as new credential types appear in the codebase.
import re as _re
_CREDENTIAL_PATTERNS = [
    _re.compile(r"\bpl_profile\b", _re.IGNORECASE),
    _re.compile(r"\bsessionid\s*=", _re.IGNORECASE),
    _re.compile(r"\bcsrftoken\b", _re.IGNORECASE),
    _re.compile(r"X-CSRFToken", _re.IGNORECASE),
]


class ClaudeProvider:
    """Anthropic Claude provider for the audit-narration role. Same LLMProvider protocol as
    OllamaProvider — `generate(prompt, *, max_tokens, temperature) -> str`.

    Guardrails:
    - Pre-send privacy scan: prompts containing credential-shaped strings are refused
      before any network call (B7).
    - Per-day quota: if `max_calls_per_day` ai.audit rows already exist in activity_log
      within the last 24h, refuses with ClaudeRateLimitError.
    - Post-call activity_log entry records token usage (B10) for cost visibility.
    """

    def __init__(self, *, api_key: str, conn,
                 model: str = "claude-sonnet-4-6",
                 timeout_seconds: float = 60.0,
                 max_calls_per_day: int = 5,
                 _client=None):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_calls_per_day = max_calls_per_day
        self._conn = conn
        if _client is not None:
            self._client = _client
        else:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    def generate(self, prompt: str, *, max_tokens: int = 1500,
                 temperature: float = 0.2) -> str:
        self._refuse_credential_prompts(prompt)
        self._enforce_daily_quota()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            raise ClaudeError(f"claude request failed: {type(e).__name__}") from e
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        self._log_usage(response)
        return text.strip()

    # ---------- guardrails ----------

    def _refuse_credential_prompts(self, prompt):
        for pat in _CREDENTIAL_PATTERNS:
            if pat.search(prompt):
                raise ClaudeError(
                    f"prompt rejected: contains credential/sensitive pattern ({pat.pattern})")

    def _enforce_daily_quota(self):
        row = self._conn.execute(
            """SELECT COUNT(*) AS n FROM activity_log
               WHERE decision_type='ai.audit'
                 AND datetime(ts_utc) >= datetime('now', '-1 day')""").fetchone()
        n_recent = row["n"] if row else 0
        if n_recent >= self.max_calls_per_day:
            raise ClaudeRateLimitError(
                f"daily quota exhausted ({n_recent}/{self.max_calls_per_day} calls in last 24h)")

    def _log_usage(self, response):
        import json as _json
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        self._conn.execute(
            """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
                 inputs_json, executed)
               VALUES (datetime('now'), NULL, 'audit', 'ai.audit', 'claude generate',
                       ?, 1)""",
            (_json.dumps({"model": self.model,
                          "input_tokens": int(input_tokens),
                          "output_tokens": int(output_tokens)}),)
        )
        self._conn.commit()
