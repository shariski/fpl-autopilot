"""S-G T2: audit assembly tests.

Tests for run_audit, AuditReport, AggregateStat, and disk persistence.
"""
import json
import os
import tempfile

from src.audit import audit, reports
from src.audit.audit import AggregateStat
from src.data.db import connect, init_db


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.executemany("INSERT INTO teams (id, short_name, name) VALUES (?,?,?)",
                     [(1, "MCI", "Man City")])
    conn.executemany(
        "INSERT INTO players (id, web_name, position, team_id, status) VALUES (?,?,?,?,?)",
        [(10, "Haaland", "FWD", 1, "a"),
         (11, "KDB", "MID", 1, "a")])
    conn.commit()
    return conn


def _seed_simple_decision(conn, gw, captain_id, xp, actual, model_version="v1"):
    """Convenience: seed enough for a single captain residual at the given gw."""
    conn.execute(
        """INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at)
           VALUES (?,?,?,?,0,0,0,0,'2026-01-01T00:00:00Z')""",
        (captain_id, gw, model_version, xp))
    conn.execute(
        """INSERT INTO player_gw_stats
             (player_id, gw, fixture_id, minutes, goals_scored, assists,
              clean_sheets, bonus, total_points, was_substituted_in, settled_at)
           VALUES (?,?,?,90,0,0,0,0,?,1,'2026-01-01T00:00:00Z')""",
        (captain_id, gw, 100 + gw, actual))
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES (?,?,?,?,?,?,1)""",
        ("2026-01-01T11:00:00Z", gw, "manual", "lineup",
         f"captain={captain_id}",
         json.dumps({"captain": {"player_id": captain_id, "web_name": "Pl", "xp": xp},
                     "vice_player_id": None, "alternatives": []})))
    conn.commit()


# ---------- AggregateStat ----------

def test_aggregate_stat_mean_and_ci():
    """Constructed from a list of residual values, AggregateStat reports n, mean, stddev, CI."""
    values = [1.0, -1.0, 2.0, -2.0, 0.0, 1.5, -1.5, 0.5, -0.5, 0.0]  # mean ≈ 0
    agg = audit.aggregate_from_values(values)
    assert agg.n == 10
    assert abs(agg.mean_residual - 0.0) < 0.01
    assert agg.stddev > 0
    # CI must straddle the mean
    assert agg.ci_95[0] < agg.mean_residual < agg.ci_95[1]


def test_aggregate_stat_empty_handling():
    """Zero observations is valid (no CI). Used by empty-report code path."""
    agg = audit.aggregate_from_values([])
    assert agg.n == 0
    assert agg.mean_residual == 0.0
    assert agg.stddev == 0.0
    assert agg.ci_95 == (0.0, 0.0)


# ---------- run_audit ----------

def test_run_audit_with_no_decisions_returns_empty_report():
    conn = _db()
    out_dir = tempfile.mkdtemp()
    report = audit.run_audit(conn, gw_lo=1, gw_hi=3, output_dir=out_dir)

    assert report.gw_range == (1, 3)
    assert report.residuals == []
    assert report.cluster_counts == {}
    assert report.aggregate_trends == {}
    assert report.proposals == []


def test_run_audit_aggregates_by_decision_type_and_cluster():
    conn = _db()
    # Two captain decisions, both unclassified (no rich context), at gws 3 and 4
    _seed_simple_decision(conn, gw=3, captain_id=10, xp=6.5, actual=9)  # residual +5
    _seed_simple_decision(conn, gw=4, captain_id=11, xp=5.0, actual=4)  # residual -2
    out_dir = tempfile.mkdtemp()

    report = audit.run_audit(conn, gw_lo=1, gw_hi=10, output_dir=out_dir)
    assert len(report.residuals) == 2
    # Both residuals end up 'unclassified' because we have no status_at_decision context yet
    assert report.cluster_counts.get("unclassified", 0) == 2
    # Aggregate per decision_type
    assert "lineup" in report.aggregate_trends
    assert report.aggregate_trends["lineup"].n == 2


def test_run_audit_persists_to_disk():
    conn = _db()
    _seed_simple_decision(conn, gw=3, captain_id=10, xp=6.5, actual=9)
    out_dir = tempfile.mkdtemp()

    report = audit.run_audit(conn, gw_lo=1, gw_hi=5, output_dir=out_dir)

    files = os.listdir(out_dir)
    assert len(files) == 1
    assert files[0].startswith("audit_")
    assert files[0].endswith(".json")
    # The file path is also reported back on the report
    assert report.persisted_path is not None
    assert os.path.exists(report.persisted_path)


def test_reports_load_round_trip():
    conn = _db()
    _seed_simple_decision(conn, gw=3, captain_id=10, xp=6.5, actual=9)
    out_dir = tempfile.mkdtemp()
    report = audit.run_audit(conn, gw_lo=1, gw_hi=5, output_dir=out_dir)

    loaded = reports.load(report.persisted_path)
    assert loaded.gw_range == report.gw_range
    assert len(loaded.residuals) == len(report.residuals)
    assert loaded.residuals[0].residual == report.residuals[0].residual
    assert loaded.cluster_counts == report.cluster_counts


def test_audit_logs_its_own_run_to_activity_log():
    conn = _db()
    _seed_simple_decision(conn, gw=3, captain_id=10, xp=6.5, actual=9)
    out_dir = tempfile.mkdtemp()

    audit.run_audit(conn, gw_lo=1, gw_hi=5, output_dir=out_dir)

    rows = list(conn.execute(
        "SELECT decision_type, executed FROM activity_log WHERE decision_type='audit'"))
    assert len(rows) == 1
    assert rows[0]["executed"] == 1


def test_run_audit_proposals_attached_to_report():
    """If aggregate evidence supports a proposal, run_audit attaches it (using config-provided
    current_thresholds)."""
    conn = _db()
    out_dir = tempfile.mkdtemp()

    # Fake 22 transfers, all losing -1 point (tight CI well below zero) — simulated via the
    # AggregateStat passed in through current_thresholds.
    report = audit.run_audit(
        conn, gw_lo=1, gw_hi=5, output_dir=out_dir,
        current_thresholds={"thresholds.min_ep_delta_for_transfer": 2.0},
        _injected_aggregates_for_proposals={
            ("transfer", "all"): AggregateStat(
                n=22, mean_residual=-0.7, stddev=1.0, ci_95=(-1.12, -0.28))
        })
    assert len(report.proposals) == 1
    assert report.proposals[0].parameter == "thresholds.min_ep_delta_for_transfer"
