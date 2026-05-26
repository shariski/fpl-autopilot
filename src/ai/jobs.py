"""Scheduler-facing entry point. Walks the requested pane types and generates
prose for the next gameweek's recommendation, caching on success.

In Phase 3 S-A.1, only 'captain' is implemented. S-A.2/3/4 add 'transfer',
'chip', 'deadguard_summary' by adding branches here.
"""
import logging
from typing import Callable

from src.ai import reasoning

logger = logging.getLogger(__name__)


def _next_gw(conn) -> int | None:
    row = conn.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def _default_captain_decision_fn(conn):
    from src.decisions import captain
    return captain.get_captain_picks(conn)


def generate_ai_reasoning_job(
    conn,
    *,
    panes: list[str],
    provider,
    model_id: str,
    captain_decision_fn: Callable | None = None,
) -> dict:
    """Walk `panes`, generate prose for each, cache on success.

    Returns {pane_type: status_str} where status_str ∈
    {'ok', 'failed', 'skipped'}. Used by the scheduler for log diagnostics.
    """
    gw = _next_gw(conn)
    if gw is None:
        return {p: "skipped" for p in panes}
    result: dict[str, str] = {}
    captain_fn = captain_decision_fn or _default_captain_decision_fn
    for pane in panes:
        if pane == "captain":
            decision = captain_fn(conn)
            ok = reasoning.generate_captain_prose(
                conn, gw=gw, captain_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
        else:
            logger.warning("ai.jobs.unknown_pane", extra={"pane": pane})
            result[pane] = "skipped"
    return result
