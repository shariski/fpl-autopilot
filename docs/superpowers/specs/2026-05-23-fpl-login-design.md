# FPL Login + `init-fpl` — Design (Phase 2.1b)

**Status:** approved 2026-05-23
**Slice:** Phase 2.1b (Decision Automation — auth/session)
**Depends on:** 2.1a auth/crypto foundation (`src/auth/crypto.py`, `src/auth/master.py`, encrypted credential store)

## Goal

Obtain, validate, and store **one** working authenticated FPL session via programmatic
(email + password) login, exposed through an `init-fpl` CLI command that the **user** runs
themselves. Getting cookies is not enough: the session must be proven valid *for the
configured account* before it is stored.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Login method | **Programmatic** (store email + password, POST to the FPL login endpoint) | Matches the existing `credentials` schema; enables unattended re-login required by deadguard/auto-exec (2.1c, Phase 2). |
| Success criterion | **Login + `/api/me/` + team-id ownership match** | Strictest gate before any future auto-execution touches the account. |
| Code placement | **Dedicated `src/auth/fpl_login.py`** | Keeps the read-only Data-Layer `FPLClient` clean; auth/write is not a Data-Layer concern (architecture boundary, CLAUDE.md B2). |
| Login retry | **None** — single attempt, fail loud | Hammering a bot-protected login endpoint is the account-flag risk (R3). Re-login *policy* belongs to 2.1c. |
| Cookie storage | **Full cookie jar** as a JSON blob | 2.2 (Action Executor) can rebuild whatever cookies FPL requires, not just `pl_profile`. |

## Architecture & placement

```
src/auth/fpl_login.py   ← NEW: login() + validation (pure auth concern)
src/cli.py              ← extend: init-fpl subcommand (orchestration)
src/auth/master.py      ← reuse: get_master_key()
src/auth/crypto.py      ← reuse: encrypt()
src/data/repository.py  ← reuse set_encrypted(); ADD touch_session_refreshed()
```

No change to `docs/decision-engine.md` (this is auth, not decision logic — CLAUDE.md B4 not
triggered). No change to `FPLClient`.

## The login flow — `src/auth/fpl_login.py`

```python
class FPLLoginError(Exception):
    """Login or validation failure. Never carries the password or cookie values."""


@dataclass
class LoginResult:
    cookies: dict[str, str]   # serialized cookie jar, for 2.2 to rebuild a session
    csrf: str | None          # csrftoken value, for future write POSTs
    entry_id: int             # the authenticated account's team id


def login(email, password, *, expected_team_id, session=None) -> LoginResult:
    ...
```

`session` is an injectable `requests.Session` (default: a real session with the realistic
User-Agent reused from `src/data/fpl_client.py`; in tests: a fake returning canned responses).

Steps inside `login()`:

1. **POST** `https://users.premierleague.com/accounts/login/` with form data:
   `{"login": email, "password": password, "app": "plfpl-web",
   "redirect_uri": "https://fantasy.premierleague.com/a/login"}`.
   Cookies persist on the session. The `csrftoken` value is read from the post-login cookie
   jar for storage.
   *(Primary implementation: POST directly — the community-documented flow. A preliminary GET
   to the login page to seed `csrftoken` + `csrfmiddlewaretoken` is a fallback the plan adds
   only if the live POST is rejected without it.)*
2. **Best-effort early failure.** If the login response makes failure obvious (a `state=fail`
   redirect, or landing back on the login page), raise
   `FPLLoginError("login failed — check FPL email/password")` immediately. No password in the
   message. This is a fast-path nicety — step 3 is the authoritative check, so step 2 need not
   catch every failure mode.
3. **Validate (authoritative).** Authenticated **GET**
   `https://fantasy.premierleague.com/api/me/`. Schema-assert that `player.entry` exists (fail
   loud on schema drift — B6). A non-authenticated response (no `player`, or 401/403) →
   `raise FPLLoginError("login appeared to succeed but session is not authenticated")`. Bad
   credentials that slipped past step 2 are caught here, because `/me` will not be authenticated.
4. **Ownership check.** `if me["player"]["entry"] != expected_team_id:`
   `raise FPLLoginError("authenticated as entry <X> but config team_id is <Y>")`.
5. **Return** `LoginResult(cookies=<jar as name→value dict>, csrf=<csrftoken or None>,
   entry_id=<player.entry>)`.

**No automatic retry.** A network error, timeout, or bot-protection 403 fails loud.

## `init-fpl` orchestration — `src/cli.py`

