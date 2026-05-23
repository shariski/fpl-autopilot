import logging
import os
from datetime import datetime, timezone

from src import config
from src.config import load_config, db_path
from src.data import repository
from src.data.db import connect, init_db
from src.decisions import captain
from src.execution import lineup
from src.interface import telegram
from src.auth.session import SessionExpired

log = logging.getLogger(__name__)

RESOLVED = ("USER_ACTED", "SYSTEM_ACTED", "DEADGUARD_EXECUTED", "DEADGUARD_SKIPPED")


def evaluate(now, *, deadline, state, last_system_action_at, user_acted,
             warned, triggered, warn_min, trigger_min):
    """Return a directive: 'system_acted' | 'user_acted' | 'warn' | 'trigger' | 'noop'.
    Pure: no I/O, deterministic for frozen inputs (B11)."""
    if state in RESOLVED:
        return "noop"
    if last_system_action_at:
        return "system_acted"
    if user_acted:
        return "user_acted"
    mins = (deadline - now).total_seconds() / 60
    if mins <= 0:
        return "noop"
    if mins <= trigger_min:
        return "noop" if triggered else "trigger"
    if mins <= warn_min:
        return "noop" if warned else "warn"
    return "noop"


def user_acted(conn, gw):
    g = conn.execute("SELECT last_user_action_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
    if g and g["last_user_action_at"]:
        return True
    n = conn.execute(
        "SELECT COUNT(*) c FROM pending_decisions WHERE gw=? AND status IN ('confirmed','rejected')",
        (gw,)).fetchone()["c"]
    return n > 0


def send_warning(conn, gw, *, mins):
    text = (f"⏳ Deadguard will set your captain in ~{mins} min if you don't act.\n"
            f"Tap to keep your team as-is.")
    buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}]]
    telegram.send_message(text, buttons=buttons)


def handle_keep(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    _, _, gw_s = cq.get("data", "").partition(":")
    if gw_s.isdigit():
        repository.touch_user_action(conn, int(gw_s))
    telegram.answer_callback_query(cq["id"], text="Kept as is ✅", session=session)
