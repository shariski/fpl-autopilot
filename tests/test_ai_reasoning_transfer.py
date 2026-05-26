import json

from src.data.db import connect, init_db
from src.ai import reasoning


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _seed_fixtures(conn):
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30:00Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (39, 'GW39', '2026-06-09T18:30:00Z', 0, 0, 0, 'PENDING')")
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (40, 'GW40', '2026-06-16T18:30:00Z', 0, 0, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), "
                 "(2, 'Brentford', 'BRE'), (3, 'Liverpool', 'LIV'), (4, 'Aston Villa', 'AVL')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), (20, 'Watkins', 'FWD', 4, 9.0, 'd')")
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0), "
                 "(2, 39, 3, 1, '2026-06-09T19:00Z', 0), "
                 "(3, 40, 1, 4, '2026-06-16T19:00Z', 0), "
                 "(4, 38, 3, 4, '2026-06-02T17:00Z', 0), "
                 "(5, 39, 4, 2, '2026-06-09T17:00Z', 0), "
                 "(6, 40, 3, 4, '2026-06-16T17:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES "
                 "(1, 38, 2, 2, '2026-05-19T00:00Z'), (1, 39, 5, 5, '2026-05-19T00:00Z'), "
                 "(1, 40, 2, 2, '2026-05-19T00:00Z'), (4, 38, 5, 5, '2026-05-19T00:00Z'), "
                 "(4, 39, 4, 4, '2026-05-19T00:00Z'), (4, 40, 5, 5, '2026-05-19T00:00Z')")
    conn.commit()


TRANSFER_DECISION_FIXTURE = {
    "suggestions": [
        {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
         "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
         "ep_delta_5gw": 3.45, "hit_cost": 0, "confidence": 78},
        {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
         "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
         "ep_delta_5gw": 2.0,  "hit_cost": 0, "confidence": 65},
    ],
    "empty_reason": None,
    "free_transfers": 1,
}


def test_status_for_returns_player_status():
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._status_for(conn, 10) == "a"
    assert reasoning._status_for(conn, 20) == "d"


def test_status_for_returns_a_when_player_missing():
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._status_for(conn, 99999) == "a"


def test_fixtures_for_returns_next_n_gws():
    conn = _db(); _seed_fixtures(conn)
    fixtures = reasoning._fixtures_for(conn, player_id=10, next_gw=38, horizon=3)
    assert len(fixtures) == 3
    assert fixtures[0] == {"opponent": "BRE", "home": True, "fdr_attack": 2}
    assert fixtures[1] == {"opponent": "LIV", "home": False, "fdr_attack": 5}
    assert fixtures[2] == {"opponent": "AVL", "home": True, "fdr_attack": 2}


def test_fixtures_for_handles_blank_gameweek():
    conn = _db(); _seed_fixtures(conn)
    conn.execute("DELETE FROM fixtures WHERE id=2")
    conn.commit()
    fixtures = reasoning._fixtures_for(conn, player_id=10, next_gw=38, horizon=3)
    assert len(fixtures) == 2
    assert all(f["opponent"] != "LIV" for f in fixtures)


def test_build_transfer_payload_shape():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    assert payload is not None
    assert payload["out"]["web_name"] == "Watkins"
    assert payload["out"]["status"] == "d"
    assert payload["out"]["price"] == 9.0
    assert len(payload["out"]["fixtures_3gw"]) == 3
    assert payload["in"]["web_name"] == "Haaland"
    assert payload["in"]["status"] == "a"
    assert payload["ep_delta_5gw"] == 3.5
    assert payload["hit_cost"] == 0
    assert payload["confidence"] == 78
    assert payload["free_transfers"] == 1


def test_build_transfer_payload_returns_none_on_empty_suggestions():
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._build_transfer_payload(
        conn, {"suggestions": [], "empty_reason": "none", "free_transfers": 1}) is None


def test_build_transfer_payload_returns_none_when_no_next_gw():
    conn = _db()  # no gameweeks seeded
    assert reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE) is None


def test_build_transfer_prompt_includes_payload_and_examples():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    prompt = reasoning._build_transfer_prompt(payload)
    assert "Watkins" in prompt
    assert "Saka" in prompt
    assert "Isak" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "Do not invent" in prompt
