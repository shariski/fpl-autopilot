"""S-G T2: audit report persistence + load round-trip.

JSON serialization of AuditReport to data/audit/audit_{gw_lo}-{gw_hi}_{ts}.json.
Round-trip-safe — load(persist(report)) yields an equivalent AuditReport.
"""
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.analytics.residuals import Residual
from src.audit.audit import AggregateStat, AuditReport, Proposal


DEFAULT_DIR = Path("data") / "audit"


def persist(report, *, output_dir=None):
    """Write the report to JSON. Returns the absolute path written."""
    out_dir = Path(output_dir) if output_dir else DEFAULT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = report.generated_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    lo, hi = report.gw_range
    filename = f"audit_{lo}-{hi}_{ts}.json"
    path = out_dir / filename

    path.write_text(json.dumps(_to_jsonable(report), indent=2, default=str))
    return str(path.resolve())


def load(path):
    """Parse a persisted audit JSON back into an AuditReport."""
    raw = json.loads(Path(path).read_text())
    return _from_jsonable(raw)


# ---------- Serialization helpers ----------

def _to_jsonable(report):
    return {
        "gw_range": list(report.gw_range),
        "generated_at": report.generated_at.isoformat(),
        "model_version": report.model_version,
        "residuals": [asdict(r) for r in report.residuals],
        "cluster_counts": dict(report.cluster_counts),
        "aggregate_trends": {
            dtype: {"n": stat.n, "mean_residual": stat.mean_residual,
                    "stddev": stat.stddev, "ci_95": list(stat.ci_95)}
            for dtype, stat in report.aggregate_trends.items()
        },
        "proposals": [
            {**asdict(p), "bounded_range": list(p.bounded_range) if p.bounded_range else None}
            for p in report.proposals
        ],
        "narrative": report.narrative,
        "narrative_provider": report.narrative_provider,
        "persisted_path": report.persisted_path,
    }


def _from_jsonable(raw):
    residuals = [Residual(**r) for r in raw["residuals"]]
    aggregate_trends = {
        dtype: AggregateStat(
            n=stat["n"], mean_residual=stat["mean_residual"],
            stddev=stat["stddev"], ci_95=tuple(stat["ci_95"]))
        for dtype, stat in raw["aggregate_trends"].items()
    }
    proposals = [
        Proposal(
            parameter=p["parameter"],
            current_value=p["current_value"],
            proposed_value=p["proposed_value"],
            justification=p["justification"],
            n_observations=p["n_observations"],
            confidence=p["confidence"],
            bounded_range=tuple(p["bounded_range"]) if p["bounded_range"] else None,
        )
        for p in raw["proposals"]
    ]
    return AuditReport(
        gw_range=tuple(raw["gw_range"]),
        generated_at=datetime.fromisoformat(raw["generated_at"]),
        model_version=raw["model_version"],
        residuals=residuals,
        cluster_counts=raw["cluster_counts"],
        aggregate_trends=aggregate_trends,
        proposals=proposals,
        narrative=raw.get("narrative"),
        narrative_provider=raw.get("narrative_provider"),
        persisted_path=raw.get("persisted_path"),
    )


def format_text(report):
    """Human-readable single-string formatter used by the CLI `review` subcommand (T5)."""
    lines = []
    lines.append(f"=== Audit GW{report.gw_range[0]}–{report.gw_range[1]} "
                 f"(model {report.model_version}) ===")
    lines.append(f"Generated: {report.generated_at.isoformat()}")
    lines.append(f"Residuals analyzed: {len(report.residuals)}")
    if report.cluster_counts:
        lines.append("\nClusters:")
        for k, v in sorted(report.cluster_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {k}: {v}")
    if report.aggregate_trends:
        lines.append("\nTrends:")
        for dtype, stat in report.aggregate_trends.items():
            lines.append(f"  {dtype}: n={stat.n}, mean={stat.mean_residual:+.2f}, "
                         f"CI95=[{stat.ci_95[0]:+.2f}, {stat.ci_95[1]:+.2f}]")
    if report.proposals:
        lines.append("\nProposed adjustments (advisory only):")
        for p in report.proposals:
            lines.append(f"  {p.parameter}: {p.current_value} → {p.proposed_value} "
                         f"({p.confidence} conf, N={p.n_observations})")
            lines.append(f"    {p.justification}")
    else:
        lines.append("\nNo threshold adjustments proposed.")
    if report.narrative:
        lines.append(f"\n--- Narrative ({report.narrative_provider}) ---\n{report.narrative}")
    return "\n".join(lines)
