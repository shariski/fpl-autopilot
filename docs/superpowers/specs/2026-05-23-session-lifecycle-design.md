# Session Lifecycle — Design (Phase 2.1c)

**Status:** approved 2026-05-23
**Slice:** Phase 2.1c (Decision Automation — auth/session)
**Depends on:** 2.1a auth/crypto, 2.1b FPL login (`src/auth/fpl_login.py`, encrypted credential store)

## Goal

Hand out a guaranteed-valid authenticated FPL session: verify it before use, re-login
transparently when it has expired, and freeze auto-execution after two consecutive re-login
failures (CLAUDE.md B7). This is the reusable session provider the Action Executor (2.2) will
consume; this slice builds the mechanism and its state machine, tested in isolation.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Expiry detection | **Reactive verify-before-use** — `GET /api/me/` before handing out the session; re-login if not authenticated. No time threshold. | FPL decides when sessions die; the deadguard always calls this right before acting, so a fresh session before a deadline happens naturally. Simplest robust option. |
| Master key | **Passed in as a parameter.** The long-running serve/scheduler process does NOT load it yet. | Nothing consumes unattended re-login until 2.2 (Action Executor). YAGNI — wire the key into the scheduler when the first auto-exec job lands. |
| Alerting on freeze | **Durable `auth_state='frozen'` + `log.warning`** for now. | No execution context or notification channel yet. Structured `activity_log` entry → 2.2; Telegram alert (B9) → 2.4. |
| Unfreeze | **Re-run `init-fpl`** (resets state to active). No dedicated command. | A successful manual login is the natural recovery; YAGNI. |

## Architecture & placement

```
src/auth/session.py     ← NEW: ensure_session() + state machine + exceptions
src/data/schema.sql     ← add auth_state, relogin_failures to the credentials table
src/data/db.py          ← idempotent migration: ALTER TABLE ADD COLUMN if missing
src/data/repository.py  ← credential-state read/write + store_session helpers
src/cli.py              ← auth-status command; init-fpl resets state on success
```

Reuses `src/auth/fpl_login.py` (`login`, `FPLLoginError`, `LoginResult`),
`src/auth/crypto.py` (`encrypt`, `decrypt`), and the existing credential store
(`set_encrypted`, `get_encrypted`, `touch_session_refreshed`). No change to
`docs/decision-engine.md` (B4 not triggered — this is auth, not decision logic).

## State machine

`credentials.auth_state ∈ {active, expired, frozen}`; `credentials.relogin_failures` is an
integer counter.

```
            init-fpl success
                  │
                  ▼
   ┌────────► ACTIVE ──/me not authenticated──► EXPIRED
   │            ▲                                   │
   │     re-login OK                          re-login attempt
   │   (failures = 0)                       ┌────────┴────────┐
   │            │                        success          FPLLoginError
   └────────────┘                       (→ ACTIVE)        failures += 1
                                                               │
                                                  failures >= 2 ? ──► FROZEN
                                                               │ no
                                                          (stay EXPIRED)
```

- `FROZEN` is sticky: `ensure_session` refuses without attempting a login.
- The `relogin_failures` counter persists in the DB, so "twice in a row" (B7) holds whether the
  two failures occur within one run or across two scheduled runs. A successful re-login resets
  it to 0.
- Recovery from `FROZEN`: a successful `init-fpl` sets `auth_state='active'`, `relogin_failures=0`.

## Core: `ensure_session(conn, key, *, expected_team_id) -> requests.Session`

```
state = repository.get_auth_state(conn)
if state == "frozen":
    raise SessionFrozen("auto-execution is frozen; re-run init-fpl")
if no credentials row (or no stored cookies):
    raise SessionNotInitialized("no stored FPL session; run init-fpl")

session = a requests.Session with the realistic User-Agent and the decrypted stored cookies
me_resp = session.get(ME_URL, timeout=TIMEOUT)
if me_resp.status_code == 200:
    player = me_resp.json().get("player")
    if player and player.get("entry") == expected_team_id:
        repository.mark_session_ok(conn)          # auth_state="active", relogin_failures=0
        return session

# session is expired → attempt one re-login
repository.set_auth_state(conn, "expired")
email = crypto.decrypt(key, repository.get_encrypted(conn, "fpl_email_encrypted"))
password = crypto.decrypt(key, repository.get_encrypted(conn, "fpl_password_encrypted"))
try:
    result = login_fn(email, password, expected_team_id=expected_team_id)
except FPLLoginError:
    failures = repository.increment_relogin_failures(conn)   # returns the new count
    if failures >= 2:
        repository.set_auth_state(conn, "frozen")
        log.warning("FPL auto-execution frozen after %d consecutive re-login failures", failures)
        raise SessionFrozen("auto-execution frozen after repeated re-login failures")
    raise ReloginFailed("FPL re-login failed; session still expired")

# re-login succeeded: store the fresh session and return a session built from it
repository.store_session(conn, key, result)   # encrypts cookies+csrf, touch_session_refreshed,
                                              # auth_state="active", relogin_failures=0
return a requests.Session built from result.cookies
```

`login_fn` defaults to `fpl_login.login` and is injectable for tests (no live calls).
`key` is the master key (already verified upstream by `master.load_key`).

## Repository additions

All static SQL with parameterized values (no string interpolation; consistent with the
existing whitelist-gated `set_encrypted`).

