"""S-G T1: residual computation tests.

Pure-math tests using frozen activity_log + xp + player_gw_stats fixtures. No I/O beyond DB.
"""
import json

from src.analytics import residuals
from src.data.db import connect, init_db


def _db():
    conn = connect(":memory:")
    init_db(conn)
    # Minimum players + teams for FK shape (we don't enforce FK constraints in the schema
    # but downstream queries SELECT against them).
    conn.executemany("INSERT INTO teams (id, short_name, name) VALUES (?,?,?)",
                     [(1, "MCI", "Man City"), (2, "LIV", "Liverpool")])
    conn.executemany(
        "INSERT INTO players (id, web_name, position, team_id, status) VALUES (?,?,?,?,?)",
        [(10, "Haaland", "FWD", 1, "a"),
         (11, "Salah", "MID", 2, "a"),
         (12, "Watkins", "FWD", 1, "a"),
         (13, "Cl-Lewin", "FWD", 2, "a")])
    conn.commit()
    return conn


def _seed_xp(conn, rows):
    """rows: list of (player_id, gw, xp[, model_version])."""
    parsed = []
    for r in rows:
        pid, gw, xp = r[0], r[1], r[2]
        mv = r[3] if len(r) > 3 else "v1"
        parsed.append((pid, gw, mv, xp))
    conn.executemany(
        """INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at)
           VALUES (?,?,?,?,0,0,0,0,'2026-01-01T00:00:00Z')""",
        parsed,
    )
    conn.commit()


def _seed_actual(conn, rows):
    """rows: list of (player_id, gw, fixture_id, total_points)."""
    conn.executemany(
        """INSERT INTO player_gw_stats
             (player_id, gw, fixture_id, minutes, goals_scored, assists,
              clean_sheets, bonus, total_points, was_substituted_in, settled_at)
           VALUES (?,?,?,90,0,0,0,0,?,1,'2026-01-01T00:00:00Z')""",
        [(pid, gw, fid, tp) for (pid, gw, fid, tp) in rows]
    )
    conn.commit()


def _log_lineup(conn, gw, captain):
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES (?,?,?,?,?,?,1)""",
        ("2026-01-01T11:00:00Z", gw, "manual", "lineup",
         f"captain={captain['player_id']}",
         json.dumps({"captain": captain, "vice_player_id": None, "alternatives": []}))
    )
    conn.commit()


def _log_transfer(conn, gw, chosen):
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES (?,?,?,?,?,?,1)""",
        ("2026-01-01T11:00:00Z", gw, "manual", "transfer",
         f"transfer out={chosen['out']['player_id']} in={chosen['in']['player_id']}",
         json.dumps({"chosen": chosen, "alternatives": []}))
    )
    conn.commit()


