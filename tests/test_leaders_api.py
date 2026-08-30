"""v0.27: GET /api/leaders — cohort + patterns payload."""
import pytest
from fastapi.testclient import TestClient

from src.data import repository
from src.data.db import connect, init_db
from src.interface import api
from src.interface.deps import get_db


@pytest.fixture
def client():
    conn = connect(":memory:", check_same_thread=False)
    init_db(conn)

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


def test_leaders_empty(client):
    tc, _ = client
    r = tc.get("/api/leaders")
    assert r.status_code == 200
    body = r.json()
    assert body["cohort"] == [] and body["patterns"]["chip_timing"]["rows"] == []


def test_leaders_with_seeded_snapshots(client):
    tc, conn = client
    repository.upsert_leader_entry(conn, 1, "P1", "E1", past_rank=100, past_pts=2000,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_snapshot(conn, 1, 1, 91, 227, 10937, 0, 1000,
                                      0, 0, "3xc")
    r = tc.get("/api/leaders")
    body = r.json()
    assert body["cohort"][0]["entry_id"] == 1
    assert body["cohort"][0]["chips_used"] == ["3xc"]
    assert body["patterns"]["chip_timing"]["rows"] == [
        {"chip": "3xc", "gw": 1, "count": 1}]
