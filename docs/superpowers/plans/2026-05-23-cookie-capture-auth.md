# Cookie-Capture Auth Implementation Plan

> **⚠️ SUPERSEDED (2026-05-23)** — the FPL API uses a Bearer token, not cookies. Do not execute
> this plan. The replacement will be written from `2026-05-23-token-capture-auth-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the (now-impossible) programmatic FPL login with cookie-capture: paste a browser `Cookie:` header, validate it via `/api/me/`, store it encrypted, and verify-before-use.

**Architecture:** Consolidate the auth-session concern into `src/auth/session.py` (parse, validate, store, ensure), rework `_init_fpl_cli` into a paste flow, then delete the dead programmatic-login code (`fpl_login.py`, the re-login/freeze logic, `increment_relogin_failures`). The 3-task order keeps the suite green at every step: rebuild `session.py` first, rework `cli.py` second, delete orphaned code last.

**Tech Stack:** Python 3.11+, `requests`, `cryptography` (Fernet via `src/auth/crypto.py`), raw `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-cookie-capture-auth-design.md`

**Baseline:** suite is green at 146 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/auth/session.py` | Rewrite | `parse_cookie_header`, `validate_cookies`, `store_cookies`, `ensure_session`, exceptions, `_session_from_cookies`, constants |
| `tests/test_session.py` | Rewrite | cookie-flow tests (fixtures only) |
| `src/cli.py` | Modify | `_init_fpl_cli` → paste flow; `_auth_status_cli` → drop `relogin_failures` line |
| `tests/test_cli_init_fpl.py` | Rewrite | cookie-flow CLI tests |
| `src/auth/fpl_login.py` | Delete | dead programmatic login |
| `tests/test_fpl_login.py` | Delete | tests of deleted module |
| `src/data/repository.py` | Modify | remove `increment_relogin_failures` |
| `tests/test_repository.py` | Modify | remove its test; simplify `test_mark_session_ok_resets` |

Unchanged: `src/auth/crypto.py`, `src/auth/master.py`, `src/data/schema.sql`, `src/data/db.py`. The `fpl_email_encrypted`/`fpl_password_encrypted`/`relogin_failures` columns stay (dormant).

---

### Task 1: Rebuild `src/auth/session.py` for cookie-capture

**Files:**
- Rewrite: `src/auth/session.py`
- Rewrite: `tests/test_session.py`

This task replaces the module wholesale — the old re-login/freeze design and its `fpl_login` import are removed and the verify-only cookie design takes their place. Nothing in non-test code imports `session.py` yet, so this is self-contained.

- [ ] **Step 1: Write the failing tests** — overwrite `tests/test_session.py` with EXACTLY:

