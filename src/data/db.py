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


def _migrate_players(conn):
    """Add the market-momentum columns to an existing players table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
    if "transfers_in" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN transfers_in INTEGER")
    if "transfers_out" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN transfers_out INTEGER")
    if "chance_of_playing" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN chance_of_playing REAL")


def _migrate_player_stats(conn):
    """v0.12/v0.13: databank columns on player_stats (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player_stats)")}
    for name, decl in [("xgc", "REAL"), ("dc", "INTEGER"), ("saves", "INTEGER"),
                       ("starts", "INTEGER"), ("bps", "INTEGER"),
                       ("yellow_cards", "INTEGER"), ("red_cards", "INTEGER"),
                       ("was_home", "BOOLEAN"), ("value", "REAL")]:
        if name not in cols:
            conn.execute(f"ALTER TABLE player_stats ADD COLUMN {name} {decl}")


def _migrate_player_gw_stats(conn):
    """v0.23: full per-GW stat capture on player_gw_stats (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player_gw_stats)")}
    for name, decl in [("starts", "INTEGER"), ("saves", "INTEGER"), ("bps", "INTEGER"),
                       ("expected_goals", "REAL"), ("expected_assists", "REAL"),
                       ("expected_goals_conceded", "REAL"),
                       ("defensive_contribution", "INTEGER"),
                       ("yellow_cards", "INTEGER"), ("red_cards", "INTEGER")]:
        if name not in cols:
            conn.execute(f"ALTER TABLE player_gw_stats ADD COLUMN {name} {decl}")


def _migrate_fdr(conn):
    """v0.12: continuous FDR v2 multipliers on fdr (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(fdr)")}
    for name in ("fdr_attack_mult", "fdr_defense_mult"):
        if name not in cols:
            conn.execute(f"ALTER TABLE fdr ADD COLUMN {name} REAL")


def _migrate_xp(conn):
    """v0.13: xP v2 component columns on xp (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(xp)")}
    for name in ("p_start", "xbonus", "xdc", "xcs_lambda"):
        if name not in cols:
            conn.execute(f"ALTER TABLE xp ADD COLUMN {name} REAL")


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate_credentials(conn)
    _migrate_gameweeks(conn)
    _migrate_players(conn)
    _migrate_player_stats(conn)
    _migrate_player_gw_stats(conn)
    _migrate_fdr(conn)
    _migrate_xp(conn)
    conn.commit()
