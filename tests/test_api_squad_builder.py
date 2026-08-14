import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.ai import cache
from src.ai.squad import runner as squad_runner
from src.ai.squad.digest import build_squad_digest
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


def _result(player_id=1):
    return {"picks": [{"player_id": player_id, "slot": "GKP1", "reason": "r"}],
            "template_rationale": "T", "risks": [], "source": "ai"}


def test_builder_generates_and_enriches(client, monkeypatch):
    tc, conn = client
    from src.decisions.squad_builder import build_candidate_pool

    pool_pid = build_candidate_pool(conn)[0]["player_id"]
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg, conn=None: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: _result(pool_pid))
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "ai"
    assert body["picks"][0]["web_name"]  # enriched from the players table
    assert body["budget_used"] is not None


def test_builder_cached_hit_skips_generation(client, monkeypatch):
    tc, conn = client
    digest = build_squad_digest(conn)
    rec_hash = cache.recommendation_hash(digest)
    cache.put(conn, 38, "squad", rec_hash, json.dumps(_result(), sort_keys=True), "m")

    def _boom(*a, **kw):
        raise AssertionError("must not regenerate on cache hit")

    monkeypatch.setattr(squad_runner, "generate_squad", _boom)
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    assert r.json()["status"] == "cached"


def test_builder_unavailable_when_runner_fails(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr("src.ai.squad.runner.generate_squad", lambda *a, **kw: None)
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"
