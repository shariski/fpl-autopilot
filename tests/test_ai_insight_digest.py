import json
import pathlib

from src.ai.insight.digest import build_player_digest
from src.data.db import connect, init_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _seed():
    """Minimal deterministic seed: one player, team, gameweek, understat, fdr, xp."""
    from src.data import repository, name_resolver
    from src.data.models import BootstrapStatic, Fixture, UnderstatPlayersResponse
    from src.analytics import fdr, xp

    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(json.loads((FIX / "bootstrap-static.json").read_text()))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
    conn.execute("UPDATE gameweeks SET finished=0 WHERE id=38")
    repository.upsert_fixtures(conn, [Fixture.model_validate(f) for f in json.loads((FIX / "fixtures.json").read_text())])
    us = UnderstatPlayersResponse.model_validate(json.loads((FIX / "understat-players.json").read_text())).players
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(conn, us, res, "2025")
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    conn.execute(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at) "
        "SELECT player_id, gw, 'v2', xp, xminutes, xgoals, xassists, xcs, computed_at "
        "FROM xp WHERE model_version='v1'")
    conn.commit()
    conn.commit()
    return conn


def test_digest_unknown_player_returns_none():
    conn = _seed()
    assert build_player_digest(conn, 999999) is None
    conn.close()


def test_digest_pre_season_shape():
    conn = _seed()
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    d = build_player_digest(conn, pid)
    assert d is not None
    assert set(d.keys()) == {"player", "prior_season", "current_season_gws",
                             "projection", "fixtures", "data_limits"}
    p = d["player"]
    assert set(p.keys()) == {"web_name", "position", "team", "price", "status",
                             "ownership_pct", "form"}
    # pre-season: no player_gw_stats rows yet
    assert d["current_season_gws"] == []
    assert any("no current-season" in lim for lim in d["data_limits"])
    # projection rows for the next 6 GWs
    assert 0 < len(d["projection"]) <= 6
    assert {"gw", "xp"} <= set(d["projection"][0].keys())
    # fixtures resolve opponent + venue + fdr
    assert 0 < len(d["fixtures"]) <= 6
    f0 = d["fixtures"][0]
    assert set(f0.keys()) == {"gw", "opponent", "venue", "fdr_attack", "fdr_defense"}
    assert f0["venue"] in ("H", "A")
    assert 1 <= f0["fdr_attack"] <= 5
    conn.close()


def test_digest_mid_season_includes_gw_stats():
    conn = _seed()
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes, "
                 "goals_scored, assists, clean_sheets, bonus, total_points, settled_at) "
                 "VALUES (?, 37, 1, 90, 1, 0, 1, 2, 9, '2026-05-20T00:00:00Z')", (pid,))
    conn.commit()
    d = build_player_digest(conn, pid)
    assert len(d["current_season_gws"]) == 1
    g = d["current_season_gws"][0]
    assert g == {"gw": 37, "minutes": 90, "goals": 1, "assists": 0, "total_points": 9}
    assert not any("no current-season" in lim for lim in d["data_limits"])
    conn.close()


def test_digest_understat_missing_flagged_in_limits():
    conn = _seed()
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    conn.execute("DELETE FROM understat_players WHERE fpl_player_id=?", (pid,))
    conn.commit()
    d = build_player_digest(conn, pid)
    assert any("no understat" in lim for lim in d["data_limits"])
    assert d["prior_season"] is None
    conn.close()
