import os
import requests
from src.data import repository

BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
API_BASE = "https://api.telegram.org"
TIMEOUT = 10


def is_configured():
    """True only when both the bot token and chat id env vars are set and non-empty."""
    return bool(os.getenv(BOT_TOKEN_ENV)) and bool(os.getenv(CHAT_ID_ENV))


def send_message(text, *, buttons=None, session=None):
    """Pure transport. No-op (return False) if the channel is unconfigured.
    Returns True only on HTTP 200 + JSON {"ok": true}. Catches all network/HTTP
    errors and returns False. Never raises. Never logs the token/chat/URL (B7)."""
    if not is_configured():
        return False
    token = os.getenv(BOT_TOKEN_ENV)
    chat_id = os.getenv(CHAT_ID_ENV)
    payload = {"chat_id": chat_id, "text": text}
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/sendMessage", json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    return bool(body.get("ok"))


_ICONS = {
    "executed": "✅ Executed",
    "info": "📊 Decision pending",
    "alert": "❌ Autopilot blocked",
}


def _format(kind, summary):
    """B9 copy: functional icon + header + caller-built summary (action/reason/impact)."""
    header = _ICONS.get(kind, _ICONS["info"])
    suffix = "\nReview before the deadline." if kind == "info" else ""
    return f"{header}\n{summary}{suffix}"


def notify(conn, *, kind, decision_type, mode, summary, session=None):
    """Send one B9 notification. Silent no-op (no send, no log) when unconfigured.
    On a send failure while configured, log ONE activity row (B9/B10) and return
    False. Never raises."""
    if not is_configured():
        return False
    ok = send_message(_format(kind, summary), session=session)
    if not ok:
        repository.log_activity(
            conn, decision_type="notification", mode=mode,
            action_taken=f"telegram send failed ({decision_type}/{kind})",
            inputs={"kind": kind, "summary": summary, "decision_type": decision_type},
            executed=False)
    return ok


def notify_plan(conn, plan, *, mode, session=None):
    """Best-effort: notify per plan entry (executed -> confirmation, else pending info).
    Early-returns when unconfigured so callers with minimal plan dicts never touch
    summary/executed keys (keeps the existing scheduler/router tests untouched)."""
    if not is_configured():
        return
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=entry["summary"], session=session)


def get_updates(offset, *, session=None):
    """Telegram getUpdates. Returns the 'result' list, or [] when unconfigured or on any
    error (never raises, never logs the token). offset (int|None) acks prior updates."""
    if not is_configured():
        return []
    token = os.getenv(BOT_TOKEN_ENV)
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/getUpdates",
                            json={"offset": offset, "timeout": 0}, timeout=TIMEOUT)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    try:
        body = resp.json()
    except ValueError:
        return []
    if not isinstance(body, dict) or not body.get("ok"):
        return []
    result = body.get("result")
    return result if isinstance(result, list) else []


def answer_callback_query(callback_query_id, *, text=None, session=None):
    """Ack a callback so the client stops spinning. Returns False when unconfigured/on error."""
    if not is_configured():
        return False
    token = os.getenv(BOT_TOKEN_ENV)
    payload = {"callback_query_id": callback_query_id}
    if text is not None:
        payload["text"] = text
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/answerCallbackQuery", json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return False
    return resp.status_code == 200
