import pytest
import requests
from src import cli
from src.data.db import connect, init_db
from src.data.models import BootstrapStatic, EntryPicks, Fixture


class FakeClient:
    def __init__(self, bs, fx, picks):
        self._bs, self._fx, self._picks = bs, fx, picks

    def bootstrap_static(self):
        return self._bs

    def fixtures(self, event=None):
        return self._fx

    def picks(self, team_id, gw):
        return self._picks


class NoSquadClient(FakeClient):
    """FPL returns 404 for entry/{id}/event/{gw}/picks/ when no squad is saved yet (pre-season)."""

    def picks(self, team_id, gw):
        resp = requests.Response()
        resp.status_code = 404
        resp.url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{gw}/picks/"
        raise requests.exceptions.HTTPError("404 Client Error", response=resp)


def test_refresh_populates_db(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    client = FakeClient(bs, fx, picks)
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"}}

    cli.refresh(full=True, cfg=cfg, conn=conn, client=client, sources=("fpl",))  # FPL-only: no live Understat call

    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == len(bs.teams)
    assert conn.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == len(fx)
    assert conn.execute("SELECT COUNT(*) c FROM my_team").fetchone()["c"] == 1
    conn.close()


def test_refresh_skips_my_team_when_no_squad_saved(load, capsys):
    """A 404 from the picks endpoint (squad not saved for the new season) must not abort refresh."""
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"}}

    cli.refresh(full=True, cfg=cfg, conn=conn, client=NoSquadClient(bs, fx, None), sources=("fpl",))

    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == len(fx)
    assert conn.execute("SELECT COUNT(*) c FROM my_team").fetchone()["c"] == 0
    assert "no squad saved" in capsys.readouterr().out
    conn.close()


def test_current_gw_prefers_next_gw_pre_season(db):
    """Pre-season: no finished/current GW; picks must resolve to the is_next GW (GW1), not MAX(id)."""
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1), "
               "(38, '2027-05-30T13:30:00Z', 0, 0, 0)")
    db.commit()
    assert cli._current_gw_from_db(db) == 1


def test_current_gw_prefers_next_gw_mid_season(db):
    """Mid-season: is_next (upcoming GW) wins over is_current (live GW)."""
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (5, '2026-09-25T17:30:00Z', 0, 1, 0), "
               "(6, '2026-10-02T17:30:00Z', 0, 0, 1)")
    db.commit()
    assert cli._current_gw_from_db(db) == 6


class FakeUnderstatClient:
    def __init__(self, resp):
        self._resp = resp
        self.called = False

    def players_stats(self, season="2025"):
        self.called = True
        return self._resp


class BoomUnderstatClient:
    def players_stats(self, season="2025"):
        raise RuntimeError("understat down")


def _understat_resp(load):
    from src.data.models import UnderstatPlayersResponse
    return UnderstatPlayersResponse.model_validate(load("understat-players.json"))


class FakeDatabankClient:
    def __init__(self, gw_rows):
        self._gw = gw_rows
        self.calls = []

    def fetch_gw(self, season, gw):
        self.calls.append((season, gw))
        return self._gw.get(gw, [])


def _db_row(element, gw):
    return {"element": element, "name": "x", "team": "T", "position": "P",
            "minutes": 90, "expected_goals": 0.5, "expected_assists": 0.2,
            "expected_goals_conceded": 1.4, "dc": 2, "saves": 1, "starts": 1,
            "bps": 20, "bonus": 0, "total_points": 5,
            "yellow_cards": 0, "red_cards": 0, "was_home": True, "value": 5.0}


