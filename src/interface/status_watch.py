"""Squad status-change watcher: alerts when a squad player's availability worsens.

Runs inside the scheduler's refresh cycle: captures the squad's (status,
chance_of_playing) BEFORE the FPL fetch, diffs AFTER, and alerts Telegram for
players whose availability worsened (B9: state the action + impact; idempotent
— no change, no alert). Constants pinned in docs/decision-engine.md (v0.18).
"""
import json
import logging

from src.data import repository
from src.interface import telegram

log = logging.getLogger(__name__)

# Availability worsening: a status-rank increase, or a play-chance drop of at
# least this many percentage points (chance_of_playing is 0-100 in the DB).
STATUS_RANK = {"a": 0, "d": 1, "i": 2, "u": 3, "s": 3}
COP_DROP_POINTS = 25

_STATUS_LABEL = {"a": "available", "d": "doubtful", "i": "injured",
                 "u": "unavailable", "s": "suspended"}


def squad_status_snapshot(conn):
    """{player_id: (status, chance_of_playing)} for the current squad (latest my_team)."""
    row = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if row is None:
        return {}
    ids = [p["element"] for p in json.loads(row["picks_json"])]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, status, chance_of_playing FROM players WHERE id IN ({ph})", ids).fetchall()
    return {r["id"]: (r["status"], r["chance_of_playing"]) for r in rows}


def changed(before, after):
    """Players whose availability worsened between two snapshots.

    Returns [(player_id, old_status, old_cop, new_status, new_cop)] — status
    rank increased, or chance_of_playing dropped by >= COP_DROP_POINTS.
    Improvements, no-ops, removed players and players new to the squad are ignored.
    """
    out = []
    for pid, (ost, ocop) in before.items():
        nxt = after.get(pid)
        if nxt is None:
            continue
        nst, ncop = nxt
        if STATUS_RANK.get(nst, 0) > STATUS_RANK.get(ost, 0):
            out.append((pid, ost, ocop, nst, ncop))
        elif (ocop is not None and ncop is not None
              and ncop <= ocop - COP_DROP_POINTS):
            out.append((pid, ost, ocop, nst, ncop))
    return out


def _fmt_cop(cop):
    return f"{cop:.0f}%" if cop is not None else "—"


def run_watch(conn, before, *, notify_fn=None):
    """Diff the fresh snapshot against `before`, notify + log each worsening.

    Returns the list of alerted (player_id, new_status, new_cop). Never raises:
    a notify failure is logged and does not abort the remaining alerts.
    """
    notify_fn = notify_fn or telegram.notify
    after = squad_status_snapshot(conn)
    alerted = []
    for pid, ost, ocop, nst, ncop in changed(before, after):
        row = conn.execute("SELECT web_name FROM players WHERE id=?", (pid,)).fetchone()
        name = row["web_name"] if row else f"#{pid}"
        summary = (f"{name} flagged {_STATUS_LABEL.get(nst, nst)} — "
                   f"{_fmt_cop(ncop)} chance to play (was {_fmt_cop(ocop)}).")
        try:
            notify_fn(conn, kind="status", decision_type="status", mode="auto",
                      summary=summary)
        except Exception:
            log.exception("status_watch.notify_failed")
        repository.log_activity(
            conn, decision_type="status", mode="auto",
            action_taken=f"status change {ost}->{nst}",
            inputs={"player_id": pid, "old_status": ost, "new_status": nst,
                    "old_cop": ocop, "new_cop": ncop, "summary": summary},
            executed=False)
        alerted.append((pid, nst, ncop))
    return alerted
