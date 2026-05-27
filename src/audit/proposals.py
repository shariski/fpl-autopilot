"""S-G T2: advisory threshold proposals.

Reads aggregate stats and emits Proposal objects for soft-tunable parameters when evidence
supports a change. v1 covers only `thresholds.min_ep_delta_for_transfer`. Other parameters are
candidates in the spec table but emit no proposals at v1.

Proposals are advisory. They are surfaced to the user. They are never applied at S-G scope.
"""
from src.audit.audit import Proposal

MIN_OBSERVATIONS = 20
PROPOSAL_STEP = 0.5  # v1 takes one fixed step per audit cycle; bigger jumps need human review.


def propose_threshold_adjustments(aggregates, current_thresholds):
    """Look at aggregates → emit Proposal objects where evidence supports a change.

    `aggregates` is dict[(decision_type, cluster_or_subset), AggregateStat].
    `current_thresholds` is dict[param_name, float] from config — used to compute proposed_value.
    """
    out = []
    transfer_agg = aggregates.get(("transfer", "all"))
    if transfer_agg is not None:
        p = _maybe_transfer_threshold_proposal(transfer_agg, current_thresholds)
        if p is not None:
            out.append(p)
    return out


def _maybe_transfer_threshold_proposal(agg, current_thresholds):
    """If transfer residuals are statistically significantly negative, propose raising the
    EP-delta threshold by one fixed step."""
    if agg.n < MIN_OBSERVATIONS:
        return None
    # CI must not cross zero — otherwise the effect is not significant.
    lo, hi = agg.ci_95
    if lo <= 0 <= hi:
        return None
    # Only raise the threshold when the mean residual is negative (transfers losing points).
    if agg.mean_residual >= 0:
        return None

    current = current_thresholds.get("thresholds.min_ep_delta_for_transfer")
    if current is None:
        return None  # No current value to compare to → can't propose anything coherent.

    proposed = round(current + PROPOSAL_STEP, 2)
    confidence = _confidence_label(agg)
    justification = (
        f"Transfers underperform expectation by mean {agg.mean_residual:.2f} EP "
        f"(95% CI [{lo:.2f}, {hi:.2f}], N={agg.n}). "
        f"Raising the threshold (one step of +{PROPOSAL_STEP}) makes the engine more selective."
    )
    return Proposal(
        parameter="thresholds.min_ep_delta_for_transfer",
        current_value=float(current),
        proposed_value=proposed,
        justification=justification,
        n_observations=agg.n,
        confidence=confidence,
        bounded_range=None,
    )


def _confidence_label(agg):
    """Confidence is derived from the relative CI tightness vs. the mean magnitude.

    Tighter CI relative to the effect size → higher confidence."""
    if agg.mean_residual == 0:
        return "low"
    half_width = (agg.ci_95[1] - agg.ci_95[0]) / 2
    ratio = abs(half_width / agg.mean_residual)
    if ratio < 0.25:
        return "high"
    if ratio < 0.55:
        return "medium"
    return "low"
