# Session Lifecycle Implementation Plan (Phase 2.1c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hand out a guaranteed-valid authenticated FPL session — verify before use, re-login transparently on expiry, and freeze auto-execution after two consecutive re-login failures.

**Architecture:** A new `src/auth/session.py` exposes `ensure_session()`: it builds a session from the encrypted stored cookies, verifies it with `GET /api/me/`, and on expiry re-logs-in via `fpl_login.login` using the decrypted stored credentials. A persistent `auth_state` + `relogin_failures` on the `credentials` row drives an active/expired/frozen state machine. The master key is passed in (scheduler wiring deferred to 2.2).

**Tech Stack:** Python 3.11+, `requests`, `cryptography` (Fernet via `src/auth/crypto.py`), raw `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-session-lifecycle-design.md`

**Baseline:** suite is green at 134 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/data/schema.sql` | add `auth_state`, `relogin_failures` to `credentials` | Modify |
| `src/data/db.py` | idempotent `_migrate_credentials` invoked from `init_db` | Modify |
| `src/data/repository.py` | `get_auth_state`, `set_auth_state`, `increment_relogin_failures`, `mark_session_ok` | Modify |
| `src/auth/session.py` | `ensure_session` + exceptions + `_persist_relogin` | Create |
| `src/cli.py` | `auth-status` command; `mark_session_ok` in `_init_fpl_cli` | Modify |
| `tests/test_db.py` | migration test | Create (append if it exists) |
| `tests/test_repository.py` | state-helper tests | Modify |
| `tests/test_session.py` | `ensure_session` tests | Create |
| `tests/test_cli_init_fpl.py` | unfreeze + auth-status tests | Modify |

Reused: `src/auth/fpl_login.py` (`login`, `FPLLoginError`, `LoginResult`, `ME_URL`, `TIMEOUT`, `USER_AGENT`), `src/auth/crypto.py` (`encrypt`, `decrypt`), `src/auth/master.py` (`init_master_password`, `load_key`), `src/data/repository.py` (`set_encrypted`, `get_encrypted`, `touch_session_refreshed`).

---

### Task 1: Schema columns + idempotent migration

**Files:**
- Modify: `src/data/schema.sql`
- Modify: `src/data/db.py`
- Test: `tests/test_db.py` (create; if it already exists, append the test function)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py` (or append the function if the file exists):

```python
import sqlite3
from src.data import db


def test_migrate_credentials_adds_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # a credentials table created BEFORE the new columns existed
    conn.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY, session_last_refreshed TIMESTAMP)")
    db._migrate_credentials(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    assert "auth_state" in cols
    assert "relogin_failures" in cols
    # idempotent: a second run is a no-op
    db._migrate_credentials(conn)
    cols_again = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    assert cols_again == cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_migrate_credentials_adds_columns -v`
Expected: FAIL — `AttributeError: module 'src.data.db' has no attribute '_migrate_credentials'`.

- [ ] **Step 3: Implement**

In `src/data/schema.sql`, change the `credentials` table's last column line. Replace:

```sql
  session_last_refreshed TIMESTAMP
);
```
with:
```sql
  session_last_refreshed TIMESTAMP,
  auth_state TEXT DEFAULT 'active',
  relogin_failures INTEGER DEFAULT 0
);
```
(Only the `credentials` table block — it is the one with `fpl_email_encrypted`.)

In `src/data/db.py`, add the migration function and call it from `init_db`. The file currently is:
```python
def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
```
Replace that with:
```python
def _migrate_credentials(conn):
    """Add auth_state / relogin_failures to an existing credentials table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    if "auth_state" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN auth_state TEXT DEFAULT 'active'")
    if "relogin_failures" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN relogin_failures INTEGER DEFAULT 0")


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate_credentials(conn)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py::test_migrate_credentials_adds_columns -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite (schema change touches every DB)**

Run: `.venv/bin/pytest -q`
Expected: 135 passed (134 + 1). The in-memory `db` fixture now creates the columns via `schema.sql`; the migration is a no-op there.

- [ ] **Step 6: Commit**

```bash
git add src/data/schema.sql src/data/db.py tests/test_db.py
git commit -m "feat: credentials auth_state/relogin_failures columns + migration"
```

---

### Task 2: Repository state helpers

**Files:**
- Modify: `src/data/repository.py`
- Test: `tests/test_repository.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repository.py`:

```python
def test_auth_state_get_set(db):
    from src.data import repository
    assert repository.get_auth_state(db) is None  # no row yet
    repository.set_auth_state(db, "frozen")
    assert repository.get_auth_state(db) == "frozen"


