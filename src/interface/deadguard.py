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
    text = (f"⏳ Deadguard will set your captain when ~{mins} min remain before the deadline, "
            f"unless you act.\nTap to keep your team as-is.")
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


def _run_trigger(conn, key, gw):
    repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")
    caps = captain.get_captain_picks(conn)
    if not caps["picks"]:
        repository.set_gameweek_state(conn, gw, "DEADGUARD_SKIPPED")
        repository.mark_deadguard_triggered(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="skipped: no captain pick available", executed=False)
        _notify(conn, "info", "Deadguard ran — no safe action (no data). Team unchanged.")
        return
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True)
    except SessionExpired:
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        return
    except Exception as e:
        _notify(conn, "alert", f"Deadguard failed: {type(e).__name__}")
        return
    if not getattr(result, "ok", False):
        _notify(conn, "alert", "Deadguard: captain submission did not complete — will retry.")
        return                                          # not marked triggered -> retryable next tick
    # Captain/vice submission succeeded. Mark triggered FIRST; re-setting the same captain is
    # idempotent at FPL, so a crash before this mark only risks a harmless re-submit. NOTE: this
    # idempotency does NOT hold for the non-idempotent transfers 2.5b adds — revisit then.
    name = caps["picks"][0]["web_name"]
    try:
        repository.mark_deadguard_triggered(conn, gw)
        repository.set_gameweek_state(conn, gw, "DEADGUARD_EXECUTED")
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"captain set: {name}", inputs={"pick": caps["picks"][0]},
                                executed=True)
    except Exception:
        log.exception("deadguard post-execution bookkeeping failed (captain was already set)")
    _notify(conn, "executed", f"Deadguard set your captain: {name}")


def _notify(conn, kind, summary):
    try:
        telegram.notify(conn, kind=kind, decision_type="deadguard", mode="deadguard", summary=summary)
    except Exception:
        log.exception("deadguard notify failed")


def run_deadguard_job(key, *, conn=None, now=None, cfg=None):
    cfg = cfg or load_config()
    if not config.deadguard_enabled(cfg):
        return None
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT id, deadline_utc, state, last_system_action_at, deadguard_warned_at, "
            "deadguard_triggered_at FROM gameweeks WHERE is_next=1").fetchone()
        if not row or not row["deadline_utc"]:
            return None
        gw = row["id"]
        now = now or datetime.now(timezone.utc)
        directive = evaluate(
            now, deadline=datetime.fromisoformat(row["deadline_utc"]), state=row["state"],
            last_system_action_at=row["last_system_action_at"], user_acted=user_acted(conn, gw),
            warned=bool(row["deadguard_warned_at"]), triggered=bool(row["deadguard_triggered_at"]),
            warn_min=config.deadguard_warning_minutes(cfg),
            trigger_min=config.deadguard_trigger_minutes(cfg))
        if directive == "system_acted":
            repository.set_gameweek_state(conn, gw, "SYSTEM_ACTED")
        elif directive == "user_acted":
            repository.set_gameweek_state(conn, gw, "USER_ACTED")
        elif directive == "warn":
            send_warning(conn, gw, mins=config.deadguard_trigger_minutes(cfg))
            repository.mark_deadguard_warned(conn, gw)
        elif directive == "trigger":
            _run_trigger(conn, key, gw)
        return directive
    finally:
        if owns:
            conn.close()
