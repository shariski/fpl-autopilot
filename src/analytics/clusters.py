"""S-G T1: cluster classification.

Pure function: classify(residual, context) → cluster_id. No DB I/O.

Clusters are evaluated in priority order; the first matching cluster wins (deterministic +
exclusive). A residual that matches no condition becomes 'unclassified'.

Context keys (all optional — missing → cluster falls through):
    status_changed_within_h6: bool — player's status changed to d/i/s within H-6 of deadline
    status_at_decision: str        — 'a'/'d'/'i'/'s'/'u' at the moment the decision was logged
    actual_minutes: int            — minutes the player actually played in the GW
    xminutes_pred: float           — the xMinutes prediction at decision time
"""

VALID_CLUSTERS = (
    "late_injury",
    "rotation_miss",
    "xp_model_miss",
    "fdr_miss",
    "lucky_outperform",
    "unclassified",
)


def classify(residual, context):
    """Return the cluster id for this residual.

    The order below is the priority: late_injury beats rotation_miss beats xp_model_miss, etc.
    """
    r = residual.residual

    if _is_late_injury(r, context):
        return "late_injury"
    if _is_rotation_miss(r, context):
        return "rotation_miss"
    if _is_xp_model_miss(r, context):
        return "xp_model_miss"
    if _is_fdr_miss(r, context):
        return "fdr_miss"
    if _is_lucky_outperform(r, context):
        return "lucky_outperform"
    return "unclassified"


def _is_late_injury(r, ctx):
    return bool(ctx.get("status_changed_within_h6")) and r <= -2


def _is_rotation_miss(r, ctx):
    if ctx.get("status_at_decision") != "a":
        return False
    actual_min = ctx.get("actual_minutes")
    xmin_pred = ctx.get("xminutes_pred")
    if actual_min is None or xmin_pred is None:
        return False
    return actual_min < 45 and xmin_pred >= 75 and r < 0


def _is_xp_model_miss(r, ctx):
    """Engine over-predicted: player played but underperformed by ≥ 3.
    Positive surprises are caught by lucky_outperform instead."""
    if ctx.get("status_at_decision") != "a":
        return False
    actual_min = ctx.get("actual_minutes")
    if actual_min is None:
        return False
    return actual_min >= 60 and r <= -3


def _is_fdr_miss(r, ctx):
    # v1 placeholder: requires team-level fixture analysis we haven't built yet.
    # Returns False to leave the dispatch consistent; a future enrichment fills this in.
    return False


def _is_lucky_outperform(r, ctx):
    if ctx.get("status_at_decision") != "a":
        return False
    actual_min = ctx.get("actual_minutes")
    if actual_min is None or actual_min < 1:
        return False
    return r >= 5
