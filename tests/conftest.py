import json
import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def load():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())
    return _load


@pytest.fixture
def db():
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()
