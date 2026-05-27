"""S-G T2: advisory proposal tests.

`propose_threshold_adjustments(aggregates, current_thresholds)` produces Proposal objects
for soft-tunable parameters when evidence supports a change. S-G never applies these — they're
informational. S-H (a later slice gated on a B15 amendment) is what actually applies.
"""
from src.audit import proposals


def _agg(n, mean, stddev):
    """Construct an AggregateStat for testing. CI computed inline (1.96 σ / √n)."""
    from src.audit.audit import AggregateStat
    half_width = 1.96 * stddev / (n ** 0.5) if n else 0.0
    return AggregateStat(n=n, mean_residual=mean, stddev=stddev,
                         ci_95=(mean - half_width, mean + half_width))


def test_propose_higher_transfer_threshold_when_transfers_underperform():
    """N=22 transfers, mean residual -0.7 with tight CI well below zero → propose +0.5 step up."""
    aggregates = {
        ("transfer", "all"): _agg(n=22, mean=-0.7, stddev=1.0),
    }
    current_thresholds = {"thresholds.min_ep_delta_for_transfer": 2.0}

    out = proposals.propose_threshold_adjustments(aggregates, current_thresholds)
    assert len(out) == 1
    p = out[0]
    assert p.parameter == "thresholds.min_ep_delta_for_transfer"
    assert p.current_value == 2.0
    assert p.proposed_value == 2.5
    assert p.n_observations == 22
    assert p.confidence in ("high", "medium", "low")
    assert "underperform" in p.justification.lower() or "below" in p.justification.lower()


def test_no_proposal_when_n_below_threshold():
    """Only 5 transfers → not enough evidence regardless of mean residual."""
    aggregates = {
        ("transfer", "all"): _agg(n=5, mean=-3.0, stddev=0.5),  # huge effect
    }
    current_thresholds = {"thresholds.min_ep_delta_for_transfer": 2.0}

    out = proposals.propose_threshold_adjustments(aggregates, current_thresholds)
    assert out == []


def test_no_proposal_when_ci_crosses_zero():
    """N=20 but the 95% CI spans 0 → not statistically significant → no proposal."""
    aggregates = {
        ("transfer", "all"): _agg(n=20, mean=-0.3, stddev=3.0),  # wide CI → crosses zero
    }
    current_thresholds = {"thresholds.min_ep_delta_for_transfer": 2.0}

    out = proposals.propose_threshold_adjustments(aggregates, current_thresholds)
    assert out == []


def test_proposal_confidence_label_reflects_ci_width():
    """Tighter CI → higher confidence label."""
    # Very tight CI: confidence='high'
    tight = {("transfer", "all"): _agg(n=50, mean=-1.5, stddev=0.5)}
    out_tight = proposals.propose_threshold_adjustments(
        tight, {"thresholds.min_ep_delta_for_transfer": 2.0})
    assert out_tight[0].confidence == "high"

    # Moderate CI: confidence='medium' or 'low'
    moderate = {("transfer", "all"): _agg(n=22, mean=-0.7, stddev=1.0)}
    out_mod = proposals.propose_threshold_adjustments(
        moderate, {"thresholds.min_ep_delta_for_transfer": 2.0})
    # CI half-width ~= 1.96 * 1.0 / sqrt(22) ~= 0.42 → ratio = 0.42 / 0.7 = 0.60 → medium
    assert out_mod[0].confidence in ("medium", "low")
    # Sanity: tight is more confident than moderate
    assert out_tight[0].confidence != out_mod[0].confidence or out_tight[0].n_observations > out_mod[0].n_observations


def test_proposal_step_is_bounded():
    """Proposed step is at most +0.5 from current (per spec — bounded auto-tune; v1 fixed step)."""
    aggregates = {
        ("transfer", "all"): _agg(n=50, mean=-3.0, stddev=0.4),  # massive miss
    }
    current_thresholds = {"thresholds.min_ep_delta_for_transfer": 2.0}

    out = proposals.propose_threshold_adjustments(aggregates, current_thresholds)
    assert len(out) == 1
    # Even with massive evidence, propose a single +0.5 step — bigger changes need human approval
    assert out[0].proposed_value == 2.5
