# Token-Capture Auth (with refresh) — Design

**Status:** approved 2026-05-23 (supersedes `2026-05-23-cookie-capture-auth-design.md`)
**Slice:** Phase 2.1 auth pivot — final form. Replaces the programmatic-login mechanism (2.1b/2.1c)
and the never-implemented cookie-capture design.
**Depends on:** 2.1a auth/crypto, the encrypted credential store, the 2.1c `auth_state` column + migration.

## Why this design (evidence)

The FPL web app uses **PingOne (OAuth2/OIDC)** for auth. Verified live on 2026-05-23:

- Authenticated API calls send **`X-Api-Authorization: Bearer <access_token>`** (an RS256 JWT).
  Confirmed: `GET /api/me/` and `GET /api/my-team/3122849/` both returned HTTP 200 with the
  user's data using only this header (no cookie). The access token's lifetime is **8 hours**
  (`iat→exp` = 28800s).
- The SPA refreshes the access token with a **public-client refresh-token grant** (seen in the
  app bundle): `POST https://account.premierleague.com/as/token`,
  `Content-Type: application/x-www-form-urlencoded`,
  body `grant_type=refresh_token&refresh_token=<rt>&client_id=bfcbaf69-aade-4c1b-8f00-c1cb8a193030`.
- The OIDC discovery doc (`/as/.well-known/openid-configuration`) lists `refresh_token` in
  `grant_types_supported`, and **the token endpoint is reachable by a plain HTTP client** — a
  scripted POST returns a normal JSON OAuth error, not a Cloudflare block. (Cloudflare fronts the
  host but passes API POSTs through; only the interactive *login* page is bot-walled.)

So: capture the **refresh token** once, mint 8-hour access tokens unattended via `/as/token`, and
call the FPL API with `X-Api-Authorization: Bearer`. This restores unattended operation (the
deadguard premise) that cookie-capture would have lost.

**Build-time caveat to validate:** a probe POST with a *dummy* refresh token returned
`invalid_client` (not `invalid_grant`), and the discovery metadata doesn't advertise public
(`none`) client auth. This is most likely because a fake token has no client binding — the SPA
demonstrably refreshes with only `client_id`. The implementation's first task validates the real
refresh end-to-end; if it fails, we capture the SPA's exact `/as/token` request shape and adjust.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Credential captured | The **refresh token** (pasted into `init-fpl`). |
| API auth | `X-Api-Authorization: Bearer <access_token>`; access token cached + auto-refreshed. |
| Refresh | `POST /as/token` public-client refresh grant; `client_id` is a hardcoded constant (not a secret). |
| Storage | New columns `refresh_token_encrypted`, `access_token_encrypted`, `access_token_expires_at` (additive migration). Cookie/password columns stay dormant. |
| Expiry / rotation | Refresh when the access token is within a skew of expiry; store the rotated refresh token each time. A failed refresh (`invalid_grant`) → `expired`, re-run `init-fpl`. |

## Constants (`src/auth/session.py`)

```python
TOKEN_URL = "https://account.premierleague.com/as/token"
ME_URL = "https://fantasy.premierleague.com/api/me/"
CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"   # public SPA client id (from the access-token JWT)
TIMEOUT = 10
EXPIRY_SKEW_SECONDS = 120   # refresh slightly early
```

## Module: `src/auth/session.py` (rebuilt)

Exceptions (none carry token values):
`SessionError` → `SessionNotInitialized`, `SessionExpired`, `TokenRefreshError`, `SessionValidationError`.

### `refresh_access_token(refresh_token, *, session=None) -> dict`
- `POST TOKEN_URL` with `data={"grant_type":"refresh_token","refresh_token":refresh_token,"client_id":CLIENT_ID}`,
  `Content-Type: application/x-www-form-urlencoded`, realistic `USER_AGENT`.
- On HTTP 200: return the parsed JSON (`access_token`, `expires_in`, optional rotated `refresh_token`).
- On a 4xx OAuth error (JSON with `error`): `raise TokenRefreshError(error)` — message is the OAuth
  `error` code only (e.g. `invalid_grant`), never the token.
- A `requests.RequestException` propagates (network failure is not "expired").