def _log_deadguard(conn, gw, captain_pick):
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES (?,?,?,?,?,?,1)""",
        ("2026-01-01T11:00:00Z", gw, "deadguard", "deadguard",
         f"captain {captain_pick['web_name']}; bench optimized",
         json.dumps({"pick": captain_pick}))
    )
    conn.commit()


# ---------- Captain residual ----------

def test_residual_captain_one_subject():
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5)])
    _seed_actual(conn, [(10, 3, 100, 9)])
    _log_lineup(conn, gw=3,
                captain={"player_id": 10, "web_name": "Haaland", "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert len(out) == 1
    r = out[0]
    assert r.decision_type == "lineup"
    assert r.subject_player_ids == [10]
    # captain multiplier: expected = 6.5 * 2 = 13; actual = 9 * 2 = 18
    assert r.expected_points == 13.0
    assert r.actual_points == 18.0
    assert r.residual == 5.0
    assert r.model_version == "v1"


# ---------- Transfer residual ----------

def test_residual_transfer_in_minus_out_minus_hit():
    conn = _db()
    # Single-GW xp for both subjects at the transfer gw
    _seed_xp(conn, [(12, 5, 4.0), (13, 5, 5.5)])
    _seed_actual(conn, [(12, 5, 200, 2), (13, 5, 200, 7)])  # out underperformed, in delivered

    chosen = {
        "out": {"player_id": 12, "web_name": "Watkins", "price": 7.5},
        "in":  {"player_id": 13, "web_name": "Cl-Lewin", "price": 7.0},
        "ep_delta_5gw": 7.5,
        "hit_cost": -4,   # took a hit
        "confidence": 70,
    }
    _log_transfer(conn, gw=5, chosen=chosen)

    out = residuals.compute_residuals(conn, gw_lo=5, gw_hi=5)
    assert len(out) == 1
    r = out[0]
    assert r.decision_type == "transfer"
    assert sorted(r.subject_player_ids) == [12, 13]
    # expected = (xp_in - xp_out) + hit_cost = (5.5 - 4.0) + (-4) = -2.5
    assert r.expected_points == -2.5
    # actual = (actual_in - actual_out) + hit_cost = (7 - 2) + (-4) = 1
    assert r.actual_points == 1.0
    assert r.residual == 3.5


# ---------- Deadguard residual (captain + transfer aggregated) ----------

def test_residual_deadguard_aggregates_captain_and_transfer():
    """Deadguard's summary activity_log row covers captain pick + (optionally) transfer.
    The transfer is recorded on gameweeks.deadguard_transfer_in_id/out_id.
    v1 sums captain + transfer; bench is v2."""
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5), (12, 3, 4.0), (13, 3, 5.5)])
    _seed_actual(conn, [(10, 3, 100, 9), (12, 3, 200, 2), (13, 3, 200, 7)])
    # Record the deadguard transfer on the gameweeks row (JSON blob format)
    conn.execute(
        "INSERT INTO gameweeks (id, deadline_utc, finished, is_next, is_current, "
        "deadguard_transfer_json) "
        "VALUES (3, '2026-01-03T11:30:00Z', 1, 0, 1, ?)",
        (json.dumps({"out_id": 12, "in_id": 13}),))
    conn.commit()
    _log_deadguard(conn, gw=3,
                   captain_pick={"player_id": 10, "web_name": "Haaland",
                                 "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert len(out) == 1
    r = out[0]
    assert r.decision_type == "deadguard"
    # subjects = [captain, transfer_in, transfer_out]
    assert sorted(r.subject_player_ids) == [10, 12, 13]
    # expected: captain (6.5*2=13) + transfer delta (5.5 - 4.0 = 1.5) = 14.5
    assert r.expected_points == 14.5
    # actual: captain (9*2=18) + transfer delta (7 - 2 = 5.0) = 23
    assert r.actual_points == 23.0
    assert r.residual == 8.5


def test_residual_deadguard_captain_only_when_no_transfer():
    """If deadguard_transfer_in_id is NULL (no transfer leg), only the captain residual counts."""
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5)])
    _seed_actual(conn, [(10, 3, 100, 9)])
    conn.execute(
        "INSERT INTO gameweeks (id, deadline_utc, finished, is_next, is_current) "
        "VALUES (3, '2026-01-03T11:30:00Z', 1, 0, 1)")
    conn.commit()
    _log_deadguard(conn, gw=3,
                   captain_pick={"player_id": 10, "web_name": "Haaland",
                                 "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert len(out) == 1
    r = out[0]
    assert r.subject_player_ids == [10]
    assert r.expected_points == 13.0
    assert r.actual_points == 18.0


# ---------- Edge cases ----------

def test_residual_skips_unsettled_decisions():
    """If player_gw_stats has no row for the captain's GW, skip the decision (don't error)."""
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5)])
    # NO player_gw_stats row for (10, 3)
    _log_lineup(conn, gw=3,
                captain={"player_id": 10, "web_name": "Haaland", "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert out == []


def test_residual_dgw_sums_fixture_rows():
    """DGW: two player_gw_stats rows for (player, gw) → summed actual."""
    conn = _db()
    _seed_xp(conn, [(10, 18, 6.5)])
    _seed_actual(conn, [(10, 18, 100, 5), (10, 18, 101, 7)])  # two fixtures → 12 total
    _log_lineup(conn, gw=18,
                captain={"player_id": 10, "web_name": "Haaland", "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=18, gw_hi=18)
    assert len(out) == 1
    # actual = (5 + 7) * 2 = 24; expected = 6.5 * 2 = 13
    assert out[0].actual_points == 24.0
    assert out[0].expected_points == 13.0


def test_residual_segments_by_model_version():
    """The model_version field reflects the xp version that was active at decision time."""
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5, "v2")])  # v2 row exists
    _seed_actual(conn, [(10, 3, 100, 9)])
    _log_lineup(conn, gw=3,
                captain={"player_id": 10, "web_name": "Haaland", "position": "FWD", "xp": 6.5})

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert len(out) == 1
    assert out[0].model_version == "v2"


def test_residual_skips_non_executed_rows():
    """Decisions that were logged but not executed (e.g. aborted by user) are excluded."""
    conn = _db()
    _seed_xp(conn, [(10, 3, 6.5)])
    _seed_actual(conn, [(10, 3, 100, 9)])
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES (?,?,?,?,?,?,0)""",
        ("2026-01-01T11:00:00Z", 3, "manual", "lineup", "aborted",
         json.dumps({"captain": {"player_id": 10, "xp": 6.5}}))
    )
    conn.commit()

    out = residuals.compute_residuals(conn, gw_lo=3, gw_hi=3)
    assert out == []
