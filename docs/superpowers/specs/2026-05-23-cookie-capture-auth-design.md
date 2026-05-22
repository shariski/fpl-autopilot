# Cookie-Capture Auth — Design (replaces programmatic login)

**Status:** approved 2026-05-23
**Slice:** Phase 2.1 auth pivot (supersedes the programmatic-login mechanism from 2.1b/2.1c)
**Depends on:** 2.1a auth/crypto (`src/auth/crypto.py`, `src/auth/master.py`), the encrypted credential store

## Why this exists

Programmatic email+password login is infeasible for this account (see the `fpl-auth-reality`
memory and the findings below), so the tool authenticates by **reusing a session cookie the user
captures from their already-logged-in browser**. The browser does the hard part (PingOne DaVinci
+ Cloudflare); the tool reuses the resulting session against the open FPL API.

### Findings that forced the pivot (verified 2026-05-23)
- `users.premierleague.com` (the classic login host slice 2.1b targeted) **no longer resolves** in DNS.
- PL auth moved to `account.premierleague.com`, a **PingOne DaVinci** orchestration behind
  **Cloudflare** (login POSTs JSON to `/davinci/connections/{id}/capabilities/customHTMLTemplate`
  with rotating `interactionId`/`skEvent` flow state; a datacenter-IP request to the host gets
  HTTP 403 + `__cf_bm`). Replaying it programmatically means reverse-engineering and perpetually
  maintaining a brittle, remotely-configured flow behind a bot wall — rejected (B6 unofficial/no
  stability; R3 account-flag).
- The **authenticated FPL API is open**: `fantasy.premierleague.com/api/` runs on `openresty`
  with no Cloudflare/datadome. Unauthenticated `GET /api/me/` returns `{"player":null,...}`;
  authenticated returns a `player` with an `entry`. So a captured cookie works directly against
  the API, and validation is a simple `/me` check.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Capture format | Full `Cookie:` header, pasted; parsed into a cookie dict; `csrftoken` extracted for future writes. |
| Cleanup | Clean rework: delete the dead programmatic-login code + tests; keep `/me` validation; leave unused DB columns dormant (no schema change). |
| Expiry handling | Reactive verify-before-use; on expiry mark `expired` and raise — no auto-re-login. Recovery = re-run `init-fpl`. |
| Paste visibility | Visible (`input`), not hidden — a long multi-part string the user must see to confirm; never logged afterward. |

## Architecture & placement

Consolidate the auth-session concern into a single module and delete the now-misnamed login module.

```
src/auth/session.py     ← parse_cookie_header, validate_cookies, store_cookies,
                          ensure_session, exceptions, _session_from_cookies, constants
src/auth/fpl_login.py   ← DELETE (login() POSTs to a dead host; /me validation moves to session.py)
tests/test_fpl_login.py ← DELETE
src/cli.py              ← _init_fpl_cli reworked to paste-a-cookie; auth-status trimmed
src/data/repository.py  ← remove increment_relogin_failures (now dead); keep get/set_auth_state, mark_session_ok
```

Unchanged: `src/auth/crypto.py`, `src/auth/master.py`, `src/data/schema.sql` (columns stay),
`src/data/db.py`. `USER_AGENT` is imported from `src/data/fpl_client.py`; `ME_URL` and `TIMEOUT`
move into `session.py`. No `decision-engine.md` change (B4 not triggered).

## Module: `src/auth/session.py`

```python
import json, logging, requests
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
```

### `parse_cookie_header(header) -> dict[str, str]`
Parse a browser `Cookie:` header into name→value pairs.
- Split on `;`, each piece on the **first** `=` (cookie values can contain `=`, e.g. base64/JWT).
- Strip surrounding whitespace from names and values; skip empty pieces.
- Tolerate a leading `Cookie:` prefix if the user copies it (strip it).
- Raise `SessionInvalidCookie("no cookies found in pasted header")` if the result is empty.

### `_session_from_cookies(cookies) -> requests.Session`
Build a `requests.Session` with `USER_AGENT` and the given cookies set on the jar.

### `validate_cookies(cookies, *, expected_team_id, session=None) -> int`
- `session = session or _session_from_cookies(cookies)`.
- `me = session.get(ME_URL, timeout=TIMEOUT)`.
- If status != 200 or `me.json().get("player")` is falsy → `raise SessionInvalidCookie("cookie is not authenticated")`.
- `entry = player.get("entry")`; if `entry != expected_team_id` →
  `raise SessionInvalidCookie(f"cookie authenticates entry {entry}, not configured team {expected_team_id}")`.
- Return `entry`. A `requests.RequestException` propagates (caller decides; not swallowed).

### `store_cookies(conn, key, cookies)`
- `repository.set_encrypted(conn, "session_cookie_encrypted", encrypt(key, json.dumps(cookies)))`.
- `repository.set_encrypted(conn, "csrf_token_encrypted", encrypt(key, cookies.get("csrftoken", "")))`.
- `repository.touch_session_refreshed(conn)`; `repository.mark_session_ok(conn)` (auth_state=active).

