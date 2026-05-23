import os
import requests

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
        return bool((resp.json() or {}).get("ok"))
    except ValueError:
        return False
