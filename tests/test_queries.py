import json
import pytest
from src.interface import queries


def test_get_squad_prefers_authed_row_over_public(db):
    """Two my_team rows present (public at gw=37, authed at gw=38) — get_squad returns the authed."""
    # Insert public row at gw=37 (last_finished)
    pick1 = {"element": 1, "position": 1, "multiplier": 1, "is_captain": False, "is_vice_captain": False}
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (37, ?, 0.0, 100.0, NULL, 't')", (json.dumps([pick1]),))
    # Insert authed row at gw=38 (next_gw)
    pick2 = {"element": 2, "position": 1, "multiplier": 1, "is_captain": False, "is_vice_captain": False}
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (38, ?, 0.5, 100.5, 2, 't')", (json.dumps([pick2]),))
    # Add player 2
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (2, 'Star', 3, 1, 5.0, 'a')")
    db.execute("INSERT INTO teams (id, short_name) VALUES (1, 'ARS')")
    db.commit()

    result = queries.get_squad(db)
    assert result["gw"] == 38, f"Expected gw=38, got {result['gw']}"
    assert result["free_transfers"] == 2, f"Expected free_transfers=2, got {result['free_transfers']}"
    assert any(p["id"] == 2 for p in result["players"]), "Expected player 2 in squad"


def test_get_squad_falls_back_to_public_when_only_public(db):
    """Only a public row present -> get_squad returns it with free_transfers=None."""
    # Insert only public row at gw=37
    pick1 = {"element": 1, "position": 1, "multiplier": 1, "is_captain": False, "is_vice_captain": False}
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (37, ?, 0.0, 100.0, NULL, 't')", (json.dumps([pick1]),))
    # Add player 1
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (1, 'Solo', 3, 1, 5.0, 'a')")
    db.execute("INSERT INTO teams (id, short_name) VALUES (1, 'ARS')")
    db.commit()

    result = queries.get_squad(db)
    assert result["gw"] == 37, f"Expected gw=37, got {result['gw']}"
    assert result["free_transfers"] is None, f"Expected free_transfers=None, got {result['free_transfers']}"
    assert any(p["id"] == 1 for p in result["players"]), "Expected player 1 in squad"
