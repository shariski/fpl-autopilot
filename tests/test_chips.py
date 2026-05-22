from src.data.db import connect, init_db
from src.analytics import dgw


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_team_fixture_count_single_double_blank():
    conn = _db()
    conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES "
                 "(1,5,1,2,0),(2,6,1,3,0),(3,6,4,1,0)")
    conn.commit()
    assert dgw.team_fixture_count(conn, 1, 5) == 1
    assert dgw.team_fixture_count(conn, 1, 6) == 2  # double
    assert dgw.team_fixture_count(conn, 1, 7) == 0  # blank


def test_team_gw_fdr():
    conn = _db()
    conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (1,5,2,3,'t')")
    conn.commit()
    fd = dgw.team_gw_fdr(conn, 1, 5)
    assert fd["fdr_attack"] == 2 and fd["fdr_defense"] == 3
    assert dgw.team_gw_fdr(conn, 1, 9) is None
