import json
import logging
import requests
from src.auth.fpl_login import login as _login, FPLLoginError, ME_URL, TIMEOUT, USER_AGENT
from src.auth.crypto import decrypt, encrypt
from src.data import repository

log = logging.getLogger(__name__)


class SessionError(Exception):
    """Base for session-lifecycle failures. Never carries secret values."""


class SessionNotInitialized(SessionError):
    """No stored FPL session — run init-fpl."""


class SessionFrozen(SessionError):
    """Auto-execution is frozen after repeated re-login failures."""


class ReloginFailed(SessionError):
    """A single re-login attempt failed; session still expired, not yet frozen."""


def _session_from_cookies(cookies):
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    for name, value in cookies.items():
        s.cookies.set(name, value)
    return s


def ensure_session(conn, key, *, expected_team_id, login_fn=None, session=None):
    login_fn = login_fn or _login
    if repository.get_auth_state(conn) == "frozen":
        raise SessionFrozen("auto-execution is frozen; re-run init-fpl")
    cookie_blob = repository.get_encrypted(conn, "session_cookie_encrypted")
    if cookie_blob is None:
        raise SessionNotInitialized("no stored FPL session; run init-fpl")
    cookies = json.loads(decrypt(key, cookie_blob))
    session = session or _session_from_cookies(cookies)
    me = session.get(ME_URL, timeout=TIMEOUT)
    if me.status_code == 200:
        player = (me.json() or {}).get("player")
        if player and player.get("entry") == expected_team_id:
            repository.mark_session_ok(conn)
            return session
    raise SessionNotInitialized("session expired")  # placeholder; replaced in Task 4
