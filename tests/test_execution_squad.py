import pytest

from src.data.db import connect, init_db
from src.execution import squad


def _seed_conn():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    for pid, name in [(10, "Old1"), (11, "Old2"), (12, "Keep")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, 'DEF', 5.0, 'a', 10.0, 3.0)",
                     (pid, name))
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?, "
                 "'2026-08-22T08:00:00Z')",
                 ('[{"element": 10, "position": 1, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 11, "position": 2, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 12, "position": 3, "is_captain": false, "is_vice_captain": false, "multiplier": 1}]',))
    conn.commit()
    return conn


def test_plan_builds_out_in_pairs():
    conn = _seed_conn()
    target = [{"player_id": 12}, {"player_id": 20}, {"player_id": 21}]
    plan = squad.plan_squad_transfers(conn, target)
    outs = {p["element_out"] for p in plan}
    assert outs == {10, 11}
    assert {p["element_in"] for p in plan} == {20, 21}
    conn.close()


def test_plan_empty_when_identical():
    conn = _seed_conn()
    target = [{"player_id": 10}, {"player_id": 11}, {"player_id": 12}]
    assert squad.plan_squad_transfers(conn, target) == []
    conn.close()


def test_apply_squad_dry_run_posts_nothing(monkeypatch):
    conn = _seed_conn()
    from types import SimpleNamespace

    monkeypatch.setattr("src.execution.executor.apply_transfers",
                        lambda s, e, payload, dry_run: SimpleNamespace(ok=True))
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50},
                                      {"element": 11, "selling_price": 50},
                                      {"element": 12, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=False, provider=None)
    assert out["dry_run"] is True and len(out["applied"]) == 1
    row = conn.execute("SELECT * FROM activity_log WHERE decision_type='squad'").fetchone()
    assert row is not None
    conn.close()


def test_apply_squad_refuses_when_no_diff(monkeypatch):
    conn = _seed_conn()
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers", lambda c, target: [])
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=True, provider=None)
    assert out["applied"] == [] and out["failed"] == ["no changes to apply"]
    conn.close()


def test_apply_squad_aborts_on_api_refusal(monkeypatch):
    conn = _seed_conn()

    class _Refused(Exception):
        pass

    def _apply(session, entry, payload, dry_run):
        raise _Refused("transfer refused by API")

    monkeypatch.setattr("src.execution.executor.apply_transfers", _apply)
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=True, confirm_fn=lambda d: True,
                            provider=None)
    assert out["failed"] and "refused" in out["failed"][0].lower()
    conn.close()
