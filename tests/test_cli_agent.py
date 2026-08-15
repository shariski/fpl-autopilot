import json

import pytest

from src import cli
from src.data.db import connect, init_db


def _seed_status_data(db):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_current, is_next, state, finished) "
               "VALUES (1, '2026-08-21T17:30:00Z', 0, 1, 'PENDING', 0), "
               "(38, '2027-05-30T13:30:00Z', 1, 0, 'PENDING', 0)")
    db.execute("INSERT INTO cache_meta (resource, last_fetched_utc) VALUES "
               "('bootstrap-static', '2026-08-15T10:00:00Z'), "
               "('fixtures', '2026-08-15T10:00:00Z'), "
               "('my_team', '2026-08-15T10:00:00Z'), "
               "('understat', '2026-08-14T09:00:00Z')")
    db.execute("INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, executed) "
               "VALUES ('2026-08-15T09:00:00Z', 1, 'manual', 'squad', 'built squad', 0), "
               "('2026-08-14T18:00:00Z', 1, 'manual', 'override', 'unfrozen (user)', 1)")
    db.execute("INSERT INTO pending_decisions (gw, decision_type, identity_json, summary, status, created_at) "
               "VALUES (1, 'captain', '{}', 'Captain pick needed', 'pending', '2026-08-15T08:00:00Z')")
    db.execute("INSERT INTO system_state (key, value) VALUES "
               "('freeze', '{\"since\": \"2026-08-15T07:00:00Z\", \"source\": \"user\", \"reason\": \"travel\"}')")
    db.execute("INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0)")
    db.commit()


def _cfg():
    return {"mode": {"current": "manual"}, "xp_model": {"version": "v1"}}


def test_status_json_envelope(db, capsys):
    _seed_status_data(db)
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["contract_version"] == "1" and out["command"] == "status"
    data = out["data"]
    assert data["mode"] == "manual"
    assert data["frozen"] == {"is_frozen": True, "since": "2026-08-15T07:00:00Z",
                              "source": "user", "reason": "travel"}
    assert data["auth"]["state"] == "active" and data["auth"]["relogin_failures"] == 0
    assert data["data_freshness"]["bootstrap-static"] == "2026-08-15T10:00:00Z"
    assert data["data_freshness"]["understat"] == "2026-08-14T09:00:00Z"
    assert data["next_gameweek"]["id"] == 1
    assert data["next_gameweek"]["state"] == "PENDING"
    assert data["current_gameweek"]["id"] == 38
    assert len(data["pending_decisions"]) == 1
    assert len(data["last_system_actions"]) == 2
    assert data["health"] == {"db_ok": True, "players": 0, "teams": 0}
    assert data["data_basis"] == {"as_of_utc": "2026-08-15T10:00:00Z", "xp_model_version": "v1"}


def test_status_empty_db(db, capsys):
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["frozen"] == {"is_frozen": False}
    assert data["auth"] is None
    assert data["next_gameweek"] is None
    assert data["pending_decisions"] == []
    assert data["data_freshness"] == {"bootstrap-static": None, "fixtures": None,
                                      "my_team": None, "understat": None}


def test_resume_includes_activity_and_rules(db, capsys):
    _seed_status_data(db)
    cli._cmd_resume_cli(conn=db, cfg=_cfg(), tail=10, json_out=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert [e["decision_type"] for e in data["activity"]["entries"]] == ["override", "squad"]
    assert data["activity"]["entries"][1]["executed"] is False
    rules = data["operating_rules"]
    assert "captain" in rules["agent_safe_commands"]
    assert "apply-squad" in rules["human_only_commands"]
    assert rules["boot_ritual"][0].startswith("resume")


def test_log_filters(db, capsys):
    db.execute("INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, executed) VALUES "
               "('2026-08-15T09:00:00Z', 1, 'manual', 'squad', 'built squad', 0), "
               "('2026-08-14T18:00:00Z', 1, 'deadguard', 'transfer', 'auto sub', 1), "
               "('2026-08-14T08:00:00Z', 37, 'manual', 'transfer', 'free transfer', 1)")
    db.commit()
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, gw=1, json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert len(entries) == 2
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, mode="deadguard", json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert [e["decision_type"] for e in entries] == ["transfer"]
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, decision_type="transfer", gw=37, json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert len(entries) == 1 and entries[0]["action_taken"] == "free transfer"


def test_json_err_exit_code_and_shape(capsys):
    with pytest.raises(SystemExit) as exc:
        cli._json_err("squad", "E_NO_DATA", "no upcoming gameweek", "run refresh first")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["command"] == "squad"
    assert out["error"] == {"code": "E_NO_DATA", "message": "no upcoming gameweek",
                            "hint": "run refresh first"}


def test_status_text_mode(db, capsys):
    _seed_status_data(db)
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=False)
    text = capsys.readouterr().out
    assert "mode: manual" in text and "next GW: 1" in text and "frozen" in text
