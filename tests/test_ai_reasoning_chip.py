import json

from src.data.db import connect, init_db
from src.ai import reasoning, cache as ai_cache, provider as prv


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    return conn


CHIP_DECISION_FIXTURE = {
    "recommendation": {
        "chip": "triple_captain",
        "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
    },
}


def test_build_chip_payload_shape():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    assert payload == {
        "chip": "triple_captain",
        "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
        "next_gw": 38,
    }


def test_build_chip_payload_returns_none_on_no_recommendation():
    conn = _db()
    assert reasoning._build_chip_payload(conn, {"recommendation": None}) is None


def test_build_chip_payload_returns_none_when_no_next_gw():
    conn = connect(":memory:")
    init_db(conn)
    assert reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE) is None


def test_build_chip_prompt_includes_payload_and_examples():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    prompt = reasoning._build_chip_prompt(payload)
    assert "triple_captain" in prompt
    assert "GW39 DGW: Haaland DGW-xP 14.8" in prompt
    assert "wildcard" in prompt
    assert "free_hit" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "Do not invent" in prompt


def test_render_chip_reasoning_returns_classic_engine_reason_on_cache_miss():
    conn = _db()
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE)
    assert source == "classic"
    assert prose == "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2."


def test_render_chip_reasoning_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="chip", rec_hash=rec_hash,
                 prose="Triple Captain on Haaland — strong DGW.", model_id="m")
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE)
    assert source == "ai"
    assert prose == "Triple Captain on Haaland — strong DGW."


def test_render_chip_reasoning_returns_classic_empty_on_no_recommendation():
    conn = _db()
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision={"recommendation": None})
    assert source == "classic"
    assert prose == ""


def test_generate_chip_prose_caches_grounded_prose():
    conn = _db()
    # Grounded prose: numbers 39, 14.8, 12.0, 2 all appear in payload JSON
    stub = prv.StubProvider("Triple Captain on Haaland in GW39 — DGW-xP 14.8 above the 12.0 threshold, FDR 2.")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="chip", rec_hash=rec_hash) is not None


def test_generate_chip_prose_rejects_ungrounded_prose():
    conn = _db()
    stub = prv.StubProvider("Triple Captain — confidence 99 over 99 GWs.")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_chip_prose_rejects_empty_prose():
    conn = _db()
    stub = prv.StubProvider("")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_chip_prose_skips_on_no_recommendation():
    conn = _db()

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called when no recommendation")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision={"recommendation": None},
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_chip_prose_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="chip", rec_hash=rec_hash,
                 prose="cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called on cache hit")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_chip_prose_swallows_provider_errors():
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("down")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