def test_refresh_databank_populates_player_stats(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "databank": {"seasons": ["2025-26"]}}
    dbc = FakeDatabankClient({1: [_db_row(e.id, 1) for e in bs.elements[:5]]})
    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks),
                understat_client=FakeUnderstatClient(_understat_resp(load)),
                databank_client=dbc)
    n = conn.execute("SELECT COUNT(*) c FROM player_stats WHERE source='fpl_databank:2025-26'").fetchone()["c"]
    assert n == 5
    row = conn.execute("SELECT xg, dc, starts FROM player_stats LIMIT 1").fetchone()
    assert row["xg"] == 0.5 and row["dc"] == 2 and row["starts"] == 1
    conn.close()


def test_refresh_databank_failure_degrades_gracefully(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "databank": {"seasons": ["2025-26"]}}

    class BoomDatabankClient:
        def fetch_gw(self, season, gw):
            raise RuntimeError("databank down")

    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks),
                understat_client=FakeUnderstatClient(_understat_resp(load)),
                databank_client=BoomDatabankClient())
    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert "WARNING" in capsys.readouterr().out
    conn.close()


def test_remap_databank_elements_rematches_historical_ids(load):
    """Historical databank rows carry last season's element ids, which change every
    season. Rows must be re-pointed at current players by name+team, or dropped."""
    from src.cli import _remap_databank_elements

    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'Man City', 'MCI')")
    conn.execute("INSERT INTO players (id, name, web_name, team_id, position, price, status, "
                 "ownership, form) VALUES (5000, 'Erling Haaland', 'Haaland', 1, 'FWD', 15.0, 'a', 50.0, 8.0)")
    conn.commit()
    rows = [
        {"element": 999, "name": "Erling Haaland", "team": "Man City", "position": "FWD",
         "minutes": 90, "expected_goals": 1.4, "expected_assists": 0.5,
         "expected_goals_conceded": 0.35, "dc": 2, "saves": 0, "starts": 1, "bps": 35,
         "bonus": 3, "total_points": 13, "yellow_cards": 0, "red_cards": 0,
         "was_home": True, "value": 15.0},
        {"element": 888, "name": "Gone Player", "team": "Old Team", "position": "MID",
         "minutes": 0, "expected_goals": 0.0, "expected_assists": 0.0,
         "expected_goals_conceded": 0.0, "dc": 0, "saves": 0, "starts": 0, "bps": 0,
         "bonus": 0, "total_points": 0, "yellow_cards": 0, "red_cards": 0,
         "was_home": True, "value": 5.0},
    ]
    remapped, unmatched = _remap_databank_elements(conn, rows)
    assert unmatched == 1  # left the league; dropped, never mis-matched
    assert len(remapped) == 1 and remapped[0]["element"] == 5000


def test_remap_databank_elements_passthrough_current_ids(load):
    """Current-season rows (ids already matching) pass through untouched."""
    from src.cli import _remap_databank_elements

    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO players (id, name, web_name, team_id, position, price, status, "
                 "ownership, form) VALUES (5000, 'Erling Haaland', 'Haaland', 1, 'FWD', 15.0, 'a', 50.0, 8.0)")
    conn.commit()
    row = {"element": 5000, "name": "Erling Haaland", "team": "Man City", "position": "FWD",
           "minutes": 90, "expected_goals": 1.4, "expected_assists": 0.5,
           "expected_goals_conceded": 0.35, "dc": 2, "saves": 0, "starts": 1, "bps": 35,
           "bonus": 3, "total_points": 13, "yellow_cards": 0, "red_cards": 0,
           "was_home": True, "value": 15.0}
    remapped, unmatched = _remap_databank_elements(conn, [row])
    assert unmatched == 0 and remapped[0]["element"] == 5000


def test_refresh_populates_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=FakeUnderstatClient(_understat_resp(load)),
        sources=("fpl", "understat"),
    )
    n = conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert n == len(_understat_resp(load).players)
    matched = conn.execute(
        "SELECT COUNT(*) c FROM understat_players WHERE fpl_player_id IS NOT NULL"
    ).fetchone()["c"]
    assert matched >= int(0.95 * n)
    conn.close()


