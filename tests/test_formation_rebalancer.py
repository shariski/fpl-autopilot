import json
import pytest

from src.decisions import formation_rebalancer as form_mod


def _seed_basics(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE")])
    # 1 GK, 5 DEF, 5 MID, 4 FWD — enough to fill any 1-D-M-F up to 5-5-4
    players = [
        (1, "GK", "GKP", 1),
        (2, "D1", "DEF", 1), (3, "D2", "DEF", 1), (4, "D3", "DEF", 1),
        (5, "D4", "DEF", 1), (6, "D5", "DEF", 1),
        (7, "M1", "MID", 1), (8, "M2", "MID", 1), (9, "M3", "MID", 1),
        (10, "M4", "MID", 1), (11, "M5", "MID", 1),
        (12, "F1", "FWD", 1), (13, "F2", "FWD", 1),
        (14, "F3", "FWD", 1), (15, "F4", "FWD", 1),
    ]
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   players)
    # xp v2 for the upcoming GW
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (?,?,?,?,?)",
        [(pid, 3, "v2", float(xp), 90.0)
         for pid, xp in [(1, 3.0), (2, 2.0), (3, 2.5), (4, 4.0), (5, 1.0), (6, 0.5),
                         (7, 5.0), (8, 6.0), (9, 3.5), (10, 2.8), (11, 1.5),
                         (12, 4.5), (13, 4.0), (14, 3.0), (15, 1.2)]])
    # upcoming GW
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.commit()


def _picks():
    # XI: 1-4-4-1 with GK(1) + D1 D2 D3 D4 + M1 M2 M3 M4 + F1
    # Bench: F2(12), F3(13), M5(14), F4(15)
    return [
        {"element": 1, "position": 1},
        {"element": 2, "position": 2}, {"element": 3, "position": 3},
        {"element": 4, "position": 4}, {"element": 5, "position": 5},
        {"element": 7, "position": 6}, {"element": 8, "position": 7},
        {"element": 9, "position": 8}, {"element": 10, "position": 9},
        {"element": 12, "position": 10},
        {"element": 13, "position": 12},   # bench GK (we have no extra GK; use F2 as bench anchor)
        {"element": 14, "position": 13},
        {"element": 11, "position": 14},
        {"element": 15, "position": 15},
    ]


def _seed_cohort(db, gw, formations):
    """Insert leader_gw_picks rows. formations = list of formation strings."""
    for i, fm in enumerate(formations, start=1):
        db.execute(
            "INSERT INTO leader_gw_picks (entry_id, gw, picks_json, captain_id, "
            "vice_id, formation, fetched_at) VALUES (?,?,?,?,?,?,?)",
            (i, gw, "[]", None, None, fm, "2026-09-01T00:00:00+00:00"))
    db.commit()


def test_rebalance_returns_none_when_cohort_below_minimum(db):
    _seed_basics(db)
    _seed_cohort(db, 3, ["4-4-2"] * 5)  # cohort 5 < 20
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_returns_none_when_modal_ties_within_one(db):
    _seed_basics(db)
    # 12 votes for 4-4-2, 11 for 4-3-3 — modal wins by 1 (within margin)
    _seed_cohort(db, 3, ["4-4-2"] * 12 + ["4-3-3"] * 11)
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_swaps_to_modal_formation(db):
    """XI = 1-4-4-1, cohort modal = 4-3-3 → swap lowest-xP MID starter for highest-xP bench FWD."""
    _seed_basics(db)
    _seed_cohort(db, 3, ["4-3-3"] * 25 + ["4-4-2"] * 10)
    swap = form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8)
    assert swap is not None
    assert len(swap) == 2
    out_eid = next(iter(swap.keys()))
    in_eid = next(e for e in swap if e != out_eid)
    # the outgoing starter moves to a bench slot, the incoming bench player
    # moves to the starter's XI slot
    assert swap[out_eid] in (13, 14, 15), "starter must move to bench slot 13-15"
    assert swap[in_eid] in range(1, 12), "bench player must move to XI slot"
    # and they exchange each other's current slots
    picks_dict = {p["element"]: p["position"] for p in _picks()}
    assert swap[out_eid] == picks_dict[in_eid]
    assert swap[in_eid] == picks_dict[out_eid]
    # lowest-xP MID starter (M4=10, xp 2.8) swaps with highest-xP bench FWD
    # of the deficit position (F3=14, xp 3.0 from slot 13)
    assert out_eid == 10
    assert in_eid == 14


def test_rebalance_no_op_when_already_aligned(db):
    _seed_basics(db)
    # XI is 1-4-4-1 → cohort modal is 4-4-1 → no swap
    _seed_cohort(db, 3, ["4-4-1"] * 30)
    assert form_mod.rebalance(db, _picks(), captain_id=12, vice_id=8) is None


def test_rebalance_refuses_when_squad_cannot_fill_modal(db):
    """Squad has only 1 FWD; modal is 3-4-3 → cannot fill → no swap."""
    # build a tiny squad: 1 GK, 3 DEF, 3 MID, 1 FWD
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
    db.commit()
    _seed_cohort(db, 3, ["3-4-3"] * 25)  # modal 3-4-3, squad can't fill (only 1 FWD)
    tiny_picks = [{"element": pid, "position": slot}
                  for slot, pid in enumerate([1, 2, 3, 4, 5, 6, 7, 8], start=1)]
    # pad to 15 with extra slot-15 dummy (won't be picked since not in squad)
    for slot in range(9, 16):
        tiny_picks.append({"element": 1, "position": slot})  # reuse, doesn't matter
    assert form_mod.rebalance(db, tiny_picks, captain_id=8, vice_id=7) is None


def test_rebalance_protects_captain_and_vice(db):
    """If the swap would bench captain or vice, return None (B4: ranker decides flags)."""
    _seed_basics(db)
    _seed_cohort(db, 3, ["4-3-3"] * 30)
    # captain = M4 (the lowest-xP MID starter we'd otherwise swap out)
    assert form_mod.rebalance(db, _picks(), captain_id=10, vice_id=8) is None


def test_formation_info_reports_modal_and_current(db):
    _seed_basics(db)
    _seed_cohort(db, 3, ["4-3-3"] * 30)
    info = form_mod.formation_info(db, _picks())
    assert info["cohort"] == 30
    assert info["modal"] == "4-3-3"
    assert info["current"] == "4-4-1"
    assert info["gw"] == 3
