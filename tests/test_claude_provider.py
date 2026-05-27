"""S-G T3: ClaudeProvider tests.

All tests mock the anthropic SDK — no live API calls.
"""
import json
from unittest.mock import MagicMock

import pytest

from src.ai.provider import ClaudeError, ClaudeProvider, ClaudeRateLimitError
from src.data.db import connect, init_db


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _mock_anthropic(text="audit narrative goes here", input_tokens=200, output_tokens=80):
    """Build a mock anthropic.Anthropic client that returns the canned response."""
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    client.messages.create.return_value = response
    return client


# ---------- Happy path ----------

def test_claude_provider_calls_api_with_prompt():
    """Provider passes the prompt to anthropic.messages.create."""
    conn = _db()
    client = _mock_anthropic()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    provider.generate("audit this please")

    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["messages"][0]["content"] == "audit this please"


def test_claude_provider_returns_completion_text():
    conn = _db()
    client = _mock_anthropic(text="all transfers tracked at expectation this window")
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    out = provider.generate("anything")
    assert out == "all transfers tracked at expectation this window"


def test_claude_provider_strips_whitespace():
    conn = _db()
    client = _mock_anthropic(text="\n  trimmed text  \n")
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    assert provider.generate("x") == "trimmed text"


def test_claude_provider_passes_temperature_and_max_tokens():
    conn = _db()
    client = _mock_anthropic()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    provider.generate("p", max_tokens=500, temperature=0.1)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] == 500
    assert kwargs["temperature"] == 0.1


# ---------- Error handling ----------

def test_claude_provider_raises_on_api_error():
    """Any anthropic SDK exception → ClaudeError with original chained."""
    conn = _db()
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    with pytest.raises(ClaudeError):
        provider.generate("anything")


# ---------- Privacy property ----------

@pytest.mark.parametrize("bad_prompt", [
    "context: pl_profile=secrettoken12345; user_id=42",
    "the cookie is sessionid=abc123def",
    "csrftoken=anything",
    "X-CSRFToken: secret",
])
def test_claude_provider_no_credential_pattern_in_prompt(bad_prompt):
    """Prompts containing credential-shaped strings raise before any API call."""
    conn = _db()
    client = MagicMock()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    with pytest.raises(ClaudeError, match="credential|sensitive"):
        provider.generate(bad_prompt)

    # The API was NEVER called.
    client.messages.create.assert_not_called()


def test_claude_provider_safe_prompt_does_not_raise():
    """Sanity: a normal prompt with player names + numbers passes the privacy gate."""
    conn = _db()
    client = _mock_anthropic()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    out = provider.generate("Haaland scored 9 points; xP was 6.5. Residual = 5.")
    assert out is not None


# ---------- Cost guardrails ----------

def test_claude_provider_respects_max_calls_per_day():
    """Once max_calls_per_day audit calls exist in activity_log within 24h, the next call raises."""
    conn = _db()
    # Pre-seed with 5 recent ai.audit calls (within the 24h window)
    for _ in range(5):
        conn.execute(
            """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
                 inputs_json, executed)
               VALUES (datetime('now'), NULL, 'audit', 'ai.audit', 'test call', ?, 1)""",
            (json.dumps({"input_tokens": 100, "output_tokens": 50}),))
    conn.commit()

    client = _mock_anthropic()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client,
                              max_calls_per_day=5)

    with pytest.raises(ClaudeRateLimitError):
        provider.generate("safe prompt")
    client.messages.create.assert_not_called()


def test_claude_provider_does_not_block_below_quota():
    """4 prior calls within 24h + max=5 → next call goes through."""
    conn = _db()
    for _ in range(4):
        conn.execute(
            """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
                 inputs_json, executed)
               VALUES (datetime('now'), NULL, 'audit', 'ai.audit', 'test', '{}', 1)""")
    conn.commit()

    client = _mock_anthropic()
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client,
                              max_calls_per_day=5)

    out = provider.generate("safe prompt")
    assert out is not None


# ---------- Activity-log usage tracking ----------

def test_claude_provider_logs_token_usage_to_activity_log():
    """After a successful call, an activity_log row exists with decision_type='ai.audit'
    and the token counts in inputs_json."""
    conn = _db()
    client = _mock_anthropic(text="result", input_tokens=350, output_tokens=120)
    provider = ClaudeProvider(api_key="sk-test", conn=conn, _client=client)

    provider.generate("audit prompt")

    rows = list(conn.execute(
        "SELECT inputs_json FROM activity_log WHERE decision_type='ai.audit'"))
    assert len(rows) == 1
    inputs = json.loads(rows[0]["inputs_json"])
    assert inputs["input_tokens"] == 350
    assert inputs["output_tokens"] == 120
    assert inputs["model"] == "claude-sonnet-4-6"