### `_authed_session(access_token) -> requests.Session`
A `requests.Session` with headers `User-Agent: USER_AGENT` and `X-Api-Authorization: Bearer <access_token>`.

### `validate_token(access_token, *, expected_team_id, session=None) -> int`
- `session = session or _authed_session(access_token)`; `GET ME_URL`.
- Non-200 or no `player.entry` → `raise SessionValidationError("token is not authenticated")`.
- `entry != expected_team_id` → `raise SessionValidationError(f"token authenticates entry {entry}, not team {expected_team_id}")`.
- Return `entry`.

### `store_tokens(conn, key, *, refresh_token, access_token, expires_at)`
Encrypt and persist `refresh_token_encrypted`, `access_token_encrypted`; set
`access_token_expires_at` (ISO UTC string); `touch_session_refreshed`; `mark_session_ok` (active).

### `ensure_session(conn, key, *, expected_team_id, refresh_session=None) -> requests.Session`
The verify-before-use entry point used by the executor (2.2):
```
refresh_blob = get_encrypted(conn, "refresh_token_encrypted")
if refresh_blob is None: raise SessionNotInitialized("run init-fpl")
access = decrypt(get_encrypted(conn,"access_token_encrypted")) if present else None
expires_at = read access_token_expires_at
if access and expires_at and utcnow() < (expires_at - EXPIRY_SKEW_SECONDS):
    return _authed_session(access)                      # cached token still good
# refresh:
try:
    tok = refresh_access_token(decrypt(refresh_blob), session=refresh_session)
except TokenRefreshError:
    set_auth_state(conn, "expired")
    raise SessionExpired("refresh token no longer valid; re-run init-fpl")
new_refresh = tok.get("refresh_token") or decrypt(refresh_blob)   # rotation-safe
expires_at = utcnow() + tok["expires_in"]
store_tokens(conn, key, refresh_token=new_refresh, access_token=tok["access_token"], expires_at=expires_at)
return _authed_session(tok["access_token"])
```
`refresh_session` is injectable (the `requests.Session` used for the `/as/token` POST) so tests
never hit the network. `ensure_session` does not call `/me` on the cached path; the caller's real
API request is the live check, and a `TokenRefreshError` on refresh is the durable-failure signal.

## CLI

### `_init_fpl_cli(conn=None, salt_path=None, verify_path=None, refresh_session=None, me_session=None)`
```
1. master gate (load key)  — unchanged
2. refresh_token = os.getenv("FPL_REFRESH_TOKEN") or input("Paste FPL refresh token: ")
3. try:
     tok = session.refresh_access_token(refresh_token, session=refresh_session)   # proves refresh works
     entry = session.validate_token(tok["access_token"], expected_team_id=cfg_team_id(), session=me_session)
   except session.TokenRefreshError as e:   print(f"Refresh token rejected: {e}"); return
   except session.SessionValidationError as e: print(f"Token rejected: {e}"); return
   except requests.RequestException:        print("Couldn't reach FPL; check your connection."); return
4. store_tokens(conn, key, refresh_token=tok.get("refresh_token") or refresh_token,
                access_token=tok["access_token"], expires_at=utcnow()+tok["expires_in"])
5. print(f"Authenticated as entry {entry}; session stored.")   # never echoes a token
```
`init-fpl` thus *proves the refresh chain end-to-end at setup* (the build-time caveat check). To
get the refresh token: DevTools → Network → the `/as/token` response during login → copy the
`refresh_token` value.

### `_auth_status_cli`
Prints `auth_state` and `access_token_expires_at` (so the user can see when it will auto-refresh).
No secret values.

## Schema + migration

Add to `credentials` in `schema.sql`: `refresh_token_encrypted BLOB`, `access_token_encrypted BLOB`,
`access_token_expires_at TEXT`. Extend `db._migrate_credentials` to `ALTER TABLE ... ADD COLUMN`
each if missing (idempotent, same pattern as 2.1c). Fresh DBs get them from `schema.sql`.

## Removed / superseded code (clean rework)

