import json

from src.ai import reasoning


CAPTAIN_DECISION_FIXTURE = {
    "picks": [
        {"player_id": 10, "web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)",
         "reason": "Highest xP (7.2) MCI v BRE (H). Next best Salah 5.4 — gap 1.8."},
        {"player_id": 6, "web_name": "Salah", "xp": 5.4, "fixture": "LIV v EVE (A)",
         "reason": "xP 5.4 LIV v EVE (A)."},
        {"player_id": 7, "web_name": "Saka", "xp": 5.0, "fixture": "ARS v LIV (A)",
         "reason": "xP 5.0 ARS v LIV (A)."},
    ],
    "vice_player_id": 6,
    "confidence": 82,
}


def test_build_captain_payload_shape():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    assert payload == {
        "captain": {"web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)"},
        "vice": {"web_name": "Salah", "xp": 5.4},
        "alternative_gap": 1.8,
        "confidence": 82,
    }


def test_build_captain_payload_with_single_pick():
    decision = {
        "picks": [{"player_id": 10, "web_name": "Haaland", "xp": 7.2,
                   "fixture": "MCI v BRE (H)", "reason": "..."}],
        "vice_player_id": None,
        "confidence": 60,
    }
    payload = reasoning._build_captain_payload(decision)
    assert payload["vice"] is None
    assert payload["alternative_gap"] is None
    assert payload["captain"]["web_name"] == "Haaland"


def test_build_captain_payload_returns_none_on_empty_picks():
    decision = {"picks": [], "vice_player_id": None, "confidence": None}
    assert reasoning._build_captain_payload(decision) is None


def test_build_captain_prompt_includes_payload_and_examples():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    prompt = reasoning._build_captain_prompt(payload)
    assert "Haaland" in prompt          # from payload
    assert "Saka" in prompt             # from the second few-shot exemplar
    assert '"web_name": "Haaland"' in prompt or json.dumps(payload, sort_keys=True, indent=2) in prompt
    assert "Do not invent" in prompt
    # substitution actually happened — neither placeholder remains in the rendered prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt


from src.data.db import connect, init_db
from src.ai import cache as ai_cache


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_render_captain_reasoning_returns_classic_on_cache_miss():
    conn = _db()
    prose, source = reasoning.render_captain_reasoning(conn, gw=38,
                                                       captain_decision=CAPTAIN_DECISION_FIXTURE)
    assert source == "classic"
    assert prose == CAPTAIN_DECISION_FIXTURE["picks"][0]["reason"]


def test_render_captain_reasoning_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="captain", rec_hash=rec_hash,
                 prose="LLM prose here.", model_id="qwen2.5:7b-instruct-q4_K_M")
    prose, source = reasoning.render_captain_reasoning(conn, gw=38,
                                                       captain_decision=CAPTAIN_DECISION_FIXTURE)
    assert source == "ai"
    assert prose == "LLM prose here."


def test_render_captain_reasoning_returns_classic_on_empty_picks():
    conn = _db()
    decision = {"picks": [], "vice_player_id": None, "confidence": None}
    prose, source = reasoning.render_captain_reasoning(conn, gw=38, captain_decision=decision)
    assert source == "classic"
    assert prose == ""


from src.ai import provider as prv


def test_generate_captain_prose_caches_grounded_prose():
    conn = _db()
    stub = prv.StubProvider("Haaland captain at 7.2 xP, gap 1.8, confidence 82.")
    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    hit = ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash)
    assert hit is not None
    assert hit["prose"] == "Haaland captain at 7.2 xP, gap 1.8, confidence 82."


def test_generate_captain_prose_rejects_ungrounded_prose():
    conn = _db()
    stub = prv.StubProvider("Haaland captain at 7.2 xP — confidence 99.")  # 99 not in payload
    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash) is None


def test_generate_captain_prose_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="captain", rec_hash=rec_hash,
                 prose="already cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called on cache hit")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_captain_prose_skips_on_empty_picks():
    conn = _db()
    decision = {"picks": [], "vice_player_id": None, "confidence": None}

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called with empty picks")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=decision,
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_captain_prose_swallows_provider_errors():
    """Provider exceptions don't bubble — they're logged + the row isn't cached."""
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("ollama is down")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
