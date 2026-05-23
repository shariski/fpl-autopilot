import json
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


def is_enabled(cfg=None):
    return telegram.is_configured() and config.telegram_interactive_enabled(cfg)


def _dtype(decision):
    return "lineup" if decision == "captain" else "transfer"


def send_pending(conn, entry, *, gw, mode):
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
            send_pending(conn, entry, gw=gw, mode=mode)


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

    # 2. parse + idempotency
    action, _, pid_s = cq.get("data", "").partition(":")
    row = repository.get_pending_decision(conn, int(pid_s)) if pid_s.isdigit() else None
    if row is None or row["status"] != "pending":
        telegram.answer_callback_query(cq["id"], text="Already handled", session=session)
        return
    pid = row["id"]
    dtype = row["decision_type"]

    # 3. reject
    if action == "r":
        repository.set_pending_status(conn, pid, "rejected")
        repository.log_activity(conn, decision_type=dtype, mode=mode,
                                action_taken="rejected via telegram", executed=False)
        telegram.notify(conn, kind="info", decision_type=dtype, mode=mode,
                        summary="Rejected — no change made.")
        telegram.answer_callback_query(cq["id"], text="Rejected", session=session)
        return

    # 4. confirm: deadline guard
    now = now or datetime.now(timezone.utc)
    gw_row = conn.execute("SELECT deadline_utc FROM gameweeks WHERE id=?", (row["gw"],)).fetchone()
    if gw_row and gw_row["deadline_utc"] and now > datetime.fromisoformat(gw_row["deadline_utc"]):
        repository.set_pending_status(conn, pid, "expired")
        telegram.answer_callback_query(cq["id"], text="Deadline passed", session=session)
        return

    # 5. re-run + verify
    entry, available = _recompute_entry(conn, dtype, ranker=ranker, suggester=suggester)
    if not available:
        repository.set_pending_status(conn, pid, "superseded")
        telegram.answer_callback_query(cq["id"], text="No current recommendation", session=session)
        return
    if entry["identity"] != json.loads(row["identity_json"]):
        repository.set_pending_status(conn, pid, "superseded")
        send_pending(conn, entry, gw=row["gw"], mode=mode)
        telegram.answer_callback_query(cq["id"], text="Recommendation changed — see new message", session=session)
        return

    # 6. match -> execute via the existing bounded executor
    try:
        if dtype == "lineup":
            result = lineup_fn(conn, key, live=True, confirm_fn=lambda d: True, session=session)
        else:
            result = transfer_fn(conn, key, rank=1, live=True, confirm_fn=lambda d: True, session=session)
    except SessionExpired:
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="FPL session expired — re-run init-fpl. No changes were made.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    except Exception as e:  # executor error — never crash the poller
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary=f"Execution failed: {e}")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    if not getattr(result, "ok", False):
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="Execution did not complete.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    repository.set_pending_status(conn, pid, "confirmed")
    telegram.notify(conn, kind="executed", decision_type=dtype, mode=mode,
                    summary=entry["confirmed_summary"])
    telegram.answer_callback_query(cq["id"], text="Confirmed", session=session)
