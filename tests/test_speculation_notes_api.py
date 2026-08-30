"""v0.26: speculation notes API (dashboard form backend)."""
import pytest
from fastapi.testclient import TestClient

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


def test_notes_empty_list(client):
    tc, _ = client
    r = tc.get("/api/speculation/notes")
    assert r.status_code == 200
    assert r.json() == {"notes": []}


def test_notes_post_get_delete(client):
    tc, conn = client
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    conn.commit()
    r = tc.post("/api/speculation/notes",
                json={"note": "xabi alonso is pretty good", "team_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["note"]["team_short"] == "CHE"
    nid = body["note"]["id"]

    r = tc.get("/api/speculation/notes")
    assert [n["id"] for n in r.json()["notes"]] == [nid]

    r = tc.delete(f"/api/speculation/notes/{nid}")
    assert r.status_code == 200
    assert tc.get("/api/speculation/notes").json() == {"notes": []}


def test_notes_post_empty_note_rejected(client):
    tc, _ = client
    r = tc.post("/api/speculation/notes", json={"note": "   "})
    assert r.status_code == 400


def test_notes_delete_unknown_404(client):
    tc, _ = client
    r = tc.delete("/api/speculation/notes/9999")
    assert r.status_code == 404


def test_notes_activity_logged(client):
    tc, conn = client
    tc.post("/api/speculation/notes", json={"note": "rogers takes long shots"})
    rows = conn.execute("SELECT decision_type, action_taken FROM activity_log").fetchall()
    assert rows and rows[0]["decision_type"] == "speculation"
    assert "rogers takes long shots" in rows[0]["action_taken"]


def test_teams_and_players_endpoints(client):
    tc, conn = client
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    conn.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
                 "VALUES (7,'Morgan Rogers','Rogers',1,'MID','a')")
    conn.commit()
    r = tc.get("/api/speculation/teams")
    assert [t["short_name"] for t in r.json()["teams"]] == ["CHE"]
    r = tc.get("/api/speculation/players?team_id=1")
    assert [p["web_name"] for p in r.json()["players"]] == ["Rogers"]
