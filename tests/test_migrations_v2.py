"""Migration tests: an existing pre-v0.12 database must gain the v2 columns on init_db."""
from src.data.db import connect, init_db

OLD_SCHEMA = """
CREATE TABLE players (
  id INTEGER PRIMARY KEY,
  name TEXT, web_name TEXT, team_id INTEGER, position TEXT, price REAL,
  status TEXT, ownership REAL, form REAL, updated_at TIMESTAMP
);
CREATE TABLE player_stats (
  player_id INTEGER, gw INTEGER, source TEXT, minutes INTEGER,
  goals INTEGER, assists INTEGER, xg REAL, xa REAL, bonus INTEGER, total_points INTEGER,
  PRIMARY KEY (player_id, gw, source)
);
CREATE TABLE fdr (
  team_id INTEGER, gw INTEGER, fdr_attack INTEGER, fdr_defense INTEGER,
  computed_at TIMESTAMP, PRIMARY KEY (team_id, gw)
);
CREATE TABLE xp (
  player_id INTEGER, gw INTEGER, model_version TEXT, xp REAL, xminutes REAL,
  xgoals REAL, xassists REAL, xcs REAL, computed_at TIMESTAMP,
  PRIMARY KEY (player_id, gw, model_version)
);
"""


def _old_db():
    conn = connect(":memory:")
    conn.executescript(OLD_SCHEMA)
    return conn


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_init_db_adds_v2_columns_to_old_db():
    conn = _old_db()
    init_db(conn)
    assert {"xgc", "dc", "saves", "starts", "bps", "was_home", "value"} <= _cols(conn, "player_stats")
    assert {"fdr_attack_mult", "fdr_defense_mult"} <= _cols(conn, "fdr")
    assert {"p_start", "xbonus", "xdc", "xcs_lambda"} <= _cols(conn, "xp")
    assert "chance_of_playing" in _cols(conn, "players")
    conn.close()


def test_init_db_idempotent_on_old_db():
    conn = _old_db()
    init_db(conn)
    init_db(conn)  # second run must not raise
    assert {"xgc", "dc"} <= _cols(conn, "player_stats")
    conn.close()


def test_fresh_db_has_v2_columns():
    conn = connect(":memory:")
    init_db(conn)
    assert {"xgc", "dc", "saves", "starts", "bps", "was_home", "value"} <= _cols(conn, "player_stats")
    assert {"fdr_attack_mult", "fdr_defense_mult"} <= _cols(conn, "fdr")
    assert {"p_start", "xbonus", "xdc", "xcs_lambda"} <= _cols(conn, "xp")
    assert "chance_of_playing" in _cols(conn, "players")
    conn.close()