def test_increment_relogin_failures(db):
    from src.data import repository
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 2


def test_mark_session_ok_resets(db):
    from src.data import repository
    repository.set_auth_state(db, "frozen")
    repository.increment_relogin_failures(db)
    repository.mark_session_ok(db)
    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_repository.py -k "auth_state or relogin or mark_session" -v`
Expected: FAIL — `AttributeError: module 'src.data.repository' has no attribute 'get_auth_state'`.

- [ ] **Step 3: Implement**

Add these functions to `src/data/repository.py` immediately after `touch_session_refreshed`:

```python
def get_auth_state(conn):
    row = conn.execute("SELECT auth_state FROM credentials WHERE id=1").fetchone()
    return row["auth_state"] if row else None


def set_auth_state(conn, state):
    conn.execute(
        "INSERT INTO credentials (id, auth_state) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET auth_state=excluded.auth_state",
        (state,),
    )
    conn.commit()


def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures=COALESCE(relogin_failures, 0) + 1"
    )
    conn.commit()
    return conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()["relogin_failures"]


def mark_session_ok(conn):
    conn.execute(
        "INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0) "
        "ON CONFLICT(id) DO UPDATE SET auth_state='active', relogin_failures=0"
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_repository.py -k "auth_state or relogin or mark_session" -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "feat: credential auth-state repository helpers"
```

---

### Task 3: `ensure_session` — verify, guards, happy path

**Files:**
- Create: `src/auth/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session.py`:

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
    """Returns a canned /me response; ignores cookies (logic is what we test)."""

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


def _store_cookies(db, key, cookies):
    repository.set_encrypted(db, "session_cookie_encrypted", crypto.encrypt(key, json.dumps(cookies)))


def test_ensure_session_valid(tmp_path, db):
    key = _key(tmp_path)
    _store_cookies(db, key, {"pl_profile": "abc"})
    repository.mark_session_ok(db)
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    called = []
    out = session.ensure_session(db, key, expected_team_id=3122849,
                                 login_fn=lambda *a, **k: called.append(1), session=fake)
    assert out is fake
    assert not called  # no re-login when the session is valid
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_not_initialized(tmp_path, db):
    key = _key(tmp_path)
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    with pytest.raises(session.SessionNotInitialized):
        session.ensure_session(db, key, expected_team_id=3122849, session=fake)


def test_ensure_session_frozen_refuses(tmp_path, db):
    key = _key(tmp_path)
    _store_cookies(db, key, {"pl_profile": "abc"})
    repository.set_auth_state(db, "frozen")
    called = []
    with pytest.raises(session.SessionFrozen):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=lambda *a, **k: called.append(1),
                               session=_FakeSession(me_payload={}))
    assert not called  # frozen refuses without attempting login
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'session'` / `module 'src.auth.session' has no attribute ...` (module does not exist yet).

- [ ] **Step 3: Implement**

Create `src/auth/session.py` (verify + guards + happy path; the re-login block is added in Task 4):

```python
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
```

Note: the final line is a deliberate placeholder so Task 3's three tests pass; Task 4 replaces it with the re-login logic. (The `valid`, `not_initialized`, and `frozen` tests never reach that line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/auth/session.py tests/test_session.py
git commit -m "feat: ensure_session verify + frozen/not-initialized guards"
```

---

### Task 4: `ensure_session` — re-login and freeze

**Files:**
- Modify: `src/auth/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py`:

```python
def _store_creds(db, key, cookies):
    _store_cookies(db, key, cookies)
    repository.set_encrypted(db, "fpl_email_encrypted", crypto.encrypt(key, "me@example.com"))
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "throwaway-fpl-pw"))