def test_refresh_understat_failure_degrades_gracefully(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=BoomUnderstatClient(),
        sources=("fpl", "understat"),
    )
    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    assert "WARNING" in capsys.readouterr().out
    conn.close()


def test_cli_refresh_default_sources_include_databank(monkeypatch):
    """The CLI refresh command must pull the databank by default (v0.12 wiring) —
    not just FPL + Understat, or the deployed manual/agent refresh skips ingestion."""
    import src.cli as cli

    captured = {}

    def fake_refresh(**kw):
        captured.update(kw)
        return {"fpl": None, "understat": None, "databank": None,
                "rematch": 0, "cleanup": {}, "warnings": []}

    monkeypatch.setattr(cli, "refresh", fake_refresh)
    cli.main(["refresh", "--json"])
    assert "databank" in captured["sources"]
    assert captured["sources"] == ("fpl", "understat", "databank")


def test_refresh_source_filter_fpl_only_skips_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    uc = FakeUnderstatClient(_understat_resp(load))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks),
                understat_client=uc, sources=("fpl",))
    assert uc.called is False
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    conn.close()


def test_refresh_my_team_writes_authed_row(db, monkeypatch, capsys):
    """refresh_my_team unlocks the master key, calls authed snapshot, prints summary."""
    from src.auth import master, session as auth_session
    from src.execution import executor
    from src import config as cfg_mod

    monkeypatch.setattr(master, "get_master_key", lambda: b"key")
    monkeypatch.setattr(auth_session, "ensure_session", lambda conn, key: object())
    monkeypatch.setattr(cfg_mod, "team_id", lambda: 12345)
    monkeypatch.setattr(executor, "fetch_my_team_authed",
                        lambda sess, entry: {"picks": [{"element": 1, "position": 1,
                                                        "is_captain": True, "is_vice_captain": False,
                                                        "selling_price": 50, "purchase_price": 50, "multiplier": 2}],
                                             "transfers": {"bank": 0, "value": 1000, "limit": 1},
                                             "chips": []})

    # Seed next_gw
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.commit()

    cli.refresh_my_team(conn=db)
    row = db.execute("SELECT free_transfers FROM my_team WHERE gw=38").fetchone()
    assert row is not None and row["free_transfers"] == 1

    out = capsys.readouterr().out
    assert "GW38" in out and "FT=1" in out


def test_refresh_my_team_surfaces_session_expired(db, monkeypatch, capsys):
    """If ensure_session raises, the command surfaces the error and exits non-zero."""
    from src.auth import master, session as auth_session

    monkeypatch.setattr(master, "get_master_key", lambda: b"key")

    class SessionExpired(Exception):
        pass

    monkeypatch.setattr(auth_session, "ensure_session",
                        lambda *a, **k: (_ for _ in ()).throw(SessionExpired("token bad")))

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.commit()

    with pytest.raises(SystemExit) as exc_info:
        cli.refresh_my_team(conn=db)
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "session" in err.lower() or "token" in err.lower()


