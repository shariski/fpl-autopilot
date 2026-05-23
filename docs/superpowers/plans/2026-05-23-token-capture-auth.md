# Token-Capture Auth (with refresh) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Authenticate to the FPL API with a captured OAuth2 refresh token: mint short-lived (8h) Bearer access tokens via `account.premierleague.com/as/token`, cache + auto-refresh them, and call the API with `X-Api-Authorization: Bearer`.

**Architecture:** Rebuild `src/auth/session.py` around the refresh-token grant (`refresh_access_token`, `validate_token`, `store_tokens`, `ensure_session`), add token columns to `credentials`, rework `_init_fpl_cli` into a refresh-token paste flow, then delete the dead programmatic-login code. 5 tasks ordered to keep the suite green: schema → repository → session → cli → delete.

**Tech Stack:** Python 3.11+, `requests`, `cryptography` (Fernet via `src/auth/crypto.py`), raw `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-token-capture-auth-design.md`

**Baseline:** suite is green at 146 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/data/schema.sql` | Modify | add `refresh_token_encrypted`, `access_token_encrypted`, `access_token_expires_at` to `credentials` |
| `src/data/db.py` | Modify | extend `_migrate_credentials` with the 3 new columns |
| `src/data/repository.py` | Modify | whitelist the 2 token BLOB columns; add `set_access_expiry`/`get_access_expiry`; (Task 5) drop `increment_relogin_failures` |
| `src/auth/session.py` | Rewrite | `refresh_access_token`, `validate_token`, `store_tokens`, `ensure_session`, `_authed_session`, exceptions |
| `src/cli.py` | Modify | `_init_fpl_cli` → refresh-token paste; `_auth_status_cli` → show expiry |
| `src/auth/fpl_login.py` | Delete | dead programmatic login |
| `tests/test_db.py`, `tests/test_repository.py`, `tests/test_session.py`, `tests/test_cli_init_fpl.py`, `tests/test_fpl_login.py` | Modify/Rewrite/Delete | tests |

Unchanged: `src/auth/crypto.py`, `src/auth/master.py`. Dormant columns kept (no drop): `fpl_email_encrypted`, `fpl_password_encrypted`, `session_cookie_encrypted`, `csrf_token_encrypted`, `relogin_failures`.

---

### Task 1: Token columns + migration

**Files:** Modify `src/data/schema.sql`, `src/data/db.py`; Test `tests/test_db.py`

- [x] **Step 1: Write the failing test** — append to `tests/test_db.py`:

```python
def test_migrate_credentials_adds_token_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY, session_last_refreshed TIMESTAMP)")
    db._migrate_credentials(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    for c in ("refresh_token_encrypted", "access_token_encrypted", "access_token_expires_at"):
        assert c in cols
    db._migrate_credentials(conn)  # idempotent
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_migrate_credentials_adds_token_columns -v`
Expected: FAIL — `assert 'refresh_token_encrypted' in cols` fails (column not added yet).

- [x] **Step 3: Implement**

In `src/data/schema.sql`, change the `credentials` table tail from:
```sql
  auth_state TEXT DEFAULT 'active',
  relogin_failures INTEGER DEFAULT 0
);
```
to:
```sql
  auth_state TEXT DEFAULT 'active',
  relogin_failures INTEGER DEFAULT 0,
  refresh_token_encrypted BLOB,
  access_token_encrypted BLOB,
  access_token_expires_at TEXT
);
```

In `src/data/db.py`, extend `_migrate_credentials` — after the existing `relogin_failures` block, add:
```python
    if "refresh_token_encrypted" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN refresh_token_encrypted BLOB")
    if "access_token_encrypted" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN access_token_encrypted BLOB")
    if "access_token_expires_at" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN access_token_expires_at TEXT")
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 2 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 147 passed (146 + 1).

- [x] **Step 6: Commit**

```bash
git add src/data/schema.sql src/data/db.py tests/test_db.py
git commit -m "feat: credentials token columns (refresh/access/expiry) + migration"
```

