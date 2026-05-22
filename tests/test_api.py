import json
import pathlib
import pytest
from src.data.db import connect, init_db
from src.interface import queries

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def seed(conn):
    """Seed a realistic in-memory DB from the frozen fixtures + analytics passes."""
    from src.data.models import BootstrapStatic, EntryPicks, Fixture, UnderstatPlayersResponse
    from src.data import repository, name_resolver
    from src.analytics import fdr, xp

    bs = BootstrapStatic.model_validate(_load("bootstrap-static.json"))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
    # Force GW38 to be the unfinished "next" GW so FDR/xP deterministically produce rows.
    conn.execute("UPDATE gameweeks SET finished=0 WHERE id=38")
    repository.upsert_fixtures(conn, [Fixture.model_validate(f) for f in _load("fixtures.json")])
    repository.snapshot_my_team(conn, 37, EntryPicks.model_validate(_load("picks.json")))
    us = UnderstatPlayersResponse.model_validate(_load("understat-players.json")).players
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(conn, us, res, "2025")
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    conn.commit()


@pytest.fixture
def seeded():
    conn = connect(":memory:")
    init_db(conn)
    seed(conn)
    yield conn
    conn.close()


def test_get_status(seeded):
    s = queries.get_status(seeded)
    assert set(["current_gw", "next_gw", "deadline_utc", "mode", "banners"]) <= set(s)
    assert s["banners"] == []


def test_get_squad_15_with_xp(seeded):
    sq = queries.get_squad(seeded)
    assert len(sq["players"]) == 15
    p0 = sq["players"][0]
    assert set(["id", "web_name", "position", "team_short", "price", "status", "xp_next"]) <= set(p0)
    assert any(p["xp_next"] is not None for p in sq["players"])


def test_get_fixtures_planner(seeded):
    g = queries.get_fixtures_planner(seeded)
    assert len(g["horizon"]) == 5
    assert g["rows"]
    cells = [c for row in g["rows"] for c in row["cells"] if c]
    assert cells and all(1 <= c["fdr_attack"] <= 5 for c in cells)


def test_get_activity_empty(seeded):
    a = queries.get_activity(seeded)
    assert a == {"entries": []}


from fastapi.testclient import TestClient
import sqlite3 as _sqlite3


@pytest.fixture
def client():
    from src.interface.api import app
    from src.interface.deps import get_db
    from src.data.db import init_db

    conn = _sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = _sqlite3.Row
    init_db(conn)
    seed(conn)

    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert "mode" in r.json()


def test_squad_endpoint(client):
    r = client.get("/api/squad")
    assert r.status_code == 200
    assert len(r.json()["players"]) == 15


def test_planner_endpoint(client):
    r = client.get("/api/fixtures/planner")
    assert r.status_code == 200
    assert len(r.json()["horizon"]) == 5


def test_activity_endpoint(client):
    r = client.get("/api/activity")
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_stub_endpoints(client):
    assert client.get("/api/captain").json() == {"picks": [], "vice_player_id": None}
    t = client.get("/api/transfers").json()
    assert t["suggestions"] == [] and t["empty_reason"]
    assert client.get("/api/chips").json() == {"recommendation": None}


def test_cors_header(client):
    r = client.get("/api/status", headers={"origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