def test_rematch_relinks_stale_season_rows(db):
    """Season rollover: prior-season understat rows must be re-linked to the new
    players table by name+team — old fpl_player_id pointers reference dead ids."""
    from src.cli import _rematch_prior_understat

    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'Newcastle', 'NEW')")
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, price, status, "
               "ownership, form) VALUES (449, 'Lewis Hall', 'Hall', 1, 'DEF', 5.0, 'a', 3.9, 0.0)")
    # Understat rows: one still pointing at an OLD id (449 was Bruno Fernandes last season),
    # one for a player who left the league (unmatchable), one for the CURRENT season.
    db.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name, "
               "team_title, xg_per_90, xa_per_90, minutes, games, goals, assists) "
               "VALUES (1001, 449, '2025', 'Lewis Hall', 'Newcastle United', 0.04, 0.08, 2700, 30, 1, 1)")
    db.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name, "
               "team_title, xg_per_90, xa_per_90, minutes, games, goals, assists) "
               "VALUES (1002, 999, '2025', 'Someone Else', 'Old Team', 0.1, 0.1, 900, 10, 0, 0)")
    db.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name, "
               "team_title, xg_per_90, xa_per_90, minutes, games, goals, assists) "
               "VALUES (1003, NULL, '2026', 'Fresh Player', 'Newcastle United', 0.1, 0.1, 0, 0, 0, 0)")
    db.commit()

    n = _rematch_prior_understat(db, "2026", {})
    assert n == 1
    row = db.execute("SELECT fpl_player_id FROM understat_players WHERE understat_id=1001").fetchone()
    assert row["fpl_player_id"] == 449
    # unmatchable player is nulled, not left dangling at a wrong id
    row = db.execute("SELECT fpl_player_id FROM understat_players WHERE understat_id=1002").fetchone()
    assert row["fpl_player_id"] is None
    # current-season rows are untouched
    row = db.execute("SELECT fpl_player_id FROM understat_players WHERE understat_id=1003").fetchone()
    assert row["fpl_player_id"] is None
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    db.commit()


def test_rematch_is_noop_when_no_stale_rows(db):
    from src.cli import _rematch_prior_understat

    db.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name, "
               "team_title, xg_per_90, xa_per_90, minutes, games, goals, assists) "
               "VALUES (1, NULL, '2026', 'X', 'Newcastle United', 0.1, 0.1, 0, 0, 0, 0)")
    db.commit()
    assert _rematch_prior_understat(db, "2026", {}) == 0


def test_refresh_rematches_prior_understat_after_rollover(load):
    """Full refresh on a rolled-over DB: stale 2025 links get re-pointed at the new players."""
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2026"}}
    # Seed a stale 2025 understat row pointing at an arbitrary old id.
    conn.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name, "
                 "team_title, xg_per_90, xa_per_90, minutes, games, goals, assists) "
                 "VALUES (999, 1, '2025', 'Lewis Hall', 'Newcastle United', 0.04, 0.08, 2700, 30, 1, 1)")
    conn.commit()

    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks), sources=("fpl",))

    # After refresh, the 2025 row must point at the REAL Lewis Hall in the new players table.
    row = conn.execute("""SELECT p.web_name, t.short_name AS team
                          FROM understat_players u JOIN players p ON p.id = u.fpl_player_id
                          JOIN teams t ON t.id = p.team_id
                          WHERE u.understat_id=999""").fetchone()
    assert row is not None and row["web_name"] == "Hall" and row["team"] == "NEW"
    conn.close()


def test_clear_stale_season_rows_clears_pre_season_data(db):
    """Rows written before the current season's GW1 deadline are last season's data
    (player ids change every season) — drop them; keep anything settled after."""
    from src.cli import _clear_stale_season_rows

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (1, '2026-08-21T17:30:00+00:00', 0, 0, 1)")
    db.execute("INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes, "
               "goals_scored, assists, clean_sheets, bonus, total_points, settled_at) "
               "VALUES (449, 38, 1, 90, 1, 1, 0, 2, 14, '2026-05-27T08:00:00+00:00')")
    db.execute("INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes, "
               "goals_scored, assists, clean_sheets, bonus, total_points, settled_at) "
               "VALUES (449, 1, 2, 90, 0, 0, 1, 0, 6, '2026-08-22T08:00:00+00:00')")
    db.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) "
               "VALUES (38, '[]', '2026-05-27T08:00:00+00:00')")
    db.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) "
               "VALUES (1, '[]', '2026-08-22T08:00:00+00:00')")
    db.commit()

    n_gw, n_team = _clear_stale_season_rows(db)
    assert (n_gw, n_team) == (1, 1)
    assert db.execute("SELECT COUNT(*) c FROM player_gw_stats").fetchone()["c"] == 1
    assert db.execute("SELECT gw FROM my_team").fetchall()[0]["gw"] == 1