---

### Task 2: Repository token helpers

**Files:** Modify `src/data/repository.py`; Test `tests/test_repository.py`

- [x] **Step 1: Write the failing tests** — append to `tests/test_repository.py`:

```python
def test_token_columns_whitelisted(db):
    from src.data import repository
    repository.set_encrypted(db, "refresh_token_encrypted", b"rt")
    repository.set_encrypted(db, "access_token_encrypted", b"at")
    assert repository.get_encrypted(db, "refresh_token_encrypted") == b"rt"
    assert repository.get_encrypted(db, "access_token_encrypted") == b"at"


def test_access_expiry_get_set(db):
    from src.data import repository
    assert repository.get_access_expiry(db) is None
    repository.set_access_expiry(db, "2026-05-23T12:00:00+00:00")
    assert repository.get_access_expiry(db) == "2026-05-23T12:00:00+00:00"
```

- [x] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_repository.py -k "token_columns or access_expiry" -v`
Expected: FAIL — `set_encrypted` raises `ValueError: unknown credential column: 'refresh_token_encrypted'`; `get_access_expiry` missing.

- [x] **Step 3: Implement**

In `src/data/repository.py`, change `_CRED_COLUMNS` to include the two token columns:
```python
_CRED_COLUMNS = {
    "fpl_email_encrypted", "fpl_password_encrypted",
    "session_cookie_encrypted", "csrf_token_encrypted",
    "refresh_token_encrypted", "access_token_encrypted",
}
```
Then add, immediately after `touch_session_refreshed`:
```python
def set_access_expiry(conn, expires_at_iso):
    conn.execute(
        "INSERT INTO credentials (id, access_token_expires_at) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET access_token_expires_at=excluded.access_token_expires_at",
        (expires_at_iso,),
    )
    conn.commit()


def get_access_expiry(conn):
    row = conn.execute("SELECT access_token_expires_at FROM credentials WHERE id=1").fetchone()
    return row["access_token_expires_at"] if row else None
```

- [x] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_repository.py -k "token_columns or access_expiry" -v`
Expected: 2 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 149 passed (147 + 2).

- [x] **Step 6: Commit**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "feat: repository token-column whitelist + access-expiry helpers"
```

---

### Task 3: Rebuild `session.py` around the refresh-token grant

**Files:** Rewrite `src/auth/session.py`, `tests/test_session.py`

The old module (re-login/freeze, `fpl_login` import) is replaced wholesale. Nothing in non-test code imports `session.py` yet, so this is self-contained. Note: `ensure_session` does NOT take `expected_team_id` (team-id is validated once at `init-fpl`; re-checking on every call is an unused parameter — omitted).

- [x] **Step 1: Write the failing tests** — overwrite `tests/test_session.py` with EXACTLY:

```python
import pytest
from datetime import datetime, timedelta, timezone
from src.auth import session, master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeTokenSession:
    """Fake /as/token endpoint."""

    def __init__(self, *, status_code=200, payload=None):
        self.headers = {}
        self._status = status_code
        self._payload = payload

    def post(self, url, data=None, headers=None, timeout=None):
        return _Resp(status_code=self._status, payload=self._payload)


class _FakeMeSession:
    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _key(tmp_path):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    return master.init_master_password("throwaway-master-12", s, v)


def test_refresh_access_token_ok():
    fake = _FakeTokenSession(payload={"access_token": "AT", "expires_in": 28800, "refresh_token": "RT2"})
    out = session.refresh_access_token("rt", session=fake)
    assert out["access_token"] == "AT"


def test_refresh_access_token_oauth_error():
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.TokenRefreshError) as exc:
        session.refresh_access_token("rt-throwaway", session=fake)
    assert "invalid_grant" in str(exc.value)
    assert "rt-throwaway" not in str(exc.value)


def test_validate_token_ok():
    fake = _FakeMeSession(me_payload={"player": {"entry": 3122849}})
    assert session.validate_token("AT", expected_team_id=3122849, session=fake) == 3122849