### `ensure_session(conn, key, *, expected_team_id, session=None) -> requests.Session`
```
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
A `requests.RequestException` on the `/me` GET propagates — a network blip is not expiry.

## CLI: `_init_fpl_cli` (reworked)

```
def _init_fpl_cli(conn=None, salt_path=None, verify_path=None, session=None):
    # 1. master password gate (unchanged): if not initialized -> message; else load key
    # 2. cookie_header = os.getenv("FPL_COOKIE") or input("Paste FPL Cookie header: ")
    # 3. cookies = session_mod.parse_cookie_header(cookie_header)
    # 4. try:
    #        entry = session_mod.validate_cookies(cookies, expected_team_id=cfg_team_id(), session=session)
    #    except session_mod.SessionInvalidCookie as e:  print(f"Cookie rejected: {e}"); return
    #    except requests.RequestException:              print("Couldn't reach FPL to validate the cookie; check your connection."); return
    # 5. open conn if needed; init_db; session_mod.store_cookies(conn, key, cookies)
    # 6. print(f"Authenticated as entry {entry}; session stored.")
```
Removed: `FPL_EMAIL`/`FPL_PASSWORD` prompts, `login_fn`, `FPLLoginError` handling. The `session`
parameter is the validation session, injectable for tests (defaults to one built from the pasted
cookies). The clean network-error message (step 4) resolves the 2.1b carryover (the raw traceback
the user saw).

`auth-status` drops the `relogin_failures` line; it prints `auth_state` and
`session_last_refreshed` only.

## Removed code (clean rework)

- `src/auth/fpl_login.py`, `tests/test_fpl_login.py` — deleted.
- `src/auth/session.py`: the re-login block, `login_fn` param, `SessionFrozen`, `ReloginFailed`,
  `_persist_relogin` — replaced by the verify-only `ensure_session` + `store_cookies`.
- `src/data/repository.py`: `increment_relogin_failures` + its test.
- `src/cli.py`: email/password prompts, `login_fn`, `FPLLoginError` import/handling.
- `tests/test_session.py`, `tests/test_cli_init_fpl.py`: re-login/freeze and email/password tests
  reworked to the cookie flow.

**Left dormant** (no migration): `fpl_email_encrypted`, `fpl_password_encrypted`,
`relogin_failures` columns; the `"frozen"` `auth_state` value is no longer produced.

## Error handling & security (B7)

- Cookies encrypted at rest with the master key; never logged. Exceptions carry no cookie value.
- `init-fpl` validation failure or network error → clean message, **nothing stored**.
- `validate_cookies`/`ensure_session` only treat an authenticated-negative `/me` (200 with no
  `player.entry`, or non-200) as failure; a `requests.RequestException` propagates and is **not**
  expiry.
- Realistic `USER_AGENT` on all requests (B6); `/me` shape asserted (`player.entry`), fail loud.

## Testing — fixtures only, never live

Reuse the `_Resp`/`_FakeSession` pattern. Throwaway master password; no live calls.

1. `parse_cookie_header` — `"a=1; b=2"` → `{"a":"1","b":"2"}`; value containing `=`
   (`"pl_profile=ab=cd"`) keeps `ab=cd`; leading `Cookie:` stripped; empty → `SessionInvalidCookie`;
   `csrftoken` present in the dict when supplied.
2. `validate_cookies` — authenticated `/me` with matching entry → returns entry; null player →
   `SessionInvalidCookie`; mismatched entry → `SessionInvalidCookie`.
3. `store_cookies` — round-trips: stored `session_cookie_encrypted` decrypts to the dict,
   `csrf_token_encrypted` decrypts to the csrftoken, `auth_state=active`.
4. `ensure_session` — valid → returns session, `active`; not initialized → `SessionNotInitialized`;
   expired (null player) → `SessionExpired`, state `expired`.
5. `_init_fpl_cli` (cookie flow) — `FPL_COOKIE` env + injected fake validation session →
   cookies decrypt back, `auth_state=active`, success line printed, cookie never echoed; invalid
   cookie → nothing stored; re-running after `expired` → `active` (recovery).
6. `auth-status` — prints `auth_state`, no secret.

## Scope boundary

- **IN:** cookie-header paste/parse, validation, encrypted storage, verify-before-use,
  `active/expired` state, `auth-status`, deletion of the dead programmatic-login code.
- **OUT → 2.2 (Action Executor):** wiring the master key into the running scheduler; calling
  `ensure_session` from a real execution job; using `csrftoken` for write POSTs.
- **OUT → 2.4 (Telegram):** proactively alerting the user when the session has expired before a
  deadline. This slice makes expiry *detectable* (`auth-status` / `SessionExpired`), not pushed.

## Definition of done (CLAUDE.md B14)

- `init-fpl` accepts a pasted Cookie header, validates it, and stores it encrypted; `auth-status`
  reflects `active`; `ensure_session` returns a valid session or raises `SessionExpired`.
- Dead programmatic-login code is removed; the suite is green (no orphaned references).
- No secret logged; cookies round-trip in tests.
- Manual smoke check (out of band, by the user): paste a real Cookie header into `init-fpl`,
  confirm `auth-status` shows `active`.
