"""S-G T4: audit narrator.

Layers LLM prose onto an AuditReport using the same prompt+payload+render+generate pattern as
the S-A narrators in src/ai/reasoning.py. The only differences: a new pane_type ('audit') in
the ai_reasoning_cache, and the provider can be either Ollama OR Claude (selected by the
caller — typically run_audit reads config to choose).
"""
import json
import logging
from pathlib import Path

from src.ai import cache, grounding
from src.ai.provider import ClaudeError, OllamaError

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PANE_TYPE = "audit"
_TOP_RESIDUALS = 10


def _trim(x):
    """Coerce a whole-numbered float to int so JSON yields '13' not '13.0'."""
    rounded = round(x, 2)
    return int(rounded) if rounded == int(rounded) else rounded


# ---------- Payload + prompt ----------

def _build_audit_payload(report):
    """Closed-shape payload: numbers + names only. No timestamps, no DB ids, no PII.

    Whole-numbered floats are coerced to int so JSON serialization yields '13' rather than '13.0'
    — important for grounding, because the LLM naturally writes integers without trailing zeros.
    """
    top = sorted(report.residuals, key=lambda r: abs(r.residual), reverse=True)[:_TOP_RESIDUALS]
    top_payload = []
    for r in top:
        top_payload.append({
            "web_name": r.inputs_summary.get("web_name")
                        or r.inputs_summary.get("captain_web_name")
                        or r.inputs_summary.get("in_web_name") or "Unknown",
            "gw": r.gw,
            "decision_type": r.decision_type,
            "expected": _trim(r.expected_points),
            "actual": _trim(r.actual_points),
            "residual": _trim(r.residual),
        })

    aggregate_payload = {
        dtype: {
            "n": stat.n,
            "mean_residual": _trim(stat.mean_residual),
            "ci_lo": _trim(stat.ci_95[0]),
            "ci_hi": _trim(stat.ci_95[1]),
        }
        for dtype, stat in report.aggregate_trends.items()
    }

    proposals_payload = [
        {"parameter": p.parameter, "current": p.current_value, "proposed": p.proposed_value,
         "n": p.n_observations, "confidence": p.confidence}
        for p in report.proposals
    ]

    return {
        "gw_range": list(report.gw_range),
        "model_version": report.model_version,
        "n_residuals": len(report.residuals),
        "cluster_counts": dict(report.cluster_counts),
        "aggregate_trends": aggregate_payload,
        "top_residuals": top_payload,
        "proposals": proposals_payload,
    }


def _build_audit_prompt(payload):
    template = (_PROMPTS_DIR / "audit.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "audit_examples.json").read_text())
    examples_text = "\n\n".join(
        f"INPUT: {json.dumps(ex['input'])}\nOUTPUT: {ex['output']}" for ex in examples)
    return template.format(examples=examples_text, payload_json=json.dumps(payload))


def _report_hash(report):
    """Cache identity: hash of the payload (so identical reports reuse prose)."""
    payload = _build_audit_payload(report)
    return cache.recommendation_hash(payload)


# ---------- Read + write paths ----------

def render_audit_narrative(conn, report):
    """Read path: returns (prose, model_id) or (None, None) on cache miss."""
    gw = report.gw_range[1]  # cache key uses the closing gw of the window
    rec_hash = _report_hash(report)
    row = cache.get(conn, gw, _PANE_TYPE, rec_hash)
    if row is None:
        return None, None
    return row["prose"], row["model_id"]


def generate_audit_narrative(conn, report, *, provider, model_id,
                             max_tokens=1500, temperature=0.2):
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors / quota errors / grounding failures all log + return False, never raise."""
    if not report.residuals:
        logger.info("ai.audit.skipped_empty", extra={"gw_range": list(report.gw_range)})
        return False

    gw = report.gw_range[1]
    rec_hash = _report_hash(report)
    if cache.get(conn, gw, _PANE_TYPE, rec_hash) is not None:
        return True  # already cached

    payload = _build_audit_payload(report)
    prompt = _build_audit_prompt(payload)

    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except (OllamaError, ClaudeError) as e:
        logger.warning("ai.audit.provider_error",
                       extra={"gw_range": list(report.gw_range),
                              "model_id": model_id, "error": type(e).__name__})
        return False

    if not prose:
        logger.warning("ai.audit.empty_prose",
                       extra={"gw_range": list(report.gw_range), "model_id": model_id})
        return False

    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.audit.grounding_failed",
                       extra={"gw_range": list(report.gw_range),
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False

    cache.put(conn, gw, _PANE_TYPE, rec_hash, prose, model_id)
    return True