def test_validate_token_team_mismatch():
    fake = _FakeMeSession(me_payload={"player": {"entry": 999}})
    with pytest.raises(session.SessionValidationError):
        session.validate_token("AT", expected_team_id=3122849, session=fake)


def test_validate_token_not_authenticated():
    fake = _FakeMeSession(me_payload={"player": None})
    with pytest.raises(session.SessionValidationError):
        session.validate_token("AT", expected_team_id=3122849, session=fake)


def test_store_tokens_roundtrip(tmp_path, db):
    key = _key(tmp_path)
    exp = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=exp)
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "RT"
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "AT"
    assert repository.get_access_expiry(db) == exp.isoformat()
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_uses_cached_token(tmp_path, db):
    key = _key(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=4)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT-cached", expires_at=future)
    boom = _FakeTokenSession(status_code=500, payload={"error": "should_not_be_called"})
    s = session.ensure_session(db, key, refresh_session=boom)
    assert s.headers["X-Api-Authorization"] == "Bearer AT-cached"


def test_ensure_session_refreshes_when_expired(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT-old", access_token="AT-old", expires_at=past)
    fake = _FakeTokenSession(payload={"access_token": "AT-new", "expires_in": 28800, "refresh_token": "RT-new"})
    s = session.ensure_session(db, key, refresh_session=fake)
    assert s.headers["X-Api-Authorization"] == "Bearer AT-new"
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "AT-new"
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "RT-new"


def test_ensure_session_refresh_failure_expires(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=past)
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.SessionExpired):
        session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_auth_state(db) == "expired"


def test_ensure_session_not_initialized(tmp_path, db):
    key = _key(tmp_path)
    with pytest.raises(session.SessionNotInitialized):
        session.ensure_session(db, key, refresh_session=_FakeTokenSession())
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: FAIL — `AttributeError: module 'src.auth.session' has no attribute 'refresh_access_token'` (old module).

- [x] **Step 3: Rewrite the module** — overwrite `src/auth/session.py` with EXACTLY:

```python
import logging
from datetime import datetime, timedelta, timezone
import requests
from src.data.fpl_client import USER_AGENT
from src.auth.crypto import decrypt, encrypt
from src.data import repository

TOKEN_URL = "https://account.premierleague.com/as/token"
ME_URL = "https://fantasy.premierleague.com/api/me/"
CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"  # public SPA client id (from the access-token JWT)
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
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: 10 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 153 passed. `session.py` no longer imports `fpl_login`; `cli.py` still uses `fpl_login` (still present), so nothing else breaks.

- [x] **Step 6: Commit**

```bash
git add src/auth/session.py tests/test_session.py
git commit -m "feat: rebuild session.py for OAuth refresh-token auth"
```

---

### Task 4: Rework `_init_fpl_cli` into a refresh-token paste flow

**Files:** Modify `src/cli.py`; Rewrite `tests/test_cli_init_fpl.py`

- [x] **Step 1: Write the failing tests** — overwrite `tests/test_cli_init_fpl.py` with EXACTLY:

```python
from src import cli
from src.auth import master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeTokenSession:
    def __init__(self, *, status_code=200, payload=None):
        self.headers = {}
        self._status = status_code
        self._payload = payload

    def post(self, url, data=None, headers=None, timeout=None):
        return _Resp(status_code=self._status, payload=self._payload)


