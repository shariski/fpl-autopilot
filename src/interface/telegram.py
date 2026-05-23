import os

BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
API_BASE = "https://api.telegram.org"
TIMEOUT = 10


def is_configured():
    """True only when both the bot token and chat id env vars are set and non-empty."""
    return bool(os.getenv(BOT_TOKEN_ENV)) and bool(os.getenv(CHAT_ID_ENV))
