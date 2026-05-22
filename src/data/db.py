import sqlite3
import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def connect(db_path, check_same_thread=True):
    # check_same_thread=False is needed for the FastAPI server: sync deps and routes
    # may run on different threadpool threads within one request.
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
