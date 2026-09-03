
import pytest

from src.decisions import formation_rebalancer as form_mod


PRIOR_GW = 2


def _seed_basics(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE")])
    players = [
        (1, "GK", "GKP", 1),
        (2, "D1", "DEF", 1), (3, "D2", "DEF", 1), (4, "D3", "DEF", 1),
        (5, "D4", "DEF", 1), (6, "D5", "DEF", 1),
        (7, "M1", "MID", 1), (8, "M2", "MID", 1), (9, "M3", "MID", 1),
        (10, "M4", "MID", 1), (11, "M5", "MID", 1),
        (12, "F1", "FWD", 1), (13, "F2", "FWD", 1),
        (14, "F3", "FWD", 1), (15, "F4", "FWD", 1),
    ]
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)", players)
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (?,?,?,?,?)",
        [(pid, 3, "v2", float(xp), 90.0)
         for pid, xp in [(1, 3.0), (2, 2.0), (3, 2.5), (4, 4.0), (5, 1.0), (6, 0.5),
                         (7, 5.0), (8, 6.0), (9, 3.5), (10, 2.8), (11, 1.5),
                         (12, 4.5), (13, 4.0), (14, 3.0), (15, 1.2)]])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               f"VALUES ({PRIOR_GW}, '2026-08-28T17:30:00+00:00', 0, 1)")
    db.commit()


def _picks():
    return [
        {"element": 1, "position": 1},
        {"element": 2, "position": 2}, {"element": 3, "position": 3},
        {"element": 4, "position": 4}, {"element": 5, "position": 5},
        {"element": 7, "position": 6}, {"element": 8, "position": 7},
        {"element": 9, "position": 8}, {"element": 10, "position": 9},
        {"element": 12, "position": 10},
        {"element": 13, "position": 12},
        {"element": 14, "position": 13},
        {"element": 11, "position": 14},
        {"element": 15, "position": 15},
    ]


def _seed_cohort(db, formations):
    for i, fm in enumerate(formations, start=1):
        db.execute(
            "INSERT INTO leader_gw_picks (entry_id, gw, picks_json, captain_id, "
            "vice_id, formation, fetched_at) VALUES (?,?,?,?,?,?,?)",
            (i, PRIOR_GW, "[]", None, None, fm, "2026-09-01T00:00:00+00:00"))
    db.commit()


def test_rebalance_returns_none_when_cohort_below_minimum(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-4-2"] * 5)
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_returns_none_when_modal_ties_within_one(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-4-2"] * 12 + ["4-3-3"] * 11)
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_swaps_to_modal_formation(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-3-3"] * 25 + ["4-4-2"] * 10)
    swap = form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8)
    assert swap is not None
    assert len(swap) == 2
    out_eid = next(iter(swap.keys()))
    in_eid = next(e for e in swap if e != out_eid)
    assert swap[out_eid] in (13, 14, 15)
    assert swap[in_eid] in range(1, 12)
    picks_dict = {p["element"]: p["position"] for p in _picks()}
    assert swap[out_eid] == picks_dict[in_eid]
    assert swap[in_eid] == picks_dict[out_eid]
    assert out_eid == 10
    assert in_eid == 14


def test_rebalance_no_op_when_already_aligned(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-4-1"] * 30)
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_refuses_when_squad_cannot_fill_modal(db):
    db.execute("INSERT INTO teams VALUES (1, 'Arsenal', 'ARS', 0, 0, 0, 0)")
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   [(1, "GK", "GKP", 1), (2, "D1", "DEF", 1), (3, "D2", "DEF", 1),
                    (4, "D3", "DEF", 1), (5, "M1", "MID", 1), (6, "M2", "MID", 1),
                    (7, "M3", "MID", 1), (8, "F1", "FWD", 1)])
    db.executemany("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) "
                   "VALUES (?,?,?,?,?)",
                   [(pid, 3, "v2", 2.0, 90.0) for pid in range(1, 9)])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               f"VALUES ({PRIOR_GW}, '2026-08-28T17:30:00+00:00', 0, 1)")
    db.commit()
    _seed_cohort(db, ["3-4-3"] * 25)
    tiny_picks = [{"element": pid, "position": slot}
                  for slot, pid in enumerate([1, 2, 3, 4, 5, 6, 7, 8], start=1)]
    for slot in range(9, 16):
        tiny_picks.append({"element": 1, "position": slot})
    assert form_mod.rebalance(db, tiny_picks, captain_id=8, vice_id=7) is None


def test_rebalance_protects_captain_and_vice(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-3-3"] * 30)
    assert form_mod.rebalance(db, _picks(), captain_id=10, vice_id=8) is None


def test_formation_info_reports_modal_and_current(db):
    _seed_basics(db)
    _seed_cohort(db, ["4-3-3"] * 30)
    info = form_mod.formation_info(db, _picks())
    assert info["cohort"] == 30
    assert info["modal"] == "4-3-3"
    assert info["current"] == "4-4-1"
    assert info["gw"] == 3
    assert info["source_gw"] == PRIOR_GW


def test_no_modal_when_no_finished_gw_exists(db):
    db.execute("INSERT INTO teams VALUES (1, 'Arsenal', 'ARS', 0, 0, 0, 0)")
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   [(1, "GK", "GKP", 1), (2, "D1", "DEF", 1)])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (1, '2026-08-21T17:30:00+00:00', 1, 0)")
    db.commit()
    info = form_mod.formation_info(db, _picks())
    assert info["modal"] is None
    assert info["source_gw"] is None