```python
import json
import pytest
import requests
from src.auth import session, master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _key(tmp_path):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    return master.init_master_password("throwaway-master-12", s, v)


def test_parse_cookie_header_basic():
    assert session.parse_cookie_header("a=1; b=2") == {"a": "1", "b": "2"}


def test_parse_cookie_header_keeps_equals_in_value():
    out = session.parse_cookie_header("pl_profile=ab=cd; csrftoken=tok")
    assert out["pl_profile"] == "ab=cd"
    assert out["csrftoken"] == "tok"


def test_parse_cookie_header_strips_prefix():
    assert session.parse_cookie_header("Cookie: a=1") == {"a": "1"}


def test_parse_cookie_header_empty_raises():
    with pytest.raises(session.SessionInvalidCookie):
        session.parse_cookie_header("   ")


def test_validate_cookies_ok():
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    assert session.validate_cookies({"pl_profile": "x"}, expected_team_id=3122849, session=fake) == 3122849


def test_validate_cookies_not_authenticated():
    fake = _FakeSession(me_payload={"player": None})
    with pytest.raises(session.SessionInvalidCookie):
        session.validate_cookies({"pl_profile": "x"}, expected_team_id=3122849, session=fake)


def test_validate_cookies_team_mismatch():
    fake = _FakeSession(me_payload={"player": {"entry": 999}})
    with pytest.raises(session.SessionInvalidCookie):
        session.validate_cookies({"pl_profile": "x"}, expected_team_id=3122849, session=fake)


def test_store_cookies_roundtrip(tmp_path, db):
    key = _key(tmp_path)
    session.store_cookies(db, key, {"pl_profile": "abc", "csrftoken": "tok"})
    stored = json.loads(crypto.decrypt(key, repository.get_encrypted(db, "session_cookie_encrypted")))
    assert stored == {"pl_profile": "abc", "csrftoken": "tok"}
    assert crypto.decrypt(key, repository.get_encrypted(db, "csrf_token_encrypted")) == "tok"
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_valid(tmp_path, db):
    key = _key(tmp_path)
    session.store_cookies(db, key, {"pl_profile": "abc"})
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    out = session.ensure_session(db, key, expected_team_id=3122849, session=fake)
    assert out is fake
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_not_initialized(tmp_path, db):
    key = _key(tmp_path)
    with pytest.raises(session.SessionNotInitialized):
        session.ensure_session(db, key, expected_team_id=3122849, session=_FakeSession(me_payload={}))


def test_ensure_session_expired(tmp_path, db):
    key = _key(tmp_path)
    session.store_cookies(db, key, {"pl_profile": "stale"})
    fake = _FakeSession(me_payload={"player": None})
    with pytest.raises(session.SessionExpired):
        session.ensure_session(db, key, expected_team_id=3122849, session=fake)
    assert repository.get_auth_state(db) == "expired"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: FAIL — `AttributeError: module 'src.auth.session' has no attribute 'parse_cookie_header'` (the old module has no such function).

- [ ] **Step 3: Rewrite the module** — overwrite `src/auth/session.py` with EXACTLY:

```python
import json
import logging
import requests
from src.data.fpl_client import USER_AGENT
from src.auth.crypto import decrypt, encrypt
from src.data import repository

ME_URL = "https://fantasy.premierleague.com/api/me/"
TIMEOUT = 10
log = logging.getLogger(__name__)


class SessionError(Exception):
    """Base for session failures. Never carries the cookie value."""


class SessionNotInitialized(SessionError):
    """No stored FPL session — run init-fpl."""


class SessionExpired(SessionError):
    """Stored session is no longer authenticated — re-run init-fpl with a fresh cookie."""


class SessionInvalidCookie(SessionError):
    """A pasted cookie failed validation (not authenticated, or wrong team)."""


def parse_cookie_header(header):
    """Parse a browser 'Cookie:' header into a name->value dict."""
    header = header.strip()
    if header.lower().startswith("cookie:"):
        header = header[len("cookie:"):].strip()
    cookies = {}
    for piece in header.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            cookies[name] = value
    if not cookies:
        raise SessionInvalidCookie("no cookies found in pasted header")
    return cookies


def _session_from_cookies(cookies):
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    for name, value in cookies.items():
        s.cookies.set(name, value)
    return s


def validate_cookies(cookies, *, expected_team_id, session=None):
    """GET /me with the cookies; return the authenticated entry id or raise SessionInvalidCookie."""
    session = session or _session_from_cookies(cookies)
    me = session.get(ME_URL, timeout=TIMEOUT)
    player = (me.json() or {}).get("player") if me.status_code == 200 else None
    if not player or "entry" not in player:
        raise SessionInvalidCookie("cookie is not authenticated")
    entry = player["entry"]
    if entry != expected_team_id:
        raise SessionInvalidCookie(
            f"cookie authenticates entry {entry}, not configured team {expected_team_id}")
    return entry


def store_cookies(conn, key, cookies):
    repository.set_encrypted(conn, "session_cookie_encrypted", encrypt(key, json.dumps(cookies)))
    repository.set_encrypted(conn, "csrf_token_encrypted", encrypt(key, cookies.get("csrftoken", "")))
    repository.touch_session_refreshed(conn)
    repository.mark_session_ok(conn)


def ensure_session(conn, key, *, expected_team_id, session=None):
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
    repository.set_auth_state(conn, "expired")
    raise SessionExpired("FPL session expired; re-run init-fpl with a fresh cookie")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: green. `session.py` no longer imports `fpl_login`; `cli.py` still uses `fpl_login` (which still exists), so everything else is unaffected. (Suite count is 151 here: the old `test_session.py` had 6 tests, the new one has 11.)