class _FakeMeSession:
    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _setup_master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_init_fpl_stores_tokens(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-paste-xyz")
    tok = _FakeTokenSession(payload={"access_token": "access-xyz", "expires_in": 28800, "refresh_token": "refresh-rot-xyz"})
    me = _FakeMeSession(me_payload={"player": {"entry": 3122849}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok, me_session=me)
    key = master.load_key("throwaway-master-12", s, v)
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "access-xyz"
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "refresh-rot-xyz"
    assert repository.get_auth_state(db) == "active"
    out = capsys.readouterr().out
    assert "3122849" in out
    assert "refresh-paste-xyz" not in out and "access-xyz" not in out  # tokens never echoed


def test_init_fpl_rejects_bad_refresh_token(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-bad")
    tok = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok,
                      me_session=_FakeMeSession(me_payload={}))
    assert repository.get_encrypted(db, "refresh_token_encrypted") is None
    assert "rejected" in capsys.readouterr().out.lower()


def test_init_fpl_rejects_wrong_team(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-ok")
    tok = _FakeTokenSession(payload={"access_token": "access-xyz", "expires_in": 28800})
    me = _FakeMeSession(me_payload={"player": {"entry": 999}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok, me_session=me)
    assert repository.get_encrypted(db, "refresh_token_encrypted") is None
    assert "rejected" in capsys.readouterr().out.lower()


def test_init_fpl_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-ok")
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v,
                      refresh_session=_FakeTokenSession(), me_session=_FakeMeSession(me_payload={}))
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"] == 0


def test_auth_status_cli(db, capsys):
    repository.set_auth_state(db, "active")
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "active" in out
    assert "auth_state" in out
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -v`
Expected: FAIL — old `_init_fpl_cli` has no `refresh_session` parameter (`TypeError`).

- [x] **Step 3: Replace `_init_fpl_cli`** — in `src/cli.py`, replace the ENTIRE `_init_fpl_cli` function with:

```python
def _init_fpl_cli(conn=None, salt_path=None, verify_path=None, refresh_session=None, me_session=None):
    import os
    import requests
    from datetime import datetime, timezone, timedelta
    from .auth import master, session as auth_session
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    refresh_token = os.getenv("FPL_REFRESH_TOKEN") or input("Paste FPL refresh token: ")
    try:
        tok = auth_session.refresh_access_token(refresh_token, session=refresh_session)
        entry = auth_session.validate_token(tok["access_token"], expected_team_id=cfg_team_id(), session=me_session)
    except auth_session.TokenRefreshError as exc:
        print(f"Refresh token rejected: {exc}")
        return
    except auth_session.SessionValidationError as exc:
        print(f"Token rejected: {exc}")
        return
    except requests.RequestException:
        print("Couldn't reach FPL; check your connection.")
        return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 28800)))
    auth_session.store_tokens(conn, key, refresh_token=tok.get("refresh_token") or refresh_token,
                              access_token=tok["access_token"], expires_at=expires_at)
    if owns_conn:
        conn.close()
    print(f"Authenticated as entry {entry}; session stored.")
```

- [x] **Step 4: Replace `_auth_status_cli`** — in `src/cli.py`, replace the ENTIRE `_auth_status_cli` function with:

```python
def _auth_status_cli(conn=None):
    from .data import repository
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    state = repository.get_auth_state(conn)
    if state is None:
        print("No stored FPL session — run `fpl-autopilot init-fpl`.")
    else:
        row = conn.execute(
            "SELECT session_last_refreshed FROM credentials WHERE id=1"
        ).fetchone()
        print(f"auth_state: {state}")
        print(f"access_token_expires_at: {repository.get_access_expiry(conn)}")
        print(f"session_last_refreshed: {row['session_last_refreshed']}")
    if owns_conn:
        conn.close()
```

Leave `main()` and the `init-fpl` / `auth-status` subparser registrations + dispatch unchanged.

- [x] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -v`
Expected: 5 passed.

- [x] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 154 passed. `cli.py` no longer imports `fpl_login`; `fpl_login.py` + `test_fpl_login.py` still present and passing.

