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


def _seed_transfer_minimum(conn):
    """Minimum seed so _build_transfer_payload returns non-None for the standard fixture."""
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), (2, 'Brentford', 'BRE')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), (20, 'Watkins', 'FWD', 2, 9.0, 'd')")
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) "
                 "VALUES (1, 38, 2, 2, '2026-05-19T00:00Z'), (2, 38, 4, 4, '2026-05-19T00:00Z')")
    conn.commit()


_TRANSFER_DECISION = {
    "suggestions": [
        {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
         "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
         "ep_delta_5gw": 3.5, "hit_cost": 0, "confidence": 78}
    ],
    "empty_reason": None, "free_transfers": 1,
}


def test_generate_ai_reasoning_job_caches_transfer_prose():
    """The 'transfer' pane is processed analogously to 'captain'."""
    conn = _db()
    _seed_transfer_minimum(conn)
    # Grounded prose: 2, 4, 3.5, 78 all appear in the payload JSON
    stub = prv.StubProvider("Sell Watkins (d), buy Haaland — fdr 2 vs fdr 4. "
                            "Free transfer adds 3.5 EP at 78.")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["transfer"], provider=stub, model_id="m",
        transfer_decision_fn=lambda c: _TRANSFER_DECISION)
    assert result == {"transfer": "ok"}


def test_generate_ai_reasoning_job_handles_both_captain_and_transfer():
    conn = _db()
    _seed_transfer_minimum(conn)

    class _TwoResponseStub:
        def __init__(self):
            # Captain prose first, then transfer prose. Each must be grounded
            # against its own pane's payload.
            self.responses = iter([
                # Captain: numbers 7.2, 1.8, 82 are in CAPTAIN_DECISION's payload
                "Haaland is the captain at 7.2 xP, gap 1.8 vs Salah, confidence 82.",
                # Transfer: 2, 4, 3.5, 78 are in _TRANSFER_DECISION's payload
                "Sell Watkins (d), buy Haaland — fdr 2 vs fdr 4. Free transfer adds 3.5 EP at 78.",
            ])
        def generate(self, prompt, **kw):
            return next(self.responses)

    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain", "transfer"], provider=_TwoResponseStub(), model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION,
        transfer_decision_fn=lambda c: _TRANSFER_DECISION)
    assert result == {"captain": "ok", "transfer": "ok"}
