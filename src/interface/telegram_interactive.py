import json
import logging
import os
from datetime import datetime, timezone

from src import config
from src.config import db_path
from src.data import repository
from src.data.db import connect, init_db
from src.decisions import captain, transfers
from src.execution import lineup, transfer as transfer_exec
from src.auth.session import SessionExpired
from src.interface import telegram

log = logging.getLogger(__name__)


def is_enabled(cfg=None):
    return telegram.is_configured() and config.telegram_interactive_enabled(cfg)


def _dtype(decision):
    return "lineup" if decision == "captain" else "transfer"


def send_pending(conn, entry, *, gw):
    """Create a pending_decisions row, then send the buttoned ping. No-op if unconfigured."""
    if not telegram.is_configured():
        return
    pid = repository.create_pending_decision(
        conn, gw=gw, decision_type=_dtype(entry["decision"]),
        identity=entry["identity"], summary=entry["summary"])
    buttons = [[{"text": "✅ Confirm", "callback_data": f"c:{pid}"},
                {"text": "❌ Reject", "callback_data": f"r:{pid}"}]]
    text = f"📊 Decision pending\n{entry['summary']}\nConfirm or reject below."
    telegram.send_message(text, buttons=buttons)


def notify_plan(conn, plan, *, gw, mode):
    """Interactive variant of telegram.notify_plan: executed -> ✅ confirmation; pending -> buttoned ping."""
    for entry in plan:
        if entry["executed"]:
            telegram.notify(conn, kind="executed", decision_type=entry["decision"],
                            mode=mode, summary=entry["summary"])
        else:
            send_pending(conn, entry, gw=gw)


def _recompute_entry(conn, decision_type, *, ranker, suggester):
    """Recompute the current top decision. Returns (entry, available). entry has
    decision/summary/identity (router shape) plus confirmed_summary (neutral wording)."""
    if decision_type == "lineup":
        caps = ranker(conn)
        if not caps["picks"]:
            return None, False
        name = caps["picks"][0]["web_name"]
        return ({"decision": "captain",
                 "summary": f"Captain pending: {name} (confidence {caps['confidence']})",
                 "confirmed_summary": f"Captain: {name}",
                 "identity": {"captain_id": caps["picks"][0]["player_id"],
                              "vice_id": caps["vice_player_id"]}}, True)
    sugg = suggester(conn)
    if not sugg["suggestions"]:
        return None, False
    top = sugg["suggestions"][0]
    out_n, in_n = top["out"]["web_name"], top["in"]["web_name"]
    return ({"decision": "transfer",
             "summary": (f"Transfer pending: OUT {out_n} IN {in_n} "
                         f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})"),
             "confirmed_summary": f"OUT {out_n} IN {in_n}",
             "identity": {"out_id": top["out"]["player_id"], "in_id": top["in"]["player_id"]}}, True)


def _resolve(conn, pid, status, *, dtype, mode, action):
    """Set the pending row's terminal status AND write the matching activity_log entry (B10)."""
    repository.set_pending_status(conn, pid, status)
    repository.log_activity(conn, decision_type=dtype, mode=mode, action_taken=action, executed=False)


