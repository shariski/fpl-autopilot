import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.data.db import connect, init_db
from src.interface import api
from src.interface.deps import get_db


@pytest.fixture
def client(tmp_path):
    conn = connect(str(tmp_path / "api.db"), check_same_thread=False)
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


def test_api_mode_defaults_to_config(client):
    tc, _ = client
    r = tc.get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body == {"mode": "manual", "source": "config", "config_value": "manual"}


def test_api_mode_set_and_get(client):
    tc, conn = client
    r = tc.post("/api/mode", json={"mode": "hybrid"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"mode": "hybrid", "source": "override", "config_value": "manual"}
    row = conn.execute("SELECT value FROM system_state WHERE key='mode'").fetchone()
    assert json.loads(row["value"])["mode"] == "hybrid"
    # persists across reads
    assert tc.get("/api/mode").json()["mode"] == "hybrid"


def test_api_mode_rejects_invalid(client):
    tc, _ = client
    r = tc.post("/api/mode", json={"mode": "banana"})
    assert r.status_code == 400