- [x] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_init_fpl.py
git commit -m "feat: init-fpl refresh-token paste flow; auth-status shows expiry"
```

---

### Task 5: Delete the orphaned programmatic-login code

**Files:** Delete `src/auth/fpl_login.py`, `tests/test_fpl_login.py`; Modify `src/data/repository.py`, `tests/test_repository.py`

- [x] **Step 1: Confirm `fpl_login` is fully orphaned**

Run: `grep -rn "fpl_login" src/ tests/`
Expected: matches only inside `src/auth/fpl_login.py` and `tests/test_fpl_login.py`. If anything else matches, STOP.

- [x] **Step 2: Delete the dead module + tests**

```bash
git rm src/auth/fpl_login.py tests/test_fpl_login.py
```

- [x] **Step 3: Remove `increment_relogin_failures`** — in `src/data/repository.py`, delete the entire function:

```python
def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures=COALESCE(relogin_failures, 0) + 1"
    )
    conn.commit()
    return conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()["relogin_failures"]
```
Leave `get_auth_state`, `set_auth_state`, `mark_session_ok` in place.

- [x] **Step 4: Update `tests/test_repository.py`** — delete the entire `test_increment_relogin_failures` function:

```python
def test_increment_relogin_failures(db):
    from src.data import repository
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 2
```
Then replace the entire `test_mark_session_ok_resets` function with:

```python
def test_mark_session_ok_resets(db):
    from src.data import repository
    repository.set_auth_state(db, "expired")
    repository.mark_session_ok(db)
    assert repository.get_auth_state(db) == "active"
```

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 149 passed, 0 failed. (153 after Task 3, +1 in Task 4 = 154; −4 deleted `test_fpl_login` −1 removed `test_increment_relogin_failures` = 149.)

- [x] **Step 6: Verify no dangling references + CLI loads**

```bash
grep -rn "fpl_login\|increment_relogin_failures\|FPLLoginError\|SessionFrozen\|ReloginFailed\|login_fn" src/ tests/
.venv/bin/fpl-autopilot --help
```
Expected: grep returns nothing; `--help` lists `init-fpl` and `auth-status`.

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete dead programmatic-login code (fpl_login, relogin counter)"
```

---

## Self-Review

**Spec coverage:**
- Refresh-token grant (`POST /as/token`, `client_id`, `grant_type=refresh_token`) → Task 3 `refresh_access_token`.
- `X-Api-Authorization: Bearer` authed session → Task 3 `_authed_session`.
- `/me` + team-id validation → Task 3 `validate_token`, used by Task 4 `init-fpl`.
- Access-token cache + auto-refresh + rotation storage → Task 3 `ensure_session` + `store_tokens`.
- New columns + migration → Task 1; whitelist + expiry helpers → Task 2.
- `init-fpl` paste-refresh-token flow that proves the chain at setup; clean network/OAuth error messages → Task 4.
- `auth-status` shows expiry → Task 4.
- Delete dead programmatic-login code; dormant columns kept → Task 5.
- Exceptions carry no token; tokens never echoed → Task 3 (static/OAuth-code messages), Task 4 test asserts tokens absent from output.
- Tests fixtures-only, throwaway secrets → `_FakeTokenSession`/`_FakeMeSession`, `FPL_REFRESH_TOKEN` env, `throwaway-*`.

**Deviation from spec (intentional):** `ensure_session` omits the `expected_team_id` parameter the spec sketch listed — it isn't used on the cached path and team-id is validated once at `init-fpl`, so including it would be an unused parameter. Documented here.

**Placeholder scan:** none — every code step is complete; every run step has a command + expected count. Intermediate suite counts (147 → 149 → 153 → 154 → 149) are stated per task.

**Type consistency:** `refresh_access_token(refresh_token, *, session=None)->dict`, `validate_token(access_token, *, expected_team_id, session=None)->int`, `store_tokens(conn, key, *, refresh_token, access_token, expires_at)`, `ensure_session(conn, key, *, refresh_session=None)`, `_authed_session(access_token)` are consistent across Task 3 (definitions + tests) and Task 4 (`_init_fpl_cli` calls `refresh_access_token`/`validate_token`/`store_tokens`). Exceptions `SessionNotInitialized`/`SessionExpired`/`TokenRefreshError`/`SessionValidationError` defined in Task 3, caught in Task 4. Repository `set_access_expiry`/`get_access_expiry` + token-column whitelist defined in Task 2, used in Task 3.
