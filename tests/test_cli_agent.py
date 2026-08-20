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
    return {"mode": {"current": "manual"}, "xp_model": {"version": "v2"}}


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
    assert data["data_basis"] == {"as_of_utc": "2026-08-15T10:00:00Z", "xp_model_version": "v2"}


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
            "mode": {"current": "manual"}, "xp_model": {"version": "v2"},
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
    db.execute(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at) "
        "SELECT player_id, gw, 'v2', xp, xminutes, xgoals, xassists, xcs, computed_at "
        "FROM xp WHERE model_version='v1'")
    db.commit()
    db.commit()


def test_captain_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_captain_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "captain"
    assert out["data"]["picks"] and "data_basis" in out["data"]
    assert out["data"]["data_basis"]["xp_model_version"] == "v2"


def test_main_captain_without_json_flag_ok(monkeypatch):
    """--json is no longer required: plain `fpl-autopilot captain` must work
    (pretty terminal view by default; --json keeps the machine envelope).
    Commands without a --json flag must not flip the pretty mode."""
    seen = {}

    def fake(**kw):
        seen["kw"] = kw

    monkeypatch.setattr(cli, "_cmd_captain_cli", fake)
    monkeypatch.setattr(cli, "_freeze_cli", fake)
    cli.main(["captain"])
    assert cli._PRETTY is True
    cli.main(["captain", "--json"])
    assert cli._PRETTY is False
    cli.main(["freeze"])  # no --json arg on this command
    assert cli._PRETTY is False
    cli._PRETTY = False


def test_captain_pretty_without_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._PRETTY = True
    try:
        cli._cmd_captain_cli(conn=db, cfg=_cfg())
    finally:
        cli._PRETTY = False
    out = capsys.readouterr().out
    assert not out.startswith("{")
    assert "CAPTAIN" in out.upper()
    assert "1." in out


def test_pretty_squad_table(capsys):
    cli._PRETTY = True
    try:
        cli._print_json({"ok": True, "contract_version": "1", "command": "squad",
                         "generated_at_utc": "2026-08-20T06:00:00Z",
                         "data": {"gw": 1, "status": "cached", "source": "ai",
                                  "picks": [{"slot": "GKP1", "web_name": "Dubravka",
                                             "position": "GKP", "price": 4.0,
                                             "xp_6gw": 41.96}],
                                  "budget_used": 99.0}})
    finally:
        cli._PRETTY = False
    out = capsys.readouterr().out
    assert "GKP1" in out and "Dubravka" in out and "41.96" in out
    assert "99.0m" in out


def test_pretty_transfers(capsys):
    cli._PRETTY = True
    try:
        cli._print_json({"ok": True, "contract_version": "1", "command": "transfers",
                         "generated_at_utc": "2026-08-20T06:00:00Z",
                         "data": {"suggestions": [
                             {"out": {"web_name": "Quenda", "price": 5.5},
                              "in": {"web_name": "B.Fernandes", "price": 12.0},
                              "ep_delta_5gw": 28.2, "hit_cost": 0, "confidence": 40}],
                             "free_transfers": 1}})
    finally:
        cli._PRETTY = False
    out = capsys.readouterr().out
    assert "Quenda" in out and "B.Fernandes" in out and "28.2" in out
    assert "->" in out


def test_pretty_generic_nested(capsys):
    cli._PRETTY = True
    try:
        cli._print_json({"ok": True, "contract_version": "1", "command": "insight",
                         "generated_at_utc": "2026-08-20T06:00:00Z",
                         "data": {"player": {"name": "Saka", "x": None},
                                  "tags": ["a", "b"],
                                  "rows": [{"k": 1}, {"k": 2}]}})
    finally:
        cli._PRETTY = False
    out = capsys.readouterr().out
    assert "Saka" in out
    assert "a, b" in out
    assert "None" not in out
    assert "rows (2):" in out


