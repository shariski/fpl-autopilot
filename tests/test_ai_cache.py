from src.data.db import connect, init_db
from src.ai import cache


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_recommendation_hash_stable_across_key_ordering():
    a = {"x": 1, "y": [2, 3], "z": "hi"}
    b = {"z": "hi", "y": [2, 3], "x": 1}
    assert cache.recommendation_hash(a) == cache.recommendation_hash(b)


def test_recommendation_hash_changes_when_payload_changes():
    a = {"x": 1}
    b = {"x": 2}
    assert cache.recommendation_hash(a) != cache.recommendation_hash(b)


def test_recommendation_hash_is_short_hex():
    h = cache.recommendation_hash({"x": 1})
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_get_returns_none_on_miss():
    conn = _db()
    assert cache.get(conn, gw=38, pane_type="captain", rec_hash="abc") is None


def test_put_then_get_round_trips():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="Haaland captain.", model_id="qwen2.5:7b-instruct-q4_K_M")
    hit = cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")
    assert hit is not None
    assert hit["prose"] == "Haaland captain."
    assert hit["model_id"] == "qwen2.5:7b-instruct-q4_K_M"
    assert hit["generated_at"] is not None


def test_put_is_idempotent_on_same_key():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="v1", model_id="m")
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="v2", model_id="m")
    hit = cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")
    assert hit["prose"] == "v2"


def test_different_panes_dont_collide():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="cap", model_id="m")
    cache.put(conn, gw=38, pane_type="transfer", rec_hash="abc",
              prose="trn", model_id="m")
    assert cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")["prose"] == "cap"
    assert cache.get(conn, gw=38, pane_type="transfer", rec_hash="abc")["prose"] == "trn"