- `get_auth_state(conn) -> str | None` — reads `auth_state` for row id=1 (None if no row).
- `set_auth_state(conn, state)` — upserts `auth_state` for row id=1.
- `increment_relogin_failures(conn) -> int` — `relogin_failures += 1`, returns the new value.
- `mark_session_ok(conn)` — sets `auth_state='active'`, `relogin_failures=0`.
- `store_session(conn, key, result)` — encrypts `result.cookies` (JSON) and `result.csrf` into
  the credential blobs, calls `touch_session_refreshed`, then `mark_session_ok`. (This is the
  re-login analogue of what `init-fpl` does for the first login.)

`init-fpl` (`_init_fpl_cli`) gains a call to `mark_session_ok(conn)` after storing the session,
so a successful manual login always lands in `active` with the counter cleared (this is the
unfreeze path).

## Schema + migration

Add to the `credentials` table in `schema.sql`:

```sql
  auth_state TEXT DEFAULT 'active',
  relogin_failures INTEGER DEFAULT 0
```

`init_db` runs `schema.sql` (CREATE TABLE IF NOT EXISTS), which does not alter an existing
table. Add an idempotent migration invoked from `init_db` after the script:

```python
def _migrate_credentials(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credentials)")}
    if "auth_state" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN auth_state TEXT DEFAULT 'active'")
    if "relogin_failures" not in cols:
        conn.execute("ALTER TABLE credentials ADD COLUMN relogin_failures INTEGER DEFAULT 0")
```

Fresh DBs and the in-memory test DB get the columns from `schema.sql`; existing DBs get them via
the migration. Both paths are idempotent.

## CLI: `auth-status`

`fpl-autopilot auth-status` prints `auth_state`, `relogin_failures`, and
`session_last_refreshed` for the stored credentials (or "no stored session" if none). Read-only;
prints no secret. Registered as a subcommand alongside `init-fpl`.

## Error handling & exceptions

Defined in `src/auth/session.py`; none carry secret values:

| Exception | Raised when |
|-----------|-------------|
| `SessionError` | base class |
| `SessionNotInitialized` | no credentials row / no stored cookies → run `init-fpl` |
| `SessionFrozen` | `auth_state == 'frozen'` (refused), or freeze just triggered (2nd failure) |
| `ReloginFailed` | a single re-login attempt failed but the freeze threshold is not reached |

A wrong master key (cannot decrypt stored credentials) surfaces as `SessionError` (defensive —
the key is already verified against the master-password verification token upstream).

**Transient network errors are not expiry.** A `requests.RequestException` (timeout, DNS, etc.)
on the `/me` verification GET propagates out of `ensure_session` unchanged — it is **not**
treated as an expired session, does **not** trigger a re-login, and does **not** increment the
freeze counter. Only an authenticated-but-negative `/me` (HTTP 200 without the expected
`player.entry`, or a 401/403) means "expired → re-login". This keeps a network outage from
freezing auto-execution. Re-login network failures are likewise distinct: `fpl_login.login`
raises `FPLLoginError` only for an actual login/validation failure, so a network error during
re-login also propagates rather than counting toward the freeze threshold.

## Testing — fixtures only, never live

All tests inject a fake `login_fn` and a fake session/`/me` (reuse the `_Resp`/`_FakeSession`
pattern from `tests/test_fpl_login.py`); in-memory DB; throwaway master password. No live calls.

1. **Valid session** — `/me` authenticated and team matches → returns the session, stays
   `active`, `login_fn` never called.
2. **Expired + re-login OK** — `/me` not authenticated, `login_fn` succeeds → `active`,
   `relogin_failures=0`, fresh cookies stored (decrypt to confirm).
3. **Expired + re-login fails once** — `login_fn` raises `FPLLoginError` →
   raises `ReloginFailed`, `relogin_failures=1`, state `expired`.
4. **Second consecutive failure** — with `relogin_failures` already 1, another failed re-login →
   raises `SessionFrozen`, state `frozen`.
5. **Frozen refuses** — state `frozen` → `ensure_session` raises `SessionFrozen` and `login_fn`
   is never called.
6. **No credentials** — empty credentials → raises `SessionNotInitialized`.
7. **init-fpl clears freeze** — start `frozen`, run `_init_fpl_cli` with a fake successful
   `login_fn` → `active`, `relogin_failures=0`.
8. **Migration** — create a `credentials` table without the new columns, run the migration →
   columns present; running it again is a no-op.
9. **auth-status** — prints `auth_state`/`relogin_failures` and never prints a secret value.

## Scope boundary

- **IN:** verify-before-use, transparent re-login, freeze-after-2 state machine, schema + idempotent
  migration, repository state helpers, `store_session`, `auth-status`, `init-fpl` unfreeze.
- **OUT → 2.2 (Action Executor):** wiring the master key into the running scheduler; the
  structured `activity_log` freeze entry; calling `ensure_session` from a real execution job.
- **OUT → 2.4 (Telegram):** the freeze alert notification (B9).

## Definition of done (CLAUDE.md B14)

- `ensure_session`, the state machine, the migration, `auth-status`, and `init-fpl` unfreeze
  behave as specified.
- All nine tests pass; the existing suite (134) stays green.
- No secret is logged anywhere; the freeze is durably recorded in `auth_state` plus a
  `log.warning`.
- Manual smoke check (out of band, by the user): after a live `init-fpl`, `auth-status` shows
  `active`.
