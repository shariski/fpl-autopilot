"""Emergency override (freeze / kill-switch) — Phase 2.7.

The gate that halts autonomous FPL execution. Imports only the Data Layer (B2): it
never sends notifications — confirmation/alert copy is the caller's job (CLI prints,
Telegram handlers reply, orchestrators notify).
"""
import json
from datetime import datetime, timezone

from src.data import repository

FREEZE_KEY = "freeze"
RELOGIN_FAILURE_THRESHOLD = 2          # B7: "if re-login fails twice in a row"


def is_frozen(conn):
    return repository.get_system_state(conn, FREEZE_KEY) is not None


def status(conn):
    """Return {since, reason, source} when frozen, else None."""
    raw = repository.get_system_state(conn, FREEZE_KEY)
    return json.loads(raw) if raw else None


def freeze(conn, *, reason, source):
    """Halt autonomous execution. Idempotent: a no-op (no re-log) if already frozen.
    source in {'user', 'auto'}."""
    if is_frozen(conn):
        return
    payload = {"since": datetime.now(timezone.utc).isoformat(), "reason": reason, "source": source}
    repository.set_system_state(conn, FREEZE_KEY, json.dumps(payload))
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"frozen ({source}): {reason}", executed=True)


def unfreeze(conn, *, source):
    """Resume autonomous execution. Idempotent. Does NOT reset relogin_failures (a
    successful refresh / re-init resets it via mark_session_ok)."""
    if not is_frozen(conn):
        return
    repository.clear_system_state(conn, FREEZE_KEY)
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"unfrozen ({source})", executed=True)


def maybe_auto_freeze(conn):
    """B7 policy: freeze (source='auto') when consecutive re-login failures reach the
    threshold. Reads the counter (incremented by ensure_session) — does NOT increment.
    Returns True only on the transition into a freeze, so the caller alerts exactly once."""
    if is_frozen(conn):
        return False
    if repository.get_relogin_failures(conn) >= RELOGIN_FAILURE_THRESHOLD:
        freeze(conn, reason="2 consecutive FPL re-login failures", source="auto")
        return True
    return False
