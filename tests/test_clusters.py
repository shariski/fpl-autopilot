"""S-G T1: cluster classification tests.

Pure function: classify(residual, context) -> cluster_id. No DB I/O.
"""
import random
from dataclasses import replace

from src.analytics import clusters, residuals


def _residual(*, decision_type="lineup", expected=10.0, actual=5.0,
              subject_player_ids=None, gw=3, mv="v1"):
    return residuals.Residual(
        activity_log_id=1, gw=gw, decision_type=decision_type,
        subject_player_ids=subject_player_ids or [10],
        expected_points=expected, actual_points=actual,
        residual=actual - expected, model_version=mv,
        inputs_summary={"web_name": "TestPlayer"},
    )


# Each context field has a documented default; only assert the conditions the spec calls out.

def test_classify_late_injury():
    """status changed within H-6 of deadline AND residual <= -2."""
    r = _residual(expected=10, actual=2)  # residual = -8
    ctx = {"status_changed_within_h6": True}
    assert clusters.classify(r, ctx) == "late_injury"


def test_classify_rotation_miss():
    """status='a' at decision, actual_minutes < 45, xMinutes prediction >= 75, residual negative."""
    r = _residual(expected=8, actual=2)  # residual = -6
    ctx = {"status_at_decision": "a", "actual_minutes": 30, "xminutes_pred": 80}
    assert clusters.classify(r, ctx) == "rotation_miss"


def test_classify_xp_model_miss():
    """status='a', played >= 60 minutes, residual <= -3."""
    r = _residual(expected=10, actual=4)  # residual = -6
    ctx = {"status_at_decision": "a", "actual_minutes": 75, "xminutes_pred": 80}
    assert clusters.classify(r, ctx) == "xp_model_miss"


def test_classify_lucky_outperform():
    """Large positive residual >= 5 with status='a' and minutes played."""
    r = _residual(expected=5, actual=12)  # residual = +7
    ctx = {"status_at_decision": "a", "actual_minutes": 90}
    assert clusters.classify(r, ctx) == "lucky_outperform"


def test_classify_unclassified_small_residual():
    """Small residual, nothing else matching → 'unclassified'."""
    r = _residual(expected=5, actual=4)  # residual = -1
    ctx = {"status_at_decision": "a", "actual_minutes": 90, "xminutes_pred": 80}
    assert clusters.classify(r, ctx) == "unclassified"


def test_classify_unclassified_when_context_missing():
    """If status/minutes context is unavailable, fall through to 'unclassified'."""
    r = _residual(expected=5, actual=2)
    ctx = {}
    assert clusters.classify(r, ctx) == "unclassified"


def test_classify_late_injury_beats_rotation_miss():
    """Order matters — late_injury wins when both could match."""
    r = _residual(expected=10, actual=2)
    ctx = {
        "status_changed_within_h6": True,
        "status_at_decision": "a",  # but status changed later
        "actual_minutes": 0, "xminutes_pred": 80,
    }
    assert clusters.classify(r, ctx) == "late_injury"


# ---------- Property tests ----------

def test_classify_is_total():
    """Every (residual, context) input gets exactly one cluster tag."""
    valid = {"late_injury", "rotation_miss", "xp_model_miss",
             "fdr_miss", "lucky_outperform", "unclassified"}
    rng = random.Random(42)
    for _ in range(200):
        r = _residual(expected=rng.uniform(-2, 15), actual=rng.uniform(-2, 15))
        ctx = {
            "status_changed_within_h6": rng.choice([True, False, None]),
            "status_at_decision": rng.choice(["a", "d", "i", "s", "u", None]),
            "actual_minutes": rng.choice([0, 25, 45, 60, 75, 90, None]),
            "xminutes_pred": rng.choice([0, 30, 60, 80, 90, None]),
        }
        cluster = clusters.classify(r, ctx)
        assert cluster in valid, f"unknown cluster {cluster!r} for residual={r.residual}, ctx={ctx}"


def test_classify_is_exclusive():
    """A given (residual, context) only ever returns one cluster (deterministic, no overlap)."""
    rng = random.Random(123)
    for _ in range(50):
        r = _residual(expected=rng.uniform(-2, 15), actual=rng.uniform(-2, 15))
        ctx = {
            "status_changed_within_h6": rng.choice([True, False]),
            "status_at_decision": rng.choice(["a", "d", "i"]),
            "actual_minutes": rng.choice([0, 45, 60, 90]),
            "xminutes_pred": rng.choice([0, 60, 80, 90]),
        }
        first = clusters.classify(r, ctx)
        # Calling again on the same input must return the same cluster.
        again = clusters.classify(replace(r), dict(ctx))
        assert first == again
