"""S-G T1: residual computation.

Compares what the engine expected (frozen xP at decision time) against what actually happened
(player_gw_stats.total_points). Pure read-only over the DB.

Per-decision-type formulas (spec §3):
- lineup / deadguard-captain: captain xP × 2 vs actual × 2 (FPL captain multiplier).
- transfer: (xp_in - xp_out) + hit_cost vs (actual_in - actual_out) + hit_cost.
- deadguard (with transfer leg): captain + transfer residuals summed.
"""
import json
from dataclasses import dataclass


@dataclass
class Residual:
    activity_log_id: int
    gw: int
    decision_type: str            # 'lineup' | 'transfer' | 'deadguard'
    subject_player_ids: list[int]
    expected_points: float
    actual_points: float
    residual: float               # actual - expected
    model_version: str
    inputs_summary: dict          # frozen snapshot for traceability


def compute_residuals(conn, gw_lo, gw_hi):
    """For all executed decisions in [gw_lo, gw_hi], compute residuals.

    Skips decisions whose subject isn't yet settled (player_gw_stats row missing).
    """
    rows = conn.execute(
        """SELECT id, gw, mode, decision_type, action_taken, inputs_json
           FROM activity_log
           WHERE gw BETWEEN ? AND ? AND executed = 1
             AND decision_type IN ('lineup', 'transfer', 'deadguard')
           ORDER BY gw, id""",
        (gw_lo, gw_hi)).fetchall()
    out = []
    for row in rows:
        inputs = json.loads(row["inputs_json"]) if row["inputs_json"] else {}
        r = _residual_for_row(conn, row, inputs)
        if r is not None:
            out.append(r)
    return out


def _residual_for_row(conn, row, inputs):
    dtype = row["decision_type"]
    if dtype == "lineup" and "captain" in inputs:
        return _residual_captain(conn, row, inputs["captain"], dtype="lineup")
    if dtype == "transfer" and "chosen" in inputs:
        return _residual_transfer(conn, row, inputs["chosen"])
    if dtype == "deadguard" and "pick" in inputs:
        return _residual_deadguard(conn, row, inputs["pick"])
    return None


# ---------- Per-type residuals ----------

def _residual_captain(conn, row, captain_pick, dtype):
    pid = captain_pick["player_id"]
    gw = row["gw"]
    actual = _actual_points(conn, pid, gw)
    if actual is None:
        return None
    xp = float(captain_pick.get("xp", 0.0))
    expected = xp * 2
    actual_doubled = actual * 2
    return Residual(
        activity_log_id=row["id"], gw=gw, decision_type=dtype,
        subject_player_ids=[pid],
        expected_points=expected, actual_points=float(actual_doubled),
        residual=actual_doubled - expected,
        model_version=_model_version_for(conn, pid, gw),
        inputs_summary={"web_name": captain_pick.get("web_name"), "xp": xp},
    )


def _residual_transfer(conn, row, chosen):
    out_pid = chosen["out"]["player_id"]
    in_pid = chosen["in"]["player_id"]
    gw = row["gw"]
    actual_in = _actual_points(conn, in_pid, gw)
    actual_out = _actual_points(conn, out_pid, gw)
    if actual_in is None or actual_out is None:
        return None
    xp_in = _xp_for(conn, in_pid, gw)
    xp_out = _xp_for(conn, out_pid, gw)
    if xp_in is None or xp_out is None:
        return None
    hit = float(chosen.get("hit_cost", 0))
    expected = (xp_in - xp_out) + hit
    actual = (actual_in - actual_out) + hit
    return Residual(
        activity_log_id=row["id"], gw=gw, decision_type="transfer",
        subject_player_ids=[out_pid, in_pid],
        expected_points=expected, actual_points=float(actual),
        residual=actual - expected,
        model_version=_model_version_for(conn, in_pid, gw),
        inputs_summary={
            "out_web_name": chosen["out"].get("web_name"),
            "in_web_name": chosen["in"].get("web_name"),
            "hit_cost": hit,
            "ep_delta_5gw": chosen.get("ep_delta_5gw"),
        },
    )


def _residual_deadguard(conn, row, captain_pick):
    """Deadguard summary row: captain × 2 + (optional) transfer leg from gameweeks columns.

    The transfer leg is recorded on gameweeks.deadguard_transfer_in_id/out_id (set by
    repository.set_deadguard_transfer). If both columns are NULL, only the captain leg counts.
    Bench is v2 — not included in v1.
    """
    gw = row["gw"]
    pid = captain_pick["player_id"]
    captain_actual = _actual_points(conn, pid, gw)
    if captain_actual is None:
        return None
    xp_captain = float(captain_pick.get("xp", 0.0))
    expected = xp_captain * 2
    actual = float(captain_actual * 2)
    subjects = [pid]

    from src.data import repository
    transfer = repository.get_deadguard_transfer(conn, gw)
    if transfer is not None:
        in_pid = transfer.get("in_id")
        out_pid = transfer.get("out_id")
        if in_pid is not None and out_pid is not None:
            actual_in = _actual_points(conn, in_pid, gw)
            actual_out = _actual_points(conn, out_pid, gw)
            xp_in = _xp_for(conn, in_pid, gw)
            xp_out = _xp_for(conn, out_pid, gw)
            if (actual_in is not None and actual_out is not None
                    and xp_in is not None and xp_out is not None):
                expected += (xp_in - xp_out)
                actual += (actual_in - actual_out)
                subjects.extend([out_pid, in_pid])

    return Residual(
        activity_log_id=row["id"], gw=gw, decision_type="deadguard",
        subject_player_ids=subjects,
        expected_points=expected, actual_points=actual,
        residual=actual - expected,
        model_version=_model_version_for(conn, pid, gw),
        inputs_summary={"captain_web_name": captain_pick.get("web_name"),
                        "captain_xp": xp_captain},
    )


# ---------- Helpers ----------

def _actual_points(conn, player_id, gw):
    """Sum total_points across all fixture rows for (player_id, gw). None if no row exists."""
    row = conn.execute(
        "SELECT SUM(total_points) AS total FROM player_gw_stats WHERE player_id=? AND gw=?",
        (player_id, gw)).fetchone()
    if row is None or row["total"] is None:
        return None
    return int(row["total"])


def _xp_for(conn, player_id, gw):
    """The most-recently-computed xP for (player_id, gw). None if no row exists."""
    row = conn.execute(
        """SELECT xp FROM xp WHERE player_id=? AND gw=?
           ORDER BY computed_at DESC LIMIT 1""",
        (player_id, gw)).fetchone()
    return float(row["xp"]) if row is not None else None


def _model_version_for(conn, player_id, gw):
    """The model_version of the most-recently-computed xP for (player_id, gw)."""
    row = conn.execute(
        """SELECT model_version FROM xp WHERE player_id=? AND gw=?
           ORDER BY computed_at DESC LIMIT 1""",
        (player_id, gw)).fetchone()
    return row["model_version"] if row is not None else "unknown"