- [ ] **Step 6: Commit**

```bash
git add src/auth/session.py tests/test_session.py
git commit -m "feat: rebuild session.py for cookie-capture (parse/validate/store/ensure)"
```

---

### Task 2: Rework `_init_fpl_cli` into a paste flow

**Files:**
- Modify: `src/cli.py` (replace `_init_fpl_cli` and `_auth_status_cli` function bodies)
- Rewrite: `tests/test_cli_init_fpl.py`

- [ ] **Step 1: Write the failing tests** — overwrite `tests/test_cli_init_fpl.py` with EXACTLY:

```python
import json
import requests
from src import cli
from src.auth import master, crypto, session as auth_session
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _setup_master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_init_fpl_stores_cookie(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_COOKIE", "pl_profile=abc; csrftoken=tok")
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, session=fake)
    key = master.load_key("throwaway-master-12", s, v)
    stored = json.loads(crypto.decrypt(key, repository.get_encrypted(db, "session_cookie_encrypted")))
    assert stored == {"pl_profile": "abc", "csrftoken": "tok"}
    assert crypto.decrypt(key, repository.get_encrypted(db, "csrf_token_encrypted")) == "tok"
    assert repository.get_auth_state(db) == "active"
    out = capsys.readouterr().out
    assert "3122849" in out
    assert "abc" not in out  # cookie value never echoed


def test_init_fpl_rejects_unauthenticated_cookie(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_COOKIE", "pl_profile=stale")
    fake = _FakeSession(me_payload={"player": None})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, session=fake)
    assert repository.get_encrypted(db, "session_cookie_encrypted") is None  # nothing stored
    assert "rejected" in capsys.readouterr().out.lower()


def test_init_fpl_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    monkeypatch.setenv("FPL_COOKIE", "pl_profile=abc")
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, session=_FakeSession(me_payload={}))
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"] == 0


def test_init_fpl_clears_expired(tmp_path, monkeypatch, db):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_COOKIE", "pl_profile=fresh")
    repository.set_auth_state(db, "expired")  # pretend the session had expired
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, session=fake)
    assert repository.get_auth_state(db) == "active"


def test_auth_status_cli(db, capsys):
    repository.set_auth_state(db, "active")
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "active" in out
    assert "auth_state" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -v`
Expected: FAIL — the old `_init_fpl_cli` has no `session=` parameter (`TypeError: _init_fpl_cli() got an unexpected keyword argument 'session'`).

- [ ] **Step 3: Replace the two CLI functions**

In `src/cli.py`, replace the ENTIRE `_init_fpl_cli` function with:

```python
def _init_fpl_cli(conn=None, salt_path=None, verify_path=None, session=None):
    import os
    import requests
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
    cookie_header = os.getenv("FPL_COOKIE") or input("Paste FPL Cookie header: ")
    try:
        cookies = auth_session.parse_cookie_header(cookie_header)
        entry = auth_session.validate_cookies(cookies, expected_team_id=cfg_team_id(), session=session)
    except auth_session.SessionInvalidCookie as exc:
        print(f"Cookie rejected: {exc}")
        return
    except requests.RequestException:
        print("Couldn't reach FPL to validate the cookie; check your connection.")
        return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    auth_session.store_cookies(conn, key, cookies)
    if owns_conn:
        conn.close()
    print(f"Authenticated as entry {entry}; session stored.")
```

Then replace the ENTIRE `_auth_status_cli` function with (drops the `relogin_failures` line):

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
        print(f"session_last_refreshed: {row['session_last_refreshed']}")
    if owns_conn:
        conn.close()
```

Leave `main()` and the `init-fpl` / `auth-status` subparser registrations and dispatch branches unchanged — `_init_fpl_cli()` and `_auth_status_cli()` are still called with no args.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: green. `cli.py` no longer imports `fpl_login`; `fpl_login.py` and `test_fpl_login.py` still exist and pass.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli_init_fpl.py
git commit -m "feat: init-fpl cookie-paste flow; trim auth-status"
```

---

### Task 3: Delete the orphaned programmatic-login code

