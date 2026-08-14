import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.ai import cache
from src.ai.insight.digest import build_player_digest
from src.data.db import connect, init_db
from src.interface import api
from src.interface.deps import get_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def _seed(conn):
    from src.data.models import BootstrapStatic, EntryPicks, Fixture, UnderstatPlayersResponse
    from src.data import repository, name_resolver
    from src.analytics import fdr, xp

    bs = BootstrapStatic.model_validate(_load("bootstrap-static.json"))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
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
def client():
    conn = connect(":memory:", check_same_thread=False)
    init_db(conn)
    _seed(conn)

    def override_get_db():
        try:
            yield conn
        finally:
            pass

    app = api.app
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def _payload():
    return {
        "insights": [{"category": "value_market", "claim": "Owned by 50.0 at price 15.0",
                      "evidence_used": ["50.0", "15.0"], "confidence": "medium",
                      "implication": "n/a"}],
        "summary": "Verdict summary.",
        "data_limits": ["no current-season minutes yet (pre-season)"],
    }


def test_insight_cached_hit_skips_generation(client, monkeypatch):
    tc, conn = client
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    digest = build_player_digest(conn, pid)
    rec_hash = cache.recommendation_hash(digest)
    cache.put(conn, 38, "insight", rec_hash, json.dumps(_payload(), sort_keys=True), "m")

    def _boom(*a, **kw):
        raise AssertionError("generation must not run on a cache hit")

    monkeypatch.setattr("src.ai.insight.runner.generate_player_insight", _boom)
    r = tc.get(f"/api/players/{pid}/insight")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cached"
    assert body["insights"][0]["claim"] == "Owned by 50.0 at price 15.0"
    assert body["gw"] == 38


def test_insight_generates_on_miss(client, monkeypatch):
    tc, conn = client
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]

    def _fake(conn_, player_id, *, provider, model_id, **kw):
        return _payload()

    monkeypatch.setattr("src.ai.insight.runner.generate_player_insight", _fake)
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg, conn=None: object())
    r = tc.get(f"/api/players/{pid}/insight")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "generated"
    assert body["summary"] == "Verdict summary."


def test_insight_unknown_player_404(client):
    tc, _ = client
    r = tc.get("/api/players/999999/insight")
    assert r.status_code == 404


def test_insight_unavailable_when_runner_fails(client, monkeypatch):
    tc, conn = client
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    monkeypatch.setattr("src.ai.insight.runner.generate_player_insight",
                        lambda *a, **kw: None)
    r = tc.get(f"/api/players/{pid}/insight")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"


def test_insight_ai_disabled(client, monkeypatch):
    tc, conn = client
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    monkeypatch.setattr("src.config.ai_enabled", lambda cfg=None: False)
    r = tc.get(f"/api/players/{pid}/insight")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"
    assert r.json()["reason"] == "ai_disabled"
