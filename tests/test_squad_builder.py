import json
import pathlib

from src.decisions.squad_builder import build_candidate_pool
from src.data.db import connect, init_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _seed():
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


def test_pool_shape_and_spread():
    conn = _seed()
    pool = build_candidate_pool(conn)
    assert 20 <= len(pool) <= 200
    by_pos = {}
    for p in pool:
        assert set(p.keys()) == {"player_id", "web_name", "team_short", "position",
                                 "price", "status", "xp_next", "xp_6gw", "value",
                                 "ownership_pct", "form", "transfers_in",
                                 "transfers_out", "net_momentum"}
        by_pos.setdefault(p["position"], 0)
        by_pos[p["position"]] += 1
    for pos in ("GKP", "DEF", "MID", "FWD"):
        assert by_pos.get(pos, 0) >= 8, f"position {pos} underrepresented"
    # value tier present: some cheap-ish (< 6.0) players survive the top-xp filter
    assert any(p["price"] < 6.0 for p in pool)
    # sorted by xp_6gw desc
    xps = [p["xp_6gw"] for p in pool]
    assert xps == sorted(xps, reverse=True)
    conn.close()


def test_pool_excludes_injured_and_no_xp():
    conn = _seed()
    conn.execute("UPDATE players SET status='i' WHERE position='GKP'")
    conn.commit()
    pool = build_candidate_pool(conn)
    assert all(p["status"] in ("a", "d") for p in pool)
    conn.close()


def test_pool_empty_without_next_gw():
    conn = _seed()
    conn.execute("UPDATE gameweeks SET finished=1")
    conn.commit()
    assert build_candidate_pool(conn) == []
    conn.close()