**Files:**
- Delete: `src/auth/fpl_login.py`
- Delete: `tests/test_fpl_login.py`
- Modify: `src/data/repository.py` (remove `increment_relogin_failures`)
- Modify: `tests/test_repository.py` (remove its test; simplify `test_mark_session_ok_resets`)

After Tasks 1–2, nothing imports `fpl_login`, and `ensure_session` no longer calls `increment_relogin_failures`, so both are dead.

- [ ] **Step 1: Confirm `fpl_login` is fully orphaned**

Run: `grep -rn "fpl_login" src/ tests/`
Expected: only matches inside `src/auth/fpl_login.py` and `tests/test_fpl_login.py` themselves. If anything else matches, STOP — Task 1 or 2 left a reference.

- [ ] **Step 2: Delete the dead module and its tests**

```bash
git rm src/auth/fpl_login.py tests/test_fpl_login.py
```

- [ ] **Step 3: Remove `increment_relogin_failures`**

In `src/data/repository.py`, delete the entire `increment_relogin_failures` function:

```python
def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures=COALESCE(relogin_failures, 0) + 1"
    )
    conn.commit()
    return conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()["relogin_failures"]
```

Leave `get_auth_state`, `set_auth_state`, and `mark_session_ok` in place.

- [ ] **Step 4: Update `tests/test_repository.py`**

Delete the entire `test_increment_relogin_failures` function:

```python
def test_increment_relogin_failures(db):
    from src.data import repository
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 2
```

Then replace the entire `test_mark_session_ok_resets` function (it used the removed helper and the retired `frozen` state) with:

```python
def test_mark_session_ok_resets(db):
    from src.data import repository
    repository.set_auth_state(db, "expired")
    repository.mark_session_ok(db)
    assert repository.get_auth_state(db) == "active"
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 147 passed, 0 failed. (151 after Task 1 − 4 deleted `test_fpl_login` tests = 147; `test_repository` loses 1 test but `test_cli_init_fpl` gained 1 vs the old file, netting 147.)

- [ ] **Step 6: Verify no dangling references and the CLI still loads**

```bash
grep -rn "fpl_login\|increment_relogin_failures\|FPLLoginError\|SessionFrozen\|ReloginFailed" src/ tests/
.venv/bin/fpl-autopilot --help
```
Expected: the grep returns nothing; `--help` lists `init-fpl` and `auth-status`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete dead programmatic-login code (fpl_login, relogin-failure counter)"
```

---

## Self-Review

**Spec coverage:**
- Full Cookie-header paste + parse → Task 1 `parse_cookie_header`, Task 2 `_init_fpl_cli`.
- `/me` validation + team-id ownership → Task 1 `validate_cookies`.
- Encrypted storage + csrftoken extraction → Task 1 `store_cookies`.
- Verify-before-use, `active/expired` state → Task 1 `ensure_session`.
- `init-fpl` paste flow + clean network-error message (2.1b carryover) → Task 2.
- `auth-status` drops `relogin_failures` → Task 2.
- Delete `fpl_login.py`/tests; remove re-login/freeze + `increment_relogin_failures` → Tasks 1 (session re-login removed) + 3.
- Dormant columns, no migration → schema/db untouched.
- Exceptions carry no secret; cookie never logged → static-text exceptions; Task 2 test asserts cookie not echoed.
- Tests fixtures-only, throwaway secrets → `_FakeSession`, `FPL_COOKIE` env, `throwaway-master-12`.

**Placeholder scan:** none — every code step shows complete content; every run step has a command and expected result. The two intermediate suite counts (151 after Task 1, 147 after Task 3) are explained.

**Type consistency:** `parse_cookie_header(header)->dict`, `validate_cookies(cookies, *, expected_team_id, session=None)->int`, `store_cookies(conn, key, cookies)`, `ensure_session(conn, key, *, expected_team_id, session=None)` are consistent across Task 1 (definition), Task 1 tests, and Task 2 (`_init_fpl_cli` calls). `_init_fpl_cli(conn=None, salt_path=None, verify_path=None, session=None)` matches its Task 2 test calls. Exceptions `SessionNotInitialized`/`SessionExpired`/`SessionInvalidCookie` defined in Task 1, raised/caught consistently in Tasks 1–2.
