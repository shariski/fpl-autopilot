import pytest

from src.decisions import optimal_xi as opt


# 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD. Enough to fill any valid XI.
def _seed_basics(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE")])
    players = [
        (1, "GK1", "GKP", 1),
        (2, "GK2", "GKP", 1),
        (3, "D1", "DEF", 1), (4, "D2", "DEF", 1), (5, "D3", "DEF", 1),
        (6, "D4", "DEF", 1), (7, "D5", "DEF", 1),
        (8, "M1", "MID", 1), (9, "M2", "MID", 1), (10, "M3", "MID", 1),
        (11, "M4", "MID", 1), (12, "M5", "MID", 1),
        (13, "F1", "FWD", 1), (14, "F2", "FWD", 1), (15, "F3", "FWD", 1),
    ]
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   players)
    # xp v2 for the upcoming GW — give each player a distinct xP
    xp_values = {
        1: 3.0, 2: 2.5,
        3: 4.5, 4: 4.0, 5: 3.5, 6: 2.0, 7: 1.0,
        8: 6.0, 9: 5.5, 10: 5.0, 11: 2.5, 12: 1.5,
        13: 5.0, 14: 4.5, 15: 1.0,
    }
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (?,?,?,?,?)",
        [(pid, 3, "v2", float(xp), 90.0) for pid, xp in xp_values.items()])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.commit()


def _squad():
    return [{"element": pid, "position": pos} for pid, pos in [
        (1, "GKP"), (2, "GKP"),
        (3, "DEF"), (4, "DEF"), (5, "DEF"), (6, "DEF"), (7, "DEF"),
        (8, "MID"), (9, "MID"), (10, "MID"), (11, "MID"), (12, "MID"),
        (13, "FWD"), (14, "FWD"), (15, "FWD"),
    ]]


def test_can_form_xi_true_when_squad_is_full(db):
    _seed_basics(db)
    assert opt.can_form_xi(_squad()) is True


def test_can_form_xi_false_when_no_gk(db):
    _seed_basics(db)
    squad = [p for p in _squad() if p["position"] != "GKP"]
    assert opt.can_form_xi(squad) is False


def test_can_form_xi_false_when_no_fwd_at_all(db):
    _seed_basics(db)
    squad = [p for p in _squad() if p["position"] != "FWD"]
    assert opt.can_form_xi(squad) is False


def test_select_returns_none_when_no_gw(db):
    _seed_basics(db)
    db.execute("DELETE FROM gameweeks")
    db.commit()
    assert opt.select(db, _squad()) is None


def test_select_picks_highest_xp_in_each_position(db):
    """GK1 + top-D DEF + top-M MID + top-F FWD, where D+M+F=10."""
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    assert len(res["xi"]) == 11   # 10 outfield + 1 GK
    assert len(res["bench"]) == 4
    # GK1 has higher xp (3.0) than GK2 (2.5) → GK1 must be starter
    assert 1 in res["xi"]
    assert 2 in res["bench"]
    # top-3 DEF by xP: D1(4.5), D2(4.0), D3(3.5)
    starter_defs = sorted([eid for eid in res["xi"] if eid in (3, 4, 5, 6, 7)])
    assert starter_defs[:3] == [3, 4, 5]
    # top-3 MID: M1(6.0), M2(5.5), M3(5.0)
    starter_mids = sorted([eid for eid in res["xi"] if eid in (8, 9, 10, 11, 12)])
    assert starter_mids[:3] == [8, 9, 10]
    # top-1 FWD: F1(5.0)
    starter_fwds = [eid for eid in res["xi"] if eid in (13, 14, 15)]
    assert 13 in starter_fwds


def test_formation_string_matches_chosen_xi(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    d, m, f = (int(x) for x in res["formation"].split("-"))
    assert 3 <= d <= 5 and 3 <= m <= 5 and 1 <= f <= 3
    assert d + m + f == 10  # 10 outfield slots + 1 GK = 11 starters


def test_captain_and_vice_are_top_two_starters_by_xp(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    # M1 has the highest xP among starters in any plausible formation
    assert res["captain_id"] in res["xi"]
    assert res["vice_id"] in res["xi"]
    assert res["captain_id"] != res["vice_id"]


def test_bench_anchors_sub_gk_at_index_zero(db):
    """The bench list is ordered with the GK first so the sub-GK takes slot 12."""
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    bench_first = res["bench"][0]
    assert bench_first == 2   # GK2 — the only GK not in XI
    assert res["bench_slots"][2] == 12


def test_starter_slots_are_in_xi_range(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    for eid, slot in res["starter_slots"].items():
        assert 1 <= slot <= 11
    for eid, slot in res["bench_slots"].items():
        assert 12 <= slot <= 15


def test_select_returns_none_when_squad_cant_form_xi(db):
    """Squad with 1 GK, 1 DEF, 1 MID, 1 FWD → can't form any valid XI."""
    db.execute("INSERT INTO teams VALUES (1, 'Arsenal', 'ARS', 0, 0, 0, 0)")
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   [(1, "GK", "GKP", 1), (2, "D", "DEF", 1),
                    (3, "M", "MID", 1), (4, "F", "FWD", 1)])
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (?,?,?,?,?)",
        [(pid, 3, "v2", 2.0, 90.0) for pid in range(1, 5)])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.commit()
    squad = [{"element": pid, "position": pos} for pid, pos in
             [(1, "GKP"), (2, "DEF"), (3, "MID"), (4, "FWD")]]
    assert opt.select(db, squad) is None