def test_ensure_session_relogin_ok(tmp_path, db):
    from src.auth import fpl_login
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    expired = _FakeSession(me_payload={}, me_status=200)  # no player -> expired
    fresh = fpl_login.LoginResult(cookies={"pl_profile": "fresh"}, csrf="t2", entry_id=3122849)
    out = session.ensure_session(db, key, expected_team_id=3122849,
                                 login_fn=lambda *a, **k: fresh, session=expired)
    assert isinstance(out, requests.Session)
    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0
    stored = json.loads(crypto.decrypt(key, repository.get_encrypted(db, "session_cookie_encrypted")))
    assert stored == {"pl_profile": "fresh"}


def _failing_login(*a, **k):
    from src.auth.fpl_login import FPLLoginError
    raise FPLLoginError("bad creds")


def test_ensure_session_relogin_fails_once(tmp_path, db):
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    with pytest.raises(session.ReloginFailed):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    assert repository.get_auth_state(db) == "expired"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 1


def test_ensure_session_freezes_after_two(tmp_path, db):
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    # first failed re-login
    with pytest.raises(session.ReloginFailed):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    # second consecutive failure -> frozen
    with pytest.raises(session.SessionFrozen):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    assert repository.get_auth_state(db) == "frozen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_session.py -k "relogin or freezes" -v`
Expected: FAIL — `relogin_ok` raises `SessionNotInitialized` ("session expired" placeholder) instead of returning a session; the failure tests raise `SessionNotInitialized` instead of `ReloginFailed`/`SessionFrozen`.

- [ ] **Step 3: Implement**

In `src/auth/session.py`, replace this placeholder line at the end of `ensure_session`:
```python
    raise SessionNotInitialized("session expired")  # placeholder; replaced in Task 4
```
with the re-login block:
```python
    # session expired -> attempt one re-login
    repository.set_auth_state(conn, "expired")
    email = decrypt(key, repository.get_encrypted(conn, "fpl_email_encrypted"))
    password = decrypt(key, repository.get_encrypted(conn, "fpl_password_encrypted"))
    try:
        result = login_fn(email, password, expected_team_id=expected_team_id)
    except FPLLoginError:
        failures = repository.increment_relogin_failures(conn)
        if failures >= 2:
            repository.set_auth_state(conn, "frozen")
            log.warning("FPL auto-execution frozen after %d consecutive re-login failures", failures)
            raise SessionFrozen("auto-execution frozen after repeated re-login failures")
        raise ReloginFailed("FPL re-login failed; session still expired")
    _persist_relogin(conn, key, result)
    return _session_from_cookies(result.cookies)


def _persist_relogin(conn, key, result):
    repository.set_encrypted(conn, "session_cookie_encrypted", encrypt(key, json.dumps(result.cookies)))
    repository.set_encrypted(conn, "csrf_token_encrypted", encrypt(key, result.csrf or ""))
    repository.touch_session_refreshed(conn)
    repository.mark_session_ok(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: 6 passed (3 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/auth/session.py tests/test_session.py
git commit -m "feat: ensure_session re-login + freeze-after-two"
```

---