def test_pretty_error(capsys):
    cli._PRETTY = True
    try:
        with pytest.raises(SystemExit):
            cli._json_err("squad", "E_NO_DATA", "no upcoming gameweek", "run refresh")
    finally:
        cli._PRETTY = False
    out = capsys.readouterr().out
    assert "E_NO_DATA" in out and "no upcoming gameweek" in out and "run refresh" in out


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


def test_squad_candidates_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_squad_cli(conn=db, cfg=_cfg(), candidates_only=True)
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "squad" and out["data"]["gw"] == 1
    assert out["data"]["count"] == len(out["data"]["pool"]) > 0
    p = out["data"]["pool"][0]
    assert {"player_id", "web_name", "team_short", "position", "price", "xp_next",
            "xp_6gw", "value", "ownership_pct", "form", "transfers_in",
            "transfers_out", "net_momentum"} <= set(p)


def test_squad_candidates_no_data(db, capsys):
    with pytest.raises(SystemExit) as exc:
        cli._cmd_squad_cli(conn=db, cfg=_cfg(), candidates_only=True)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NO_DATA"


def test_squad_json_built(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai import cache as ai_cache
    from src.ai.squad import runner as squad_runner
    from src.decisions.squad_builder import build_candidate_pool

    pid = build_candidate_pool(db)[0]["player_id"]
    result = {"source": "ai", "picks": [{"player_id": pid, "slot": "GKP1", "reason": "good"}],
              "template_rationale": "template", "risks": ["rotation"], "speculation": None}
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg: None)
    monkeypatch.setattr(squad_runner, "generate_squad", lambda conn, **k: result)
    cli._cmd_squad_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "generated"
    assert out["data"]["gw"] == 1 and out["data"]["source"] == "ai"
    assert out["data"]["picks"][0]["player_id"] == pid
    assert out["data"]["picks"][0]["web_name"]  # enriched from the pool
    assert out["data"]["budget_used"] >= 0
    assert out["data"]["data_basis"]["xp_model_version"] == "v2"


