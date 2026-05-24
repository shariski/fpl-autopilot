import sqlite3
import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def connect(db_path, check_same_thread=True):
    # check_same_thread=False is needed for the FastAPI server: sync deps and routes
    # may run on different threadpool threads within one request.
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_credentials(conn):
    """Add auth_state / relogin_failures to an existing credentials table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    if "auth_state" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN auth_state TEXT DEFAULT 'active'")
    if "relogin_failures" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN relogin_failures INTEGER DEFAULT 0")
    if "refresh_token_encrypted" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN refresh_token_encrypted BLOB")
    if "access_token_encrypted" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN access_token_encrypted BLOB")
    if "access_token_expires_at" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN access_token_expires_at TEXT")


def _migrate_gameweeks(conn):
    """Add the deadguard tracking columns to an existing gameweeks table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(gameweeks)")}
    if "deadguard_warned_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_warned_at TIMESTAMP")
    if "deadguard_reeval_alerted_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_reeval_alerted_at TIMESTAMP")
    if "deadguard_transfer_json" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_transfer_json TEXT")
    if "deadguard_transfer_undone_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_transfer_undone_at TIMESTAMP")


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate_credentials(conn)
    _migrate_gameweeks(conn)
    conn.commit()
