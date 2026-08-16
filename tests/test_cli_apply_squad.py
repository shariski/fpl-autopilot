from src import cli
from src.data.db import connect, init_db


def _seed(conn):
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                 "ownership, form) VALUES (10, 'Old1', 1, 'DEF', 5.0, 'a', 10.0, 3.0)")
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?, "
                 "'2026-08-22T08:00:00Z')",
                 ('[{"element": 10, "position": 1, "is_captain": false, "is_vice_captain": false, "multiplier": 1}]',))
    conn.commit()


def test_apply_squad_dry_run_prints_plan(monkeypatch, capsys):
    from src.execution import squad
    conn = connect(":memory:")
    _seed(conn)
    monkeypatch.setattr(squad, "plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr(squad, "apply_squad", lambda *a, **kw: {
        "applied": [], "failed": [], "dry_run": True,
        "pairs": [{"out_name": "Old1", "in_name": "New1"}]})
    cli._cmd_apply_squad(conn=conn, live=False, provider=object())
    out = capsys.readouterr().out
    assert "Old1" in out and "New1" in out and "dry" in out.lower()
    conn.close()


def test_apply_squad_live_requires_confirm(monkeypatch, capsys):
    from src.execution import squad
    conn = connect(":memory:")
    _seed(conn)
    monkeypatch.setattr(squad, "plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr(squad, "apply_squad", lambda *a, **kw: {
        "applied": [], "failed": ["aborted by user"], "dry_run": False})
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "pw")
    monkeypatch.setattr("src.auth.master.is_initialized", lambda **kw: True)
    monkeypatch.setattr("src.auth.master.get_master_key", lambda **kw: b"key")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    cli._cmd_apply_squad(conn=conn, live=True, provider=object())
    out = capsys.readouterr().out
    assert "aborted" in out.lower()
    conn.close()


def test_apply_squad_builds_ai_provider_when_none(monkeypatch, capsys):
    """apply-squad must build the AI provider like every other AI CLI path —
    provider=None previously crashed inside spikes.generate_spike_signals."""
    from src.execution import squad

    sentinel = object()
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg: sentinel)
    captured = {}
    monkeypatch.setattr(squad, "plan_squad_transfers", lambda c, target: [])

    def fake_apply(conn, key, **kw):
        captured["kw"] = kw
        return {"applied": [], "failed": [], "dry_run": True, "pairs": []}

    monkeypatch.setattr(squad, "apply_squad", fake_apply)
    conn = connect(":memory:")
    _seed(conn)
    cli._cmd_apply_squad(conn=conn, live=False)
    assert captured["kw"]["provider"] is sentinel
    conn.close()


def test_apply_squad_empty_snapshot_reports_actionable_failure(monkeypatch):
    """Pre-season: no saved squad yet -> 'no changes to apply' is misleading;
    the failure must say the snapshot is missing."""
    from src.execution import squad
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.commit()
    monkeypatch.setattr("src.ai.squad.runner.generate_squad", lambda conn, **kw: {
        "picks": [{"player_id": 20, "slot": "DEF1"}]})
    res = squad.apply_squad(conn, b"key", live=False)
    assert res["failed"] and "snapshot" in res["failed"][0]
    conn.close()
