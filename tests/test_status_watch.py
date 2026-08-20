import json

from src.data.db import connect, init_db
from src.interface import status_watch


def _conn_with_squad(players):
    conn = connect(":memory:")
    init_db(conn)
    picks = [{"element": pid, "position": i + 1, "multiplier": 1,
              "is_captain": False, "is_vice_captain": False}
             for i, (pid, status, cop) in enumerate(players)]
    conn.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, "
                 "chips_used_json, snapshot_at) VALUES (1, ?, 0, 100, 1, '[]', 't')",
                 (json.dumps(picks),))
    for pid, status, cop in players:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "chance_of_playing) VALUES (?, ?, 1, 'MID', 6.0, ?, ?)",
                     (pid, f"P{pid}", status, cop))
    conn.commit()
    return conn


def test_changed_detects_status_worsening():
    before = {1: ("a", 100.0)}
    after = {1: ("i", 0.0)}
    assert status_watch.changed(before, after) == [(1, "a", 100.0, "i", 0.0)]


def test_changed_detects_cop_drop_without_status_change():
    before = {1: ("d", 75.0)}
    after = {1: ("d", 25.0)}
    assert status_watch.changed(before, after) == [(1, "d", 75.0, "d", 25.0)]


def test_changed_small_cop_drop_ignored():
    before = {1: ("a", 100.0)}
    after = {1: ("a", 80.0)}  # 20 pts < 25 floor
    assert status_watch.changed(before, after) == []


def test_changed_ignores_improvement_unchanged_removed_new():
    before = {1: ("i", 0.0), 2: ("a", 100.0), 3: ("d", 100.0), 4: ("a", None)}
    after = {1: ("a", 100.0), 2: ("a", 100.0), 3: ("d", 100.0), 5: ("i", 0.0)}
    # 1 improved, 2 no-op, 3 status + cop unchanged, 4 removed, 5 new to squad
    assert status_watch.changed(before, after) == []


def test_run_watch_notifies_and_logs(monkeypatch):
    conn = _conn_with_squad([(1, "a", 100.0)])
    sent = []
    monkeypatch.setattr(status_watch.telegram, "notify",
                        lambda conn, **kw: sent.append(kw) or True)
    before = status_watch.squad_status_snapshot(conn)
    conn.execute("UPDATE players SET status='i', chance_of_playing=0.0 WHERE id=1")
    conn.commit()
    alerted = status_watch.run_watch(conn, before)
    assert alerted == [(1, "i", 0.0)]
    assert len(sent) == 1
    assert "P1" in sent[0]["summary"] and "injured" in sent[0]["summary"]
    assert "0%" in sent[0]["summary"] and "100%" in sent[0]["summary"]
    assert sent[0]["kind"] == "status" and sent[0]["decision_type"] == "status"
    row = conn.execute("SELECT action_taken, inputs_json FROM activity_log "
                       "WHERE decision_type='status'").fetchone()
    assert row is not None
    assert row["action_taken"] == "status change a->i"
    assert "P1" not in row["inputs_json"] or "player_id" in row["inputs_json"]


def test_run_watch_silent_when_no_change(monkeypatch):
    conn = _conn_with_squad([(1, "a", 100.0)])
    sent = []
    monkeypatch.setattr(status_watch.telegram, "notify",
                        lambda conn, **kw: sent.append(kw) or True)
    before = status_watch.squad_status_snapshot(conn)
    alerted = status_watch.run_watch(conn, before)
    assert alerted == [] and sent == []


def test_run_watch_no_squad_no_alert():
    conn = connect(":memory:")
    init_db(conn)
    assert status_watch.squad_status_snapshot(conn) == {}
    assert status_watch.run_watch(conn, {}) == []


def test_run_watch_notify_failure_does_not_abort(monkeypatch):
    conn = _conn_with_squad([(1, "a", 100.0), (2, "a", 100.0)])
    calls = []

    def flaky(conn, **kw):
        calls.append(kw["summary"])
        raise RuntimeError("telegram down")

    monkeypatch.setattr(status_watch.telegram, "notify", flaky)
    before = status_watch.squad_status_snapshot(conn)
    conn.execute("UPDATE players SET status='i', chance_of_playing=0.0")
    conn.commit()
    alerted = status_watch.run_watch(conn, before)
    assert len(alerted) == 2  # both players alerted despite notify failures
    assert len(calls) == 2