def test_squad_json_cached(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    import json as _json
    from src.ai import cache as ai_cache
    from src.ai.squad import runner as squad_runner

    pool = __import__("src.decisions.squad_builder", fromlist=["build_candidate_pool"]).build_candidate_pool(db)
    digest = squad_runner.build_squad_digest(db, pool=pool)
    rec_hash = ai_cache.recommendation_hash(digest)
    payload = _json.dumps({"source": "ai",
                           "picks": [{"player_id": pool[0]["player_id"], "slot": "GKP1",
                                      "reason": "cached"}],
                           "template_rationale": "t", "risks": [], "speculation": None},
                          sort_keys=True)
    db.execute("INSERT INTO ai_reasoning_cache (gw, pane_type, recommendation_hash, prose, "
               "model_id, generated_at) VALUES (?, 'squad', ?, ?, 'deepseek-chat', "
               "'2026-08-15T08:00:00Z')", (1, rec_hash, payload))
    db.commit()
    monkeypatch.setattr(squad_runner, "generate_squad",
                        lambda conn, **k: (_ for _ in ()).throw(AssertionError("must be cache hit")))
    cli._cmd_squad_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "cached"
    assert out["data"]["picks"][0]["reason"] == "cached"


def test_squad_json_ai_disabled(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src import config
    monkeypatch.setattr(config, "ai_enabled", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_squad_cli(conn=db, cfg=_cfg())
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_RUNTIME"


def test_speculate_json(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.squad import spikes
    signals = {"spikes": [{"player_id": 234, "level": "high", "reason": "in 48.1"}],
               "drops": [], "market_read": "market quiet"}
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg: None)
    monkeypatch.setattr(spikes, "generate_spike_signals",
                        lambda conn, **k: signals)
    cli._cmd_speculate_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["gw"] == 1
    assert out["data"]["signals"]["spikes"][0]["player_id"] == 234
    # no my_team snapshot -> every spike is a differential
    assert [s["player_id"] for s in out["data"]["differentials"]] == [234]


def test_speculate_json_failure(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.squad import spikes
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg: None)
    monkeypatch.setattr(spikes, "generate_spike_signals", lambda conn, **k: None)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_speculate_cli(conn=db, cfg=_cfg())
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_RUNTIME"


def test_insight_json_generated(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.insight import runner as insight_runner
    pid = db.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    payload = {"insights": [{"category": "value_market", "claim": "In 48.1", "evidence_used": ["48.1"],
                             "confidence": "high"}], "summary": "solid", "data_limits": []}
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg: None)
    monkeypatch.setattr(insight_runner, "generate_player_insight",
                        lambda conn, player_id, **k: payload)
    cli._cmd_insight_cli(pid, conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "insight" and out["data"]["status"] == "generated"
    assert out["data"]["player_id"] == pid
    assert out["data"]["insights"][0]["category"] == "value_market"
    assert out["data"]["player"]["web_name"]
    assert out["data"]["data_basis"]["xp_model_version"] == "v2"


def test_insight_json_cached(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    import json as _json
    from src.ai import cache as ai_cache
    from src.ai.insight import runner as insight_runner
    pid = db.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    digest = insight_runner.build_player_digest(db, pid)
    rec_hash = ai_cache.recommendation_hash(digest)
    payload = _json.dumps({"insights": [{"category": "fixture_alignment", "claim": "In 48.1",
                                         "evidence_used": ["48.1"], "confidence": "high"}],
                           "summary": "cached summary", "data_limits": []}, sort_keys=True)
    db.execute("INSERT INTO ai_reasoning_cache (gw, pane_type, recommendation_hash, prose, "
               "model_id, generated_at) VALUES (?, 'insight', ?, ?, 'deepseek-chat', "
               "'2026-08-15T08:00:00Z')", (1, rec_hash, payload))
    db.commit()
    monkeypatch.setattr(insight_runner, "generate_player_insight",
                        lambda conn, player_id, **k: (_ for _ in ()).throw(AssertionError("cache hit expected")))
    cli._cmd_insight_cli(pid, conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "cached"
    assert out["data"]["summary"] == "cached summary"


def test_insight_json_unknown_player(db, capsys):
    with pytest.raises(SystemExit) as exc:
        cli._cmd_insight_cli(999999, conn=db, cfg=_cfg())
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NO_DATA"


from types import SimpleNamespace


def test_live_refuses_non_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["execute-lineup", "--live"])
    assert exc.value.code == 2
    assert "--live" in capsys.readouterr().err


def test_live_ok_with_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(master, "is_initialized", lambda **k: False)
    cli.main(["execute-lineup", "--live"])
    assert "Master password not set" in capsys.readouterr().out


def test_dry_run_never_needs_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(master, "is_initialized", lambda **k: False)
    cli.main(["execute-lineup"])
    assert "Master password not set" in capsys.readouterr().out


def test_execute_lineup_applies_bench_order(monkeypatch, db):
    """execute-lineup must also apply the ranker's bench order (positions
    13-15) by default — FPL's auto-subs follow bench order, so leaving it
    untouched means a sub-optimal sub when a starter misses. The CLI only set
    captain/vice; deadguard was the only caller passing optimize_bench."""
    import src.auth.master as master
    from src.execution import lineup as lineup_mod

    captured = {}

    def fake_run(conn, key, **kw):
        captured.update(kw)
        return SimpleNamespace(ok=True, dry_run=True, status=None,
                               request={"method": "POST", "url": "x", "body": {}})

    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    monkeypatch.setattr(master, "get_master_key", lambda **k: b"k")
    monkeypatch.setattr(lineup_mod, "run_lineup", fake_run)
    cli._execute_lineup_cli(conn=db, salt_path="s", verify_path="v")
    assert captured.get("optimize_bench") is True


def test_insight_json_provider_unavailable(load, db, capsys):
    """Provider start failure must produce an E_RUNTIME envelope, not a traceback."""
    import os
    if os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY set; the provider would start")
    _seed_decision_data(db, load)
    pid = db.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    with pytest.raises(SystemExit) as exc:
        cli._cmd_insight_cli(pid, conn=db, cfg=_cfg())
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_RUNTIME"
    assert "provider" in out["error"]["message"]
