"""Contract tests for the optimal-XI module.

These tests pin the *exact shape* of the optimizer's inputs and outputs
using real FPL payload shape (integer slot positions, no web_name/xp on
raw picks). They exist to catch the class of bug where the optimizer is
written against a convenient test substitute and crashes against real
data — see PR history (v0.26 KeyError crashes).
"""
import pytest

from src.decisions import optimal_xi as opt


REAL_GW = 3


def _seed_min_squad(db):
    """Minimal real FPL squad: 15 picks with integer slot positions.
    Mirrors the raw shape returned by fetch_current_picks."""
    db.executemany(
        "INSERT INTO teams (id, name, short_name, strength_attack_home, "
        "strength_attack_away, strength_defence_home, strength_defence_away) "
        "VALUES (?,?,?,0,0,0,0)",
        [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE")])
    players = [
        (1, "GK1", "GK1", 1, "GKP"),
        (2, "GK2", "GK2", 1, "GKP"),
        (3, "D1", "D1", 1, "DEF"), (4, "D2", "D2", 1, "DEF"),
        (5, "D3", "D3", 1, "DEF"), (6, "D4", "D4", 1, "DEF"),
        (7, "D5", "D5", 1, "DEF"),
        (8, "M1", "M1", 1, "MID"), (9, "M2", "M2", 1, "MID"),
        (10, "M3", "M3", 1, "MID"), (11, "M4", "M4", 1, "MID"),
        (12, "M5", "M5", 1, "MID"),
        (13, "F1", "F1", 1, "FWD"), (14, "F2", "F2", 1, "FWD"),
        (15, "F3", "F3", 1, "FWD"),
    ]
    db.executemany(
        "INSERT INTO players (id, name, web_name, team_id, position, price, "
        "status, ownership, form) VALUES (?,?,?,?,?,5.0,'a',1.0,1.0)",
        players)
    xp_values = {1: 3.0, 2: 2.5, 3: 4.5, 4: 4.0, 5: 3.5, 6: 2.0, 7: 1.0,
                 8: 6.0, 9: 5.5, 10: 5.0, 11: 2.5, 12: 1.5,
                 13: 5.0, 14: 4.5, 15: 1.0}
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) "
        "VALUES (?,?,?,?,?)",
        [(pid, REAL_GW, "v2", float(xp), 90.0) for pid, xp in xp_values.items()])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (?, '2026-09-04T17:30:00+00:00', 1, 0)", (REAL_GW,))
    db.commit()


def _real_fpl_picks():
    """Real FPL payload shape: each pick has integer slot + is_captain/vice
    flags. NO web_name, NO xp — those don't exist on raw picks."""
    return [
        {"element": pid, "position": slot, "is_captain": False,
         "is_vice_captain": False, "multiplier": 1}
        for pid, slot in [
            (1, 1), (2, 12),
            (3, 2), (4, 3), (5, 4), (6, 5), (7, 13),
            (8, 6), (9, 7), (10, 8), (11, 9), (12, 14),
            (13, 10), (14, 11), (15, 15),
        ]
    ]


# ---- Input contract ----

def test_input_contract_accepts_raw_fpl_payload(db):
    """The optimizer must accept picks with integer `position` (slot 1-15)
    and no `web_name` / `xp` fields. This is what fetch_current_picks
    returns; anything else is a contract violation."""
    _seed_min_squad(db)
    picks = _real_fpl_picks()
    for p in picks:
        assert isinstance(p["position"], int), "slot must be int"
        assert 1 <= p["position"] <= 15
        assert "web_name" not in p, "raw FPL picks don't carry web_name"
        assert "xp" not in p, "raw FPL picks don't carry xp"
    res = opt.select(db, picks)
    assert res is not None


def test_input_contract_ignores_position_for_role_lookup(db):
    """Even if a caller mistakenly passes position as 'GKP'/'DEF'/etc.,
    the optimizer must look up roles from the players table — not
    trust the squad-slot field. (The slot field is the squad position
    1-15, never the player role.)"""
    _seed_min_squad(db)
    bad_picks = [{"element": pid, "position": role, "is_captain": False,
                  "is_vice_captain": False}
                 for pid, role in [
        (1, "GKP"), (2, "GKP"),
        (3, "DEF"), (4, "DEF"), (5, "DEF"), (6, "DEF"), (7, "DEF"),
        (8, "MID"), (9, "MID"), (10, "MID"), (11, "MID"), (12, "MID"),
        (13, "FWD"), (14, "FWD"), (15, "FWD"),
    ]]
    res = opt.select(db, bad_picks)
    # The optimizer must NOT trust the squad's `position` field for
    # role lookup. If it does, it will try `groups["GKP"].append(...)`
    # on a KeyError or pick the wrong formation. Either way: result
    # should still be valid (looked up via players table) — not crash.
    assert res is not None
    assert len(res["xi"]) == 11


