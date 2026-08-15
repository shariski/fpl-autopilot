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


from src.data.models import BootstrapStatic, EntryPicks, Fixture
import requests


class _FakeClient:
    def __init__(self, bs, fx, picks):
        self._bs, self._fx, self._picks = bs, fx, picks

    def bootstrap_static(self):
        return self._bs

    def fixtures(self, event=None):
        return self._fx

    def picks(self, team_id, gw):
        return self._picks


class _NoSquadClient(_FakeClient):
    def picks(self, team_id, gw):
        resp = requests.Response()
        resp.status_code = 404
        resp.url = "https://fantasy.premierleague.com/api/entry/1/event/1/picks/"
        raise requests.exceptions.HTTPError("404 Client Error", response=resp)


def _refresh_cfg():
    return {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
            "mode": {"current": "manual"}, "xp_model": {"version": "v1"},
            "understat": {"season": "2026"}}


def test_refresh_report_json_shape(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_FakeClient(bs, fx, picks), sources=("fpl",), report=True)
    assert report["fpl"]["bootstrap_static"]["players"] == len(bs.elements)
    assert report["fpl"]["fixtures"] == len(fx)
    # The frozen fixture marks GW38 is_next (pre-season fixtures have no GW1 flags).
    assert report["fpl"]["my_team"]["gw"] == 38
    assert report["fpl"]["my_team_skipped"] is None
    assert capsys.readouterr().out == ""
    conn.close()


def test_refresh_report_skipped_squad_no_stdout(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_NoSquadClient(bs, fx, None), sources=("fpl",), report=True)
    assert report["fpl"]["my_team_skipped"] == 38
    assert report["fpl"]["my_team"] is None
    assert capsys.readouterr().out == ""


def test_refresh_report_collects_warnings(load, capsys, monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))

    def boom(*a, **k):
        raise RuntimeError("rematch exploded")
    monkeypatch.setattr(cli, "_rematch_prior_understat", boom)
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_FakeClient(bs, fx, picks), sources=("fpl",), report=True)
    assert report["warnings"] == ["understat prior rematch failed (rematch exploded)"]
    assert capsys.readouterr().out == ""
    conn.close()


def _seed_decision_data(db, load):
    """Full deterministic seed: teams/players/gameweeks/fixtures/understat/fdr/xp."""
    from src.analytics import fdr, xp
    from src.data import name_resolver, repository
    from src.data.models import BootstrapStatic, Fixture, UnderstatPlayersResponse

    from src.data.models import EntryPicks

    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    repository.upsert_gameweeks(db, bs.events)
    db.execute("UPDATE gameweeks SET is_next=0, is_current=0, finished=0 WHERE 1")
    db.execute("UPDATE gameweeks SET is_next=1 WHERE id=1")
    repository.snapshot_my_team(db, 1, EntryPicks.model_validate(load("picks.json")))
    repository.upsert_fixtures(db, [Fixture.model_validate(f) for f in load("fixtures.json")])
    us = UnderstatPlayersResponse.model_validate(
        load("understat-players.json")).players
    fpl_players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(db, us, res, "2026")
    fdr.compute_and_store(db)
    xp.compute_and_store(db)
    db.commit()


def test_captain_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_captain_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "captain"
    assert out["data"]["picks"] and "data_basis" in out["data"]
    assert out["data"]["data_basis"]["xp_model_version"] == "v1"


def test_transfers_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_transfers_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "transfers"
    assert set(out["data"]) == {"suggestions", "empty_reason", "free_transfers", "data_basis"}
    for s in out["data"]["suggestions"]:
        assert set(s["out"]) == {"player_id", "web_name", "price"}
        assert set(s["in"]) == {"player_id", "web_name", "price"}


def test_chips_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_chips_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "chips"
    assert "recommendation" in out["data"] and "data_basis" in out["data"]


def test_freeze_status_json(db, capsys):
    db.execute("INSERT INTO system_state (key, value) VALUES "
               "('freeze', '{\"since\": \"2026-08-15T07:00:00Z\", \"source\": \"user\", \"reason\": \"travel\"}')")
    db.commit()
    cli._cmd_freeze_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["frozen"] == {"is_frozen": True, "since": "2026-08-15T07:00:00Z",
                                     "source": "user", "reason": "travel"}
    db.execute("DELETE FROM system_state")
    db.commit()
    cli._cmd_freeze_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["frozen"] == {"is_frozen": False}


def test_auth_status_json(db, capsys):
    cli._cmd_auth_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["auth"] is None
    db.execute("INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0)")
    db.commit()
    cli._cmd_auth_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["auth"]["state"] == "active"
    assert out["data"]["auth"]["relogin_failures"] == 0
    assert all(k not in out["data"]["auth"] for k in ("password", "token", "cookie"))
