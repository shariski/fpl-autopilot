import logging
from datetime import datetime, timedelta, timezone
import requests
from src.data.fpl_client import USER_AGENT
from src.auth.crypto import decrypt, encrypt
from src.data import repository

TOKEN_URL = "https://account.premierleague.com/as/token"
ME_URL = "https://fantasy.premierleague.com/api/me/"
CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"  # public SPA client id (verified)
TIMEOUT = 10
EXPIRY_SKEW_SECONDS = 120
DEFAULT_EXPIRES_IN = 28800
log = logging.getLogger(__name__)


class SessionError(Exception):
    """Base for session failures. Never carries a token value."""


class SessionNotInitialized(SessionError):
    """No stored FPL session — run init-fpl."""


class SessionExpired(SessionError):
    """Refresh token no longer valid — re-run init-fpl."""


class TokenRefreshError(SessionError):
    """A single /as/token refresh attempt failed at the OAuth layer."""


class SessionValidationError(SessionError):
    """A token failed /me validation (not authenticated, or wrong team)."""


def _now():
    return datetime.now(timezone.utc)


def refresh_access_token(refresh_token, *, session=None):
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        error = "unknown_error"
        try:
            error = (resp.json() or {}).get("error", error)
        except ValueError:
            pass
        raise TokenRefreshError(f"refresh failed: {error}")
    return resp.json()


def _authed_session(access_token):
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "X-Api-Authorization": f"Bearer {access_token}"})
    return s


def validate_token(access_token, *, expected_team_id, session=None):
    session = session or _authed_session(access_token)
    me = session.get(ME_URL, timeout=TIMEOUT)
    player = (me.json() or {}).get("player") if me.status_code == 200 else None
    if not player or "entry" not in player:
        raise SessionValidationError("token is not authenticated")
    entry = player["entry"]
    if entry != expected_team_id:
        raise SessionValidationError(
            f"token authenticates entry {entry}, not configured team {expected_team_id}")
    return entry


def store_tokens(conn, key, *, refresh_token, access_token, expires_at):
    repository.set_encrypted(conn, "refresh_token_encrypted", encrypt(key, refresh_token))
    repository.set_encrypted(conn, "access_token_encrypted", encrypt(key, access_token))
    repository.set_access_expiry(conn, expires_at.isoformat())
    repository.touch_session_refreshed(conn)
    repository.mark_session_ok(conn)


def ensure_session(conn, key, *, refresh_session=None):
    refresh_blob = repository.get_encrypted(conn, "refresh_token_encrypted")
    if refresh_blob is None:
        raise SessionNotInitialized("no stored FPL session; run init-fpl")
    access_blob = repository.get_encrypted(conn, "access_token_encrypted")
    expiry = repository.get_access_expiry(conn)
    if access_blob is not None and expiry is not None:
        if _now() < datetime.fromisoformat(expiry) - timedelta(seconds=EXPIRY_SKEW_SECONDS):
            return _authed_session(decrypt(key, access_blob))
    try:
        tok = refresh_access_token(decrypt(key, refresh_blob), session=refresh_session)
    except TokenRefreshError:
        repository.set_auth_state(conn, "expired")
        raise SessionExpired("refresh token no longer valid; re-run init-fpl")
    access_token = tok["access_token"]
    new_refresh = tok.get("refresh_token") or decrypt(key, refresh_blob)
    expires_at = _now() + timedelta(seconds=int(tok.get("expires_in", DEFAULT_EXPIRES_IN)))
    store_tokens(conn, key, refresh_token=new_refresh, access_token=access_token, expires_at=expires_at)
    return _authed_session(access_token)
