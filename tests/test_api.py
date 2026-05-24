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


@pytest.fixture
def client():
    from src.interface.api import app
    from src.interface.deps import get_db
    from src.data.db import connect, init_db

    conn = connect(":memory:", check_same_thread=False)
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


def test_captain_endpoint_wired(client):
    body = client.get("/api/captain").json()
    assert "picks" in body and "vice_player_id" in body
    assert len(body["picks"]) <= 5


def test_transfers_endpoint_wired(client):
    body = client.get("/api/transfers").json()
    assert "suggestions" in body and "empty_reason" in body
    assert isinstance(body["suggestions"], list)


def test_cors_header(client):
    r = client.get("/api/status", headers={"origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_chips_endpoint_wired(client):
    r = client.get("/api/chips")
    assert r.status_code == 200
    assert "recommendation" in r.json()  # real recommender output (null on the seeded single-GW data)


def test_status_has_frozen_false_by_default(seeded):
    s = queries.get_status(seeded)
    assert s["frozen"] is False


def test_status_frozen_banner(seeded):
    from src.execution import override
    override.freeze(seeded, reason="boom", source="user")
    s = queries.get_status(seeded)
    assert s["frozen"] is True
    assert any(b["level"] == "error" and "boom" in b["text"] for b in s["banners"])


def test_status_warning_window_banner(seeded, monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(queries, "load_config",
                        lambda: {"mode": {"current": "manual"},
                                 "deadguard": {"enabled": True, "warning_window_minutes": 120}})
    soon = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    seeded.execute("UPDATE gameweeks SET deadline_utc=?, state='PENDING' WHERE is_next=1", (soon,))
    seeded.commit()
    s = queries.get_status(seeded)
    warn = [b for b in s["banners"] if b["level"] == "warning"]
    assert warn and warn[0]["action"] == {"label": "Keep as is", "endpoint": "/api/deadguard/keep"}


def test_status_executed_banner(seeded):
    seeded.execute("UPDATE gameweeks SET state='DEADGUARD_EXECUTED' WHERE is_next=1")
    seeded.commit()
    s = queries.get_status(seeded)
    assert any(b["level"] == "info" for b in s["banners"])


def test_status_frozen_suppresses_warning(seeded, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from src.execution import override
    monkeypatch.setattr(queries, "load_config",
                        lambda: {"mode": {"current": "manual"},
                                 "deadguard": {"enabled": True, "warning_window_minutes": 120}})
    soon = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    seeded.execute("UPDATE gameweeks SET deadline_utc=?, state='PENDING' WHERE is_next=1", (soon,))
    seeded.commit()
    override.freeze(seeded, reason="x", source="user")
    s = queries.get_status(seeded)
    assert s["frozen"] is True
    assert not any(b["level"] == "warning" for b in s["banners"])
    assert any(b["level"] == "error" for b in s["banners"])
