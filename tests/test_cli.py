import types
from src import cli
from src.data import repository


def _seed_next_gw(db, gw=30):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (?, '2026-05-23T18:00:00+00:00', 1, 0)", (gw,))
    db.commit()


def test_execute_lineup_live_success_marks_user_acted(db, monkeypatch):
    _seed_next_gw(db)
    import src.auth.master as master
    import src.execution.lineup as lineup_mod
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    monkeypatch.setattr(master, "get_master_key", lambda **k: b"key")
    monkeypatch.setattr(lineup_mod, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200, request=None))
    cli._execute_lineup_cli(conn=db, live=True, confirm_fn=lambda d: True)
    row = db.execute("SELECT state, last_user_action_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED" and row["last_user_action_at"] is not None


def test_execute_lineup_dryrun_does_not_mark(db, monkeypatch):
    _seed_next_gw(db)
    import src.auth.master as master
    import src.execution.lineup as lineup_mod
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    monkeypatch.setattr(master, "get_master_key", lambda **k: b"key")
    monkeypatch.setattr(lineup_mod, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=False, dry_run=True, status=None,
                                                                     request={"method": "POST", "url": "u", "body": {}}))
    cli._execute_lineup_cli(conn=db, live=False)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"