### Task 5: `init-fpl` unfreeze + `auth-status` CLI

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_init_fpl.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_init_fpl.py`:

```python
def test_init_fpl_clears_freeze(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    monkeypatch.setenv("FPL_EMAIL", "me@example.com")
    monkeypatch.setenv("FPL_PASSWORD", "throwaway-fpl-pw")
    repository.set_auth_state(db, "frozen")  # pretend we were frozen

    fake = fpl_login.LoginResult(cookies={"pl_profile": "abc"}, csrf="tok", entry_id=3122849)
    cli._init_fpl_cli(conn=db, login_fn=lambda *a, **k: fake, salt_path=s, verify_path=v)

    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0


def test_auth_status_cli(db, capsys):
    repository.set_auth_state(db, "active")
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "active" in out
    assert "relogin_failures" in out
```

(The existing `from src.data import repository` import at the top of this test file is reused; if it is missing, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -k "clears_freeze or auth_status" -v`
Expected: FAIL — `clears_freeze` leaves state `frozen` (init-fpl does not reset it yet); `auth_status` fails with `AttributeError: module 'src.cli' has no attribute '_auth_status_cli'`.

- [ ] **Step 3: Implement**

In `src/cli.py`, in `_init_fpl_cli`, find:
```python
    repository.touch_session_refreshed(conn)
    if owns_conn:
        conn.close()
```
and insert `mark_session_ok` before the close:
```python
    repository.touch_session_refreshed(conn)
    repository.mark_session_ok(conn)
    if owns_conn:
        conn.close()
```

Then add the `auth-status` function immediately after `_init_fpl_cli` (before `serve`):
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
            "SELECT relogin_failures, session_last_refreshed FROM credentials WHERE id=1"
        ).fetchone()
        print(f"auth_state: {state}")
        print(f"relogin_failures: {row['relogin_failures']}")
        print(f"session_last_refreshed: {row['session_last_refreshed']}")
    if owns_conn:
        conn.close()
```

- [ ] **Step 4: Register the subcommand**

In `main()`, find:
```python
    sub.add_parser("init-fpl", help="log in to FPL and store the encrypted session")
```
Immediately AFTER it add:
```python
    sub.add_parser("auth-status", help="show stored FPL session state (no secrets)")
```
Then find:
```python
    elif args.command == "init-fpl":
        _init_fpl_cli()
```
Immediately AFTER it add:
```python
    elif args.command == "auth-status":
        _auth_status_cli()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli_init_fpl.py -v`
Expected: all pass (the two new tests plus the existing ones).

- [ ] **Step 6: Verify the full suite + CLI help**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: 146 passed; top-level help lists `auth-status`. **Do NOT run the real `auth-status` against your live DB unless you want to** — it is read-only and safe, but the tests already cover it.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_init_fpl.py
git commit -m "feat: init-fpl unfreeze + auth-status CLI"
```

---

## Self-Review

**Spec coverage:**
- Reactive verify-before-use (`/me`) → Task 3 `ensure_session`.
- Re-login on expiry using decrypted stored creds → Task 4.
- Freeze after 2 consecutive failures via persistent counter → Task 4 (`increment_relogin_failures` + `>= 2`), Task 2 (counter).
- `active/expired/frozen` state machine → Tasks 2 + 3 + 4.
- Master key passed as a parameter → `ensure_session(conn, key, ...)`, no scheduler change.
- Schema columns + idempotent migration → Task 1.
- Repository pure-state helpers; encryption stays in auth layer (`_persist_relogin` in `session.py`) → Tasks 2 + 4.
- `auth-status` CLI; `init-fpl` unfreeze → Task 5.
- Alerting = durable `frozen` state + `log.warning`; Telegram/activity_log deferred → Task 4 (`log.warning`, `set_auth_state("frozen")`).
- Transient network error is not expiry → `ensure_session` only catches `FPLLoginError`; a `requests` exception on the `/me` GET or during re-login propagates (verified by the design: no broad `except`).
- Exceptions carry no secrets → all raise static text only.
- Tests fixtures-only, throwaway secrets → `_FakeSession`, injected `login_fn`, in-memory `db`, `throwaway-*`.

**Placeholder scan:** the single intentional placeholder line in Task 3's `ensure_session` is explicitly called out and replaced in Task 4 step 3 — it exists so Task 3's tests pass under TDD and is not a plan gap. No other placeholders; every code step shows full code; every run step shows the command and expected result.

**Type consistency:** `ensure_session(conn, key, *, expected_team_id, login_fn=None, session=None)` is consistent across Tasks 3 and 4 and the tests. Exceptions `SessionError`/`SessionNotInitialized`/`SessionFrozen`/`ReloginFailed` defined in Task 3, used in Task 4. `LoginResult(cookies, csrf, entry_id)` matches `fpl_login` (2.1b). Repository helpers `get_auth_state`/`set_auth_state`/`increment_relogin_failures`/`mark_session_ok` defined in Task 2 and used in Tasks 3, 4, 5. `_persist_relogin` defined and used in Task 4.
