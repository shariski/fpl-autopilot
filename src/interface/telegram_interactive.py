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
