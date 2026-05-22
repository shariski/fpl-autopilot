from src.config import db_path
from src.data.db import connect, init_db


def get_db():
    conn = connect(db_path(), check_same_thread=False)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()
