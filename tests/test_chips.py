from src.data.db import connect, init_db
from src.analytics import dgw
from src.decisions import chips


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


def _seed_squad(conn, picks, chips_used=None):
    import json
    pj = json.dumps([{"element": e, "position": pos, "multiplier": 1,
                      "is_captain": False, "is_vice_captain": False} for e, pos in picks])
    cj = json.dumps(chips_used or [])
    conn.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, chips_used_json, snapshot_at) "
                 "VALUES (5, ?, 0.0, 100.0, 1, ?, 't')", (pj, cj))
    conn.commit()


def _player(conn, pid, team, position="MID", price=6.0, status="a", xg90=0.5, xa90=0.2, minutes=2700, games=30):
    conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status) VALUES (?,?,?,?,?,?)",
                 (pid, f"P{pid}", team, position, price, status))
    conn.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, minutes, games, "
                 "xg, xa, npg, npxg, xg_per_90, xa_per_90, updated_at) "
                 "VALUES (?,?,?,?,?,0,0,0,0,?,?,'t')",
                 (str(pid), pid, "2025", minutes, games, xg90, xa90))


def _gw6_double_for_all(conn, teams):
    fid = 100
    for t in teams:
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,6,?,?,0)",
                     (fid, t, 99)); fid += 1
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,6,?,?,0)",
                     (fid, 99, t)); fid += 1
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,6,1,1,'t')", (t,))
    conn.execute("INSERT OR IGNORE INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    conn.commit()


def test_free_hit_triggers_on_blank():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (1,6,1,99,0)")
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.free_hit_trigger(conn, squad, [6]) is not None


def test_bench_boost_triggers_on_dgw():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i, position="MID", xg90=0.6, xa90=0.3)
    _gw6_double_for_all(conn, list(range(1, 16)))
    _seed_squad(conn, picks)
    _, squad, _ = chips._squad(conn)
    assert chips.bench_boost_trigger(conn, squad, [6]) is not None


def test_triple_captain_triggers_for_premium_dgw():
    conn = _db()
    picks = [(1, 1)] + [(i, i) for i in range(2, 16)]
    _player(conn, 1, 1, position="FWD", price=14.0, xg90=1.0, xa90=0.3)
    for i in range(2, 16):
        _player(conn, i, i)
    _gw6_double_for_all(conn, [1])
    conn.execute("INSERT OR IGNORE INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.triple_captain_trigger(conn, squad, [6]) is not None


def test_wildcard_fixture_swing():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    for t in (1, 2, 3):
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,5,1,3,'t')", (t,))
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,8,4,3,'t')", (t,))
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5,'GW5',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.wildcard_trigger(conn, squad, 5) is not None


def test_recommend_chip_priority_and_chips_used():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i, xg90=0.6, xa90=0.3)
    _gw6_double_for_all(conn, list(range(1, 16)))
    _seed_squad(conn, picks)
    rec = chips.recommend_chip(conn)["recommendation"]
    assert rec is not None and rec["chip"] == "bench_boost"
    conn.execute("UPDATE my_team SET chips_used_json=? WHERE gw=5", ('["bboost"]',))
    conn.commit()
    assert chips.recommend_chip(conn)["recommendation"] is None


def test_recommend_chip_none_when_nothing_triggers():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    fid = 200
    for i in range(1, 16):
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,5,?,99,0)", (fid, i)); fid += 1
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,5,3,3,'t')", (i,))
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5,'GW5',0)")
    _seed_squad(conn, picks)
    conn.commit()
    assert chips.recommend_chip(conn)["recommendation"] is None


def test_recommend_chip_triple_captain_beats_bench_boost():
    conn = _db()
    # Premium player (id 1) eligible for TC; whole-squad DGW makes BB eligible too. TC must win (priority).
    picks = [(1, 1)] + [(i, i) for i in range(2, 16)]
    _player(conn, 1, 1, position="FWD", price=14.0, xg90=1.0, xa90=0.3)
    for i in range(2, 16):
        _player(conn, i, i, xg90=0.6, xa90=0.3)
    _gw6_double_for_all(conn, list(range(1, 16)))
    _seed_squad(conn, picks)
    rec = chips.recommend_chip(conn)["recommendation"]
    assert rec is not None and rec["chip"] == "triple_captain"