- `src/auth/fpl_login.py` + `tests/test_fpl_login.py` — deleted (dead programmatic login).
- `src/auth/session.py` re-login/freeze/cookie logic — replaced by the token design above.
- `src/data/repository.py` `increment_relogin_failures` + its test — removed (dead).
- `src/cli.py` email/password prompts and `FPLLoginError` handling — removed.
- Dormant columns (no migration to drop): `fpl_email_encrypted`, `fpl_password_encrypted`,
  `session_cookie_encrypted`, `csrf_token_encrypted`, `relogin_failures`. The `"frozen"`
  `auth_state` value is no longer produced (states are `active`/`expired`).

## Error handling & security (B7)

- Refresh token and access token encrypted at rest with the master key; never logged. Exceptions
  carry only OAuth error codes / static text — never a token.
- `init-fpl` failure (bad token / wrong team / network) → clean message, **nothing stored**.
- `refresh_access_token` distinguishes an OAuth error (`TokenRefreshError` → eventually `expired`)
  from a `requests.RequestException` (propagates — a network blip must not look like expiry).
- Realistic `USER_AGENT` on all requests (B6).

## Known limitations (documented, not solved here)

- **Refresh-token rotation contention:** if PingOne rotates refresh tokens and the user keeps using
  the *browser* on the same lineage, the tool's stored refresh token can be invalidated → next
  refresh fails → `expired` → re-paste. Mitigation noted to the user: a fresh `init-fpl` capture
  fixes it; ideally capture a token the browser session won't keep rotating.
- **Proactive expiry alerting** (Telegram "your session died before the deadline") → 2.4. This
  slice makes expiry detectable (`auth-status`, `SessionExpired`), not pushed.

## Testing — fixtures only, never live

Inject `refresh_session` (fake `.post` returning canned token JSON) and `me_session` (fake `.get`
returning canned `/me`). Throwaway secrets; no live calls.
1. `refresh_access_token` — fake 200 → returns dict with `access_token`/`expires_in`; fake 4xx
   `{"error":"invalid_grant"}` → `TokenRefreshError` (and `invalid_grant` text present, the token
   value absent).
2. `validate_token` — authenticated `/me` matching entry → returns entry; null player → `SessionValidationError`; wrong entry → `SessionValidationError`.
3. `store_tokens` — round-trips: `refresh_token_encrypted`/`access_token_encrypted` decrypt back; `access_token_expires_at` set; `auth_state=active`.
4. `ensure_session` — cached non-expired token → returns authed session without calling refresh;
   expired/missing access token → calls refresh, stores rotated refresh token + new access token,
   returns authed session; refresh raises `TokenRefreshError` → state `expired`, raises
   `SessionExpired`; no refresh token → `SessionNotInitialized`.
5. `_init_fpl_cli` — `FPL_REFRESH_TOKEN` env + injected fakes → tokens decrypt back, `auth_state=active`, success line printed, **no token echoed**; bad refresh token → nothing stored; re-running after `expired` → `active`.
6. `auth-status` — prints `auth_state`/`access_token_expires_at`, no secret.

## Scope boundary

- **IN:** refresh-token paste + storage, `/as/token` refresh, access-token cache + auto-refresh,
  `X-Api-Authorization` authed session, `/me` validation, `active/expired` state, `auth-status`,
  schema migration, deletion of dead programmatic-login code.
- **OUT → 2.2 (Action Executor):** wiring the master key into the running scheduler; calling
  `ensure_session` from a real execution job; the actual write POSTs (transfers/captain) — which
  will also need the FPL write CSRF/`X-Api-Authorization` flow re-verified for POSTs.
- **OUT → 2.4 (Telegram):** proactive expiry alerts.

## Definition of done (CLAUDE.md B14)

- `init-fpl` accepts a pasted refresh token, refreshes once to validate (`/me` + team-id), and
  stores refresh+access tokens encrypted; `auth-status` shows `active` + an expiry; `ensure_session`
  returns a Bearer-authed session, refreshing when the cached token is stale.
- Dead programmatic-login/cookie code removed; suite green (no orphaned references).
- No token logged; tokens round-trip in tests.
- Manual smoke check (out of band, by the user): paste a real refresh token into `init-fpl`,
  confirm `auth-status` shows `active`; the agent does not run the live flow.
