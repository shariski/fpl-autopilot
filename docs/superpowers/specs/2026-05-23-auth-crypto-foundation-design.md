# Auth Crypto Foundation (Phase 2.1a) — Design Spec

- **Date:** 2026-05-23
- **Status:** Approved for planning
- **Scope:** The encryption foundation for Phase 2 — master-password key derivation, encrypt/decrypt, encrypted credential storage, and `init-master-password`. The first slice of Auth & Session (2.1); FPL login (2.1b) and session lifecycle (2.1c) build on it.
- **Slice goal:** a user can run `fpl-autopilot init-master-password`, and the app can derive an in-memory encryption key from that password (verified on every start) and encrypt/decrypt secrets at rest — with secrets never logged or persisted in plaintext.

This is the security bedrock for storing the FPL session (2.1b+). Phase 2 itself decomposes into 2.1 (→2.1a/b/c), 2.2 Action Executor, 2.3 Mode Router, 2.4 Telegram, 2.5 Deadguard, 2.6 Dry-Run, 2.7 Emergency Override — all gated on 2.1.

## Security boundary (non-negotiable)

- The agent **never** sees, asks for, stores, or logs the master password or any FPL secret. The master password is supplied **by the user at runtime** — interactive `init-master-password` (getpass) or the `MASTER_PASSWORD` env var. Tests use **throwaway** passwords only.
- The derived key lives **in memory only**; never written to disk. Only the salt and an opaque verification token are persisted.
- Secrets (passwords, keys, cookies, tokens) are **never** written to logs (B7). Errors reference fields, not values.
- `data/.salt` and `data/.verify` are git-ignored. Losing the master password is **unrecoverable** (by design); `init-master-password` says so loudly (`onboarding.md` Step 2).

## Decisions locked

| Decision | Choice |
|---|---|
| KDF | **Argon2id** (`onboarding.md` Step 2) via `cryptography` (v44+) `...kdf.argon2.Argon2id` |
| Cipher | **Fernet** (authenticated symmetric) from `cryptography` |
| Key format | `base64.urlsafe_b64encode(32-byte Argon2id output)` (Fernet key) |
| Argon2id params | `length=32, iterations=3, lanes=4, memory_cost=65536` (64 MiB) — pinned, documented |
| Salt | 16 random bytes, persisted to `data/.salt` (git-ignored) |
| Verification | a Fernet token of a constant in `data/.verify`; decrypt-on-load validates the password (Fernet is authenticated → wrong key raises) |
| Master password source | `MASTER_PASSWORD` env, else interactive `getpass` |
| Dependency | single new dep: `cryptography` |

## Scope

### In scope
- `src/auth/` package: `crypto.py` (salt + `derive_key` + `encrypt`/`decrypt`), `master.py` (`init_master_password`, `load_key`, `is_initialized`, `get_master_key`).
- Encrypted-blob helpers on the `credentials` table: `set_encrypted` / `get_encrypted` (in `src/data/repository.py`) — generic store/load used by 2.1b for email/password/cookie/csrf.
- `init-master-password` CLI command.
- `pyproject.toml`/`requirements.txt`: `cryptography`; `.gitignore`: `data/.salt`, `data/.verify`; packages: `src.auth`.
- Tests (throwaway passwords, no network, no real secrets).

### Out of scope (later slices)
- FPL login + `init-fpl` + storing real FPL secrets (2.1b).
- Session expiry / auto re-login / freeze-after-2 (2.1c).
- Any authenticated request or action execution (2.2+).

## Components

### `src/auth/crypto.py`
```
SALT_BYTES = 16
ARGON2 = dict(length=32, iterations=3, lanes=4, memory_cost=65536)

derive_key(password: str, salt: bytes) -> bytes
    # Argon2id(salt=salt, **ARGON2).derive(password.encode()) -> 32 raw bytes
    # return base64.urlsafe_b64encode(raw)   # Fernet key
encrypt(key: bytes, plaintext: str) -> bytes      # Fernet(key).encrypt(...)
decrypt(key: bytes, token: bytes) -> str          # Fernet(key).decrypt(...).decode(); raises InvalidToken on wrong key
```

### `src/auth/master.py`
Default paths from `config.ROOT / "data"` (`.salt`, `.verify`); overridable for tests.
```
is_initialized(salt_path=..., verify_path=...) -> bool          # both files exist
init_master_password(password, salt_path=..., verify_path=...) -> bytes
    # write 16-byte salt; key = derive_key(password, salt); write verify = encrypt(key, "ok"); return key
load_key(password, salt_path=..., verify_path=...) -> bytes
    # read salt; key = derive_key(password, salt); decrypt(key, verify) (raises if wrong); return key
get_master_key(salt_path=..., verify_path=...) -> bytes
    # password from MASTER_PASSWORD env else getpass; load_key; raise a clear error if not initialized / wrong password
```
`MasterPasswordError` for wrong-password / not-initialized (message names the problem, never the value).

### `src/data/repository.py` additions
```
set_encrypted(conn, column, token: bytes)   # UPSERT credentials(id=1) setting one *_encrypted BLOB column
get_encrypted(conn, column) -> bytes | None # read that column from credentials(id=1)
```
`column` restricted to the known credential columns (whitelist) to keep SQL safe.

### `init-master-password` CLI
`fpl-autopilot init-master-password`: getpass twice (confirm; min 12 chars per onboarding); if already initialized, require confirmation to overwrite (overwriting orphans existing encrypted creds — warn). Calls `init_master_password`. Prints the irrecoverable-password warning + "store it in your password manager now". Never echoes the password.

## Testing (B7, B11)
- `test_derive_key_deterministic`: same password+salt → same key; different salt → different key.
- `test_encrypt_decrypt_roundtrip`: `decrypt(key, encrypt(key, s)) == s`.
- `test_wrong_key_fails_loudly`: decrypt with a key from a different password raises `InvalidToken` (never returns garbage).
- `test_init_then_load_simulating_restart` (tmp paths): `init_master_password(pw)`; later `load_key(pw)` returns a key that decrypts a token encrypted by the first; `load_key("wrong")` raises `MasterPasswordError`.
- `test_is_initialized`: false before, true after init.
- `test_get_master_key_env` (monkeypatch `MASTER_PASSWORD`): returns the right key; unset + not-initialized → `MasterPasswordError`.
- `test_set_get_encrypted_roundtrip` (in-memory DB): store an encrypted blob in a `credentials` column, read it back, decrypt → matches; unknown column rejected.
- `test_secrets_not_logged`: capture logs around init/load — assert the throwaway password never appears.

## Definition of done
1. `pytest` green incl. crypto + master + store tests.
2. `fpl-autopilot init-master-password` (run by the user with a throwaway password) creates `data/.salt` + `data/.verify`, and a subsequent `load_key` with the same password succeeds / wrong fails. (I verify the machinery with throwaway passwords in tests; the user may also run the command.)
3. `.salt`/`.verify` git-ignored; `cryptography` added; no secret ever logged.

## Notes
- `cryptography`'s `Argon2id` KDF requires v44+ (wheels include the Rust backend). If unavailable in the environment, fall back to `argon2-cffi` for the KDF (Fernet stays) — a one-line swap in `derive_key`; note it in the plan if needed.
- The `credentials` table already exists (`architecture.md`); this slice only adds encrypted store/load helpers, not real data.
