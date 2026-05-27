"""S-G T2: audit assembly.

Orchestrates residuals → clusters → aggregates → proposals into a serializable AuditReport.
Persists to disk via reports.persist. Logs the audit run itself to activity_log.

The audit is purely descriptive (B4): it reads past decisions and outcomes, produces a report,
and may include *advisory* proposals. It never applies a proposal — that's S-H's job, gated on
a future B15 amendment.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AggregateStat:
    n: int
    mean_residual: float
    stddev: float
    ci_95: tuple[float, float]


@dataclass
class Proposal:
    parameter: str
    current_value: float
    proposed_value: float
    justification: str
    n_observations: int
    confidence: str                            # 'high' | 'medium' | 'low'
    bounded_range: tuple[float, float] | None  # None at S-G; S-H supplies bounds


@dataclass
class AuditReport:
    gw_range: tuple[int, int]
    generated_at: datetime
    model_version: str
    residuals: list = field(default_factory=list)               # list[Residual]
    cluster_counts: dict = field(default_factory=dict)
    aggregate_trends: dict = field(default_factory=dict)        # dict[str, AggregateStat]
    proposals: list = field(default_factory=list)               # list[Proposal]
    narrative: str | None = None
    narrative_provider: str | None = None
    persisted_path: str | None = None


# ---------- Statistics ----------

def aggregate_from_values(values):
    """Build an AggregateStat from a list of residual floats.

    Empty list returns a zero-shaped Stat (n=0). CI uses the 1.96σ/√n normal approximation —
    fine since proposals require n ≥ 20 anyway."""
    n = len(values)
    if n == 0:
        return AggregateStat(n=0, mean_residual=0.0, stddev=0.0, ci_95=(0.0, 0.0))
    mean = sum(values) / n
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0
    half_width = 1.96 * stddev / math.sqrt(n) if n else 0.0
    return AggregateStat(n=n, mean_residual=mean, stddev=stddev,
                         ci_95=(mean - half_width, mean + half_width))


# ---------- run_audit ----------

def run_audit(conn, gw_lo, gw_hi, *, output_dir=None,
              ai_provider=None, ai_model_id=None,
              current_thresholds=None, _injected_aggregates_for_proposals=None):
    """Compute residuals + cluster + aggregate + proposals + persist + log self.

    `_injected_aggregates_for_proposals` is a test seam — passes synthetic AggregateStats
    directly to the proposals layer without needing fixture data. Not used in production.
    `output_dir` defaults to data/audit/ relative to the project root.
    """
    from src.analytics import residuals as residuals_mod, clusters as clusters_mod
    from src.audit import proposals as proposals_mod, reports as reports_mod
    from src.data import repository

    residuals_list = residuals_mod.compute_residuals(conn, gw_lo, gw_hi)

    # Cluster classification (v1 context is sparse — most residuals route to 'unclassified').
    cluster_counts = defaultdict(int)
    for r in residuals_list:
        cluster = clusters_mod.classify(r, _build_context(conn, r))
        cluster_counts[cluster] += 1

    # Aggregate per decision_type (v1 — finer-grained per (decision_type, cluster) is a v2 lift).
    by_type = defaultdict(list)
    for r in residuals_list:
        by_type[r.decision_type].append(r.residual)
    aggregate_trends = {dtype: aggregate_from_values(vals) for dtype, vals in by_type.items()}

    # Proposals (advisory). The test seam injects synthetic aggregates; production uses real ones
    # keyed in the shape proposals expects.
    proposal_aggregates = _injected_aggregates_for_proposals
    if proposal_aggregates is None:
        proposal_aggregates = {(dtype, "all"): stat for dtype, stat in aggregate_trends.items()}
    proposals_list = proposals_mod.propose_threshold_adjustments(
        proposal_aggregates, current_thresholds or {})

    model_version = _report_model_version(residuals_list)

    report = AuditReport(
        gw_range=(gw_lo, gw_hi),
        generated_at=datetime.now(timezone.utc),
        model_version=model_version,
        residuals=residuals_list,
        cluster_counts=dict(cluster_counts),
        aggregate_trends=aggregate_trends,
        proposals=proposals_list,
    )

    # AI narration (T4): best-effort, never breaks the audit if it fails.
    if ai_provider is not None:
        from src.ai import audit_narrator
        ok = audit_narrator.generate_audit_narrative(
            conn, report, provider=ai_provider, model_id=ai_model_id or "unknown")
        if ok:
            prose, model_id = audit_narrator.render_audit_narrative(conn, report)
            report.narrative = prose
            report.narrative_provider = model_id

    # Persist to disk.
    report.persisted_path = reports_mod.persist(report, output_dir=output_dir)

    # Log the audit run itself (B10).
    repository.log_activity(
        conn,
        decision_type="audit",
        mode="audit",
        action_taken=f"audit gw={gw_lo}..{gw_hi} n_residuals={len(residuals_list)}",
        inputs={"gw_lo": gw_lo, "gw_hi": gw_hi, "n_residuals": len(residuals_list),
                "model_version": model_version,
                "n_proposals": len(proposals_list)},
        executed=True,
    )

    return report


def _build_context(conn, residual):
    """Build the cluster-classification context for a single residual.

    v1 returns a minimal context. Enriching this (status_changed_within_h6, actual_minutes,
    xminutes_pred) requires additional columns on activity_log / status-change tracking that
    don't exist yet. v2 will populate this fully.
    """
    return {
        # v1 placeholders — most residuals will route to 'unclassified' until we backfill
        # status change tracking + minutes recording.
        "status_changed_within_h6": None,
        "status_at_decision": None,
        "actual_minutes": None,
        "xminutes_pred": None,
    }


def _report_model_version(residuals_list):
    if not residuals_list:
        return "unknown"
    versions = {r.model_version for r in residuals_list}
    if len(versions) == 1:
        return versions.pop()
    return "mixed"
