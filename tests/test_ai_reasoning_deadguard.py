import json

from src.data.db import connect, init_db
from src.ai import reasoning, cache as ai_cache, provider as prv


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


OUTCOME_FIXTURE = {
    "captain_name": "Haaland",
    "vice_name": "Salah",
    "bench_changed": True,
    "transfer": {"out_name": "Watkins", "in_name": "Calvert-Lewin"},
    "gw": 38,
}


def test_build_deadguard_payload_shape():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    assert payload == {
        "captain": "Haaland",
        "vice": "Salah",
        "bench_changed": True,
        "transfer": {"out_name": "Watkins", "in_name": "Calvert-Lewin"},
        "gw": 38,
    }


def test_build_deadguard_payload_returns_none_on_missing_captain():
    conn = _db()
    assert reasoning._build_deadguard_payload(conn, {}) is None
    assert reasoning._build_deadguard_payload(conn, {"captain_name": None}) is None


def test_build_deadguard_payload_handles_no_transfer():
    conn = _db()
    outcome = {"captain_name": "Haaland", "vice_name": "Salah",
               "bench_changed": False, "transfer": None, "gw": 38}
    payload = reasoning._build_deadguard_payload(conn, outcome)
    assert payload["transfer"] is None
    assert payload["bench_changed"] is False


def test_build_deadguard_prompt_includes_payload_and_examples():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    prompt = reasoning._build_deadguard_prompt(payload)
    assert "Haaland" in prompt
    assert "Calvert-Lewin" in prompt
    assert "Watkins" in prompt
    # At least one exemplar's captain should be in the prompt
    assert "Saka" in prompt or "Salah" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "ONLY use names and numbers" in prompt


def test_render_deadguard_summary_returns_empty_classic_on_cache_miss():
    """Classic returns empty — the deadguard module composes its own template
    summary at the _notify site when AI is unavailable."""
    conn = _db()
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome=OUTCOME_FIXTURE)
    assert source == "classic"
    assert prose == ""


def test_render_deadguard_summary_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash,
                 prose="Deadguard set captain Haaland and ran a transfer.", model_id="m")
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome=OUTCOME_FIXTURE)
    assert source == "ai"
    assert prose == "Deadguard set captain Haaland and ran a transfer."


def test_render_deadguard_summary_returns_empty_classic_on_missing_outcome():
    conn = _db()
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome={})
    assert source == "classic"
    assert prose == ""


def test_generate_deadguard_summary_caches_grounded_prose():
    conn = _db()
    # Grounded: gw 38 + names appear in payload
    stub = prv.StubProvider("Deadguard set Haaland as captain and Salah as vice for GW38, "
                            "reordered the bench, and transferred out Watkins for Calvert-Lewin.")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash) is not None


def test_generate_deadguard_summary_rejects_ungrounded_prose():
    conn = _db()
    # Wrong gw number not in payload
    stub = prv.StubProvider("Deadguard set Haaland captain for GW99, season standings 7.")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_deadguard_summary_rejects_empty_prose():
    conn = _db()
    stub = prv.StubProvider("")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_deadguard_summary_skips_on_missing_outcome():
    conn = _db()

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called when outcome is missing")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome={},
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_deadguard_summary_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash,
                 prose="cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called on cache hit")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_deadguard_summary_swallows_provider_errors():
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("down")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
