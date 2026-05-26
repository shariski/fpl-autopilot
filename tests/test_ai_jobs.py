from src.data.db import connect, init_db
from src.ai import jobs, provider as prv, cache as ai_cache, reasoning


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-05-20T11:00:00Z', 0, 1, 0, "
                 "'PENDING')")
    conn.commit()
    return conn


CAPTAIN_DECISION = {
    "picks": [
        {"player_id": 10, "web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)",
         "reason": "Highest xP (7.2) MCI v BRE (H). Next best Salah 5.4 — gap 1.8."},
        {"player_id": 6, "web_name": "Salah", "xp": 5.4, "fixture": "LIV v EVE (A)",
         "reason": "xP 5.4 LIV v EVE (A)."},
    ],
    "vice_player_id": 6,
    "confidence": 82,
}


def test_generate_ai_reasoning_job_caches_captain_prose():
    conn = _db()
    stub = prv.StubProvider("Haaland at 7.2 xP, gap 1.8, confidence 82.")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "ok"}
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash) is not None


def test_generate_ai_reasoning_job_reports_cached_on_second_run():
    conn = _db()
    stub = prv.StubProvider("Haaland at 7.2 xP, gap 1.8, confidence 82.")
    jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called — already cached")

    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=_BoomProvider(), model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "ok"}


def test_generate_ai_reasoning_job_reports_failed_on_grounding_violation():
    conn = _db()
    stub = prv.StubProvider("Haaland xP 9.9 confidence 99.")        # ungrounded
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "failed"}


def test_generate_ai_reasoning_job_returns_skipped_when_no_next_gw():
    conn = connect(":memory:")
    init_db(conn)
    stub = prv.StubProvider("anything")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "skipped"}