def handle_callback(conn, key, cq, *, session=None, now=None,
                    ranker=None, suggester=None, lineup_fn=None, transfer_fn=None):
    ranker = ranker or captain.get_captain_picks
    suggester = suggester or transfers.get_transfer_suggestions
    lineup_fn = lineup_fn or lineup.run_lineup
    transfer_fn = transfer_fn or transfer_exec.run_transfer
    mode = config.mode()

    # 1. chat whitelist
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return

    # 2. parse + validate action + idempotency
    action, _, pid_s = cq.get("data", "").partition(":")
    if action not in ("c", "r") or not pid_s.isdigit():
        telegram.answer_callback_query(cq["id"], text="Unknown action", session=session)
        return
    row = repository.get_pending_decision(conn, int(pid_s))
    if row is None or row["status"] != "pending":
        telegram.answer_callback_query(cq["id"], text="Already handled", session=session)
        return
    pid = row["id"]
    dtype = row["decision_type"]

    # 3. reject
    if action == "r":
        _resolve(conn, pid, "rejected", dtype=dtype, mode=mode, action="rejected via telegram")
        telegram.notify(conn, kind="info", decision_type=dtype, mode=mode,
                        summary="Rejected — no change made.")
        telegram.answer_callback_query(cq["id"], text="Rejected", session=session)
        return

    # 4. confirm: deadline guard
    now = now or datetime.now(timezone.utc)
    gw_row = conn.execute("SELECT deadline_utc FROM gameweeks WHERE id=?", (row["gw"],)).fetchone()
    if gw_row and gw_row["deadline_utc"] and now > datetime.fromisoformat(gw_row["deadline_utc"]):
        _resolve(conn, pid, "expired", dtype=dtype, mode=mode, action="expired (deadline passed)")
        telegram.answer_callback_query(cq["id"], text="Deadline passed", session=session)
        return

    # 5. re-run + verify (guarded: a ranker/suggester failure must not crash the poller)
    try:
        entry, available = _recompute_entry(conn, dtype, ranker=ranker, suggester=suggester)
    except Exception as e:
        _resolve(conn, pid, "failed", dtype=dtype, mode=mode,
                 action=f"confirm failed (recompute): {type(e).__name__}")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary=f"Recommendation check failed: {type(e).__name__}")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    if not available:
        _resolve(conn, pid, "superseded", dtype=dtype, mode=mode,
                 action="superseded (no current recommendation)")
        telegram.answer_callback_query(cq["id"], text="No current recommendation", session=session)
        return
    if entry["identity"] != json.loads(row["identity_json"]):
        _resolve(conn, pid, "superseded", dtype=dtype, mode=mode,
                 action="superseded (recommendation changed)")
        send_pending(conn, entry, gw=row["gw"])
        telegram.answer_callback_query(cq["id"], text="Recommendation changed — see new message", session=session)
        return

    # 6. match -> execute via the existing bounded executor
    try:
        if dtype == "lineup":
            result = lineup_fn(conn, key, live=True, confirm_fn=lambda d: True)
        else:
            result = transfer_fn(conn, key, rank=1, live=True, confirm_fn=lambda d: True)
    except SessionExpired:
        _resolve(conn, pid, "failed", dtype=dtype, mode=mode, action="confirm failed: session expired")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="FPL session expired — re-run init-fpl. No changes were made.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    except Exception as e:  # executor error — never crash the poller
        _resolve(conn, pid, "failed", dtype=dtype, mode=mode, action=f"confirm failed: {type(e).__name__}")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary=f"Execution failed: {type(e).__name__}")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    if not getattr(result, "ok", False):
        _resolve(conn, pid, "failed", dtype=dtype, mode=mode, action="confirm failed: execution did not complete")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="Execution did not complete.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    repository.set_pending_status(conn, pid, "confirmed")   # the executor already logged the execution
    telegram.notify(conn, kind="executed", decision_type=dtype, mode=mode,
                    summary=entry["confirmed_summary"])
    telegram.answer_callback_query(cq["id"], text="Confirmed", session=session)


def poll_once(key, *, conn=None, session=None):
    if not is_enabled():
        return
    owns = conn is None
    conn = conn or connect(db_path())
    init_db(conn)
    try:
        offset = repository.get_telegram_state(conn, "update_offset")
        offset = int(offset) if offset is not None else None
        for u in telegram.get_updates(offset, session=session):
            try:
                cq = u.get("callback_query")
                if cq:
                    handle_callback(conn, key, cq, session=session)
            except Exception:
                log.exception("telegram handle_callback failed; advancing offset to avoid a poison loop")
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
    finally:
        if owns:
            conn.close()