# ---- Output contract ----

EXPECTED_KEYS = {"xi", "formation", "captain_id", "vice_id",
                 "bench", "bench_slots", "starter_slots", "total_xp"}


def test_output_contract_has_exact_keys(db):
    """The optimizer must return a dict with exactly these keys and no
    others. Drift (renames, additions, removals) breaks callers."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    assert res is not None
    assert set(res.keys()) == EXPECTED_KEYS


def test_output_contract_field_types(db):
    """Each output field has a fixed type. Pinning these keeps callers
    from getting surprised."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    assert isinstance(res["xi"], list)
    assert all(isinstance(eid, int) for eid in res["xi"])
    assert isinstance(res["formation"], str)
    assert "-" in res["formation"]
    d, m, f = (int(x) for x in res["formation"].split("-"))
    assert d + m + f == 10
    assert isinstance(res["captain_id"], int)
    assert isinstance(res["vice_id"], int)
    assert isinstance(res["bench"], list)
    assert isinstance(res["bench_slots"], dict)
    assert isinstance(res["starter_slots"], dict)
    assert isinstance(res["total_xp"], (int, float))


def test_output_contract_bench_is_slot_ordered(db):
    """`bench` must list players in slot 12→15 order. FPL's auto-sub
    follows bench order, so getting this wrong changes which player
    substitutes in first when a starter misses."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    bench_slots = res["bench_slots"]
    ordered = sorted(bench_slots, key=bench_slots.get)
    assert res["bench"] == ordered


def test_output_contract_starter_slots_in_xi_range(db):
    """`starter_slots` values must be 1-11 (FPL XI slots)."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    for slot in res["starter_slots"].values():
        assert 1 <= slot <= 11, f"starter slot {slot} out of range"


def test_output_contract_bench_slots_in_bench_range(db):
    """`bench_slots` values must be 12-15."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    for slot in res["bench_slots"].values():
        assert 12 <= slot <= 15, f"bench slot {slot} out of range"


def test_output_contract_captain_and_vice_are_starters(db):
    """Captain and vice must always start. If they aren't picked by
    `_best_formation`, the caller falls back — but the optimizer's
    contract guarantees they're in `xi`."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    assert res["captain_id"] in res["xi"]
    assert res["vice_id"] in res["xi"]
    assert res["captain_id"] != res["vice_id"]


def test_output_contract_every_player_accounted_for(db):
    """All 15 input picks must appear in either xi or bench — no drops."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    expected = {p["element"] for p in _real_fpl_picks()}
    accounted = set(res["xi"]) | set(res["bench"])
    assert accounted == expected
    # And no overlap between xi and bench
    assert not (set(res["xi"]) & set(res["bench"]))


def test_output_contract_xi_size_is_11(db):
    """XI = 10 outfield + 1 GK = 11 starters. Bench = 4 leftover."""
    _seed_min_squad(db)
    res = opt.select(db, _real_fpl_picks())
    assert len(res["xi"]) == 11
    assert len(res["bench"]) == 4


# ---- Fallback contract ----

def test_fallback_returns_none_when_no_gw(db):
    """No upcoming GW → no optimizer decision. The caller falls through
    to the existing captain/vice + bench-order logic."""
    _seed_min_squad(db)
    db.execute("DELETE FROM gameweeks")
    db.commit()
    assert opt.select(db, _real_fpl_picks()) is None


def test_fallback_returns_none_when_squad_cant_form_xi(db):
    """Squad without enough outfielders → no optimizer decision."""
    db.execute("INSERT INTO teams VALUES (1, 'Arsenal', 'ARS', 0, 0, 0, 0)")
    db.executemany(
        "INSERT INTO players (id, name, web_name, team_id, position, price, "
        "status, ownership, form) VALUES (?,?,?,?,?,5.0,'a',1.0,1.0)",
        [(1, "GK", "GK", 1, "GKP"), (2, "D", "D", 1, "DEF"),
         (3, "M", "M", 1, "MID"), (4, "F", "F", 1, "FWD")])
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) "
        "VALUES (?,?,?,?,?)",
        [(pid, REAL_GW, "v2", 2.0, 90.0) for pid in range(1, 5)])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (?, '2026-09-04T17:30:00+00:00', 1, 0)", (REAL_GW,))
    db.commit()
    picks = [{"element": pid, "position": slot, "is_captain": False,
              "is_vice_captain": False, "multiplier": 1}
             for pid, slot in [(1, 1), (2, 2), (3, 3), (4, 4)]]
    assert opt.select(db, picks) is None
