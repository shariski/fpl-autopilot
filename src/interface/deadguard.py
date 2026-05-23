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
