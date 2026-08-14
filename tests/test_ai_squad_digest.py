import json
import pathlib

from src.ai.squad.digest import build_squad_digest
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
    conn.commit()
    return conn


def test_digest_shape():
    conn = _seed()
    d = build_squad_digest(conn)
    assert d["next_gw"] == 38 and d["budget"] == 100
    assert 20 <= len(d["players"]) <= 200
    p0 = d["players"][0]
    assert set(p0.keys()) == {"player_id", "web_name", "team", "position", "price",
                              "xp_next", "xp_6gw", "xg90", "xa90", "ownership_pct",
                              "form", "fixtures_3"}
    assert 0 < len(p0["fixtures_3"]) <= 3
    f0 = p0["fixtures_3"][0]
    assert set(f0.keys()) == {"opponent", "venue", "fdr_attack", "fdr_defense"}
    conn.close()