```
init-fpl:
  1. if not master.is_initialized():
         print "Master password not set — run `fpl-autopilot init-master-password` first."; return
  2. key = master.get_master_key()                      # getpass / MASTER_PASSWORD env
  3. email = os.getenv("FPL_EMAIL") or input("FPL email: ")
     password = os.getenv("FPL_PASSWORD") or getpass("FPL password: ")
  4. result = fpl_login.login(email, password, expected_team_id=config.team_id())
  5. with a DB connection:
         set_encrypted(conn, "fpl_email_encrypted",     encrypt(key, email))
         set_encrypted(conn, "fpl_password_encrypted",  encrypt(key, password))
         set_encrypted(conn, "session_cookie_encrypted", encrypt(key, json.dumps(result.cookies)))
         set_encrypted(conn, "csrf_token_encrypted",     encrypt(key, result.csrf or ""))
         touch_session_refreshed(conn)                  # session_last_refreshed = now (UTC)
  6. print f"Authenticated as entry {result.entry_id}; session stored."   # no secrets
  on FPLLoginError as e:
         print f"FPL login failed: {e}"                 # message carries no secret
         return                                          # store NOTHING
```

Email and password are stored (encrypted) because unattended re-login (2.1c) needs them.
`init-fpl` requires `init-master-password` to have run first (it needs the master key to
encrypt) and prompts for the master password to load that key.

## Repository addition

```python
def touch_session_refreshed(conn):
    """Set credentials.session_last_refreshed to the current UTC time (row id=1)."""
    conn.execute(
        "INSERT INTO credentials (id, session_last_refreshed) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET session_last_refreshed=excluded.session_last_refreshed",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
```

Static SQL, parameterized value — no injection surface (consistent with `set_encrypted`).

## Error handling & security (B7)

| Failure | Behavior |
|---------|----------|
| Bad credentials | `FPLLoginError`, actionable message, **nothing stored** |
| `/me` not authenticated | `FPLLoginError("session not authenticated")` |
| Team-id mismatch | `FPLLoginError("authenticated as entry X but config team_id is Y")` |
| Network / timeout / 403 | propagate loud; **no retry** |
| Master password not set | actionable error pointing at `init-master-password`; nothing stored |

- Email, password, and the cookie jar are encrypted at rest with the master key.
- Never log the password, email, or full cookies. `FPLLoginError` never carries secret values
  (mirrors `MasterPasswordError`).
- Realistic User-Agent on every request; the login is 2 requests total, well within ≤1 req/s (B6).
- Schema-assert the `/me` shape; fail loud on drift (B6).

## Testing — fixtures only, never live

Per the project security constraint, **no test makes a live FPL call**. All tests inject a
**fake session** that returns canned responses for the login POST and the `/me` GET. The user
runs the live `init-fpl` themselves; the agent never runs the live login (R3 account-flag risk).
All tests use **throwaway** passwords/emails.

1. `test_login_success` — fake session: login OK, `/me` returns `entry == expected` →
   `LoginResult` with correct `entry_id`, cookies, csrf.
2. `test_login_bad_credentials` — login signals failure → `FPLLoginError`; assert the
   password string is **absent** from the exception message.
3. `test_login_team_id_mismatch` — `/me` returns a different `entry` → `FPLLoginError`.
4. `test_login_not_authenticated` — `/me` returns a non-authenticated payload (no `player`) →
   `FPLLoginError`.
5. `test_init_fpl_cli_stores_encrypted` — monkeypatch `fpl_login.login` to return a
   `LoginResult`; monkeypatch `getpass`/`input` and the master key; tmp DB. Assert the
   `credentials` row has all four encrypted blobs **and they decrypt back to the inputs**
   (throwaway master password), `session_last_refreshed` is set, and the password is never echoed.
6. `test_init_fpl_requires_master_password` — no master password set → actionable error,
   **nothing stored**.

## Scope boundary

- **IN (this slice):** `login()`, validation (`/me` + team-id), `init-fpl` CLI, encrypted
  storage of email/password/cookies/csrf, `session_last_refreshed`.
- **OUT → 2.1c:** expiry detection, automatic re-login, freeze-after-2-failures, scheduled refresh.
- **OUT → 2.2 (Action Executor):** using the stored session to execute transfers/captain changes.

## Definition of done (CLAUDE.md B14)

- `login()` and `init-fpl` behave as specified above.
- All six tests pass; the existing suite (127) stays green.
- No secret is logged anywhere; encrypted blobs round-trip in tests.
- Manual smoke check is the user running live `init-fpl` once (out of band — the agent does not run it).
