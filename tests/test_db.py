def test_init_db_creates_all_tables(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected = {
        "players", "teams", "player_stats", "fixtures", "fdr", "xp",
        "my_team", "gameweeks", "activity_log", "credentials", "cache_meta",
    }
    assert expected <= tables


def test_gameweeks_state_defaults_to_pending(db):
    db.execute("INSERT INTO gameweeks (id, name) VALUES (1, 'Gameweek 1')")
    row = db.execute("SELECT state FROM gameweeks WHERE id=1").fetchone()
    assert row["state"] == "PENDING"


import sqlite3
from src.data import db


def test_migrate_credentials_adds_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # a credentials table created BEFORE the new columns existed
    conn.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY, session_last_refreshed TIMESTAMP)")
    db._migrate_credentials(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    assert "auth_state" in cols
    assert "relogin_failures" in cols
    # idempotent: a second run is a no-op
    db._migrate_credentials(conn)
    cols_again = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    assert cols_again == cols


def test_migrate_credentials_adds_token_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY, session_last_refreshed TIMESTAMP)")
    db._migrate_credentials(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    for c in ("refresh_token_encrypted", "access_token_encrypted", "access_token_expires_at"):
        assert c in cols
    db._migrate_credentials(conn)  # idempotent
