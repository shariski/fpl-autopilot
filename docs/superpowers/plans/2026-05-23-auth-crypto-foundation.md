# Auth Crypto Foundation (Phase 2.1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** master-password key derivation (Argon2id) + Fernet encrypt/decrypt + encrypted credential storage + `init-master-password`, with secrets never logged or persisted in plaintext.

**Architecture:** New `src/auth/` package: `crypto.py` (KDF + cipher), `master.py` (init/load/get the in-memory key, verified via a stored token). Encrypted-blob helpers added to `src/data/repository.py` for the existing `credentials` table. A CLI `init-master-password`. Single new dep: `cryptography`.

**Tech Stack:** Python 3.11+, `cryptography>=44` (Argon2id KDF + Fernet), `pytest`. `.venv` exists; `src/` is the package.

**Spec:** `docs/superpowers/specs/2026-05-23-auth-crypto-foundation-design.md`

**SECURITY:** secrets (passwords/keys/tokens) are NEVER logged; the derived key lives in memory only; only `data/.salt` + `data/.verify` are persisted (git-ignored). Tests use throwaway passwords only.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml`/`requirements.txt` | add `cryptography>=44`; package `src.auth`. |
| `.gitignore` | ignore `data/.salt`, `data/.verify`. |
| `src/auth/__init__.py`, `crypto.py`, `master.py` | KDF + cipher; master-key lifecycle. |
| `src/data/repository.py` | `set_encrypted` / `get_encrypted` (credentials table). |
| `src/cli.py` | `init-master-password` command. |
| `tests/test_crypto.py`, `tests/test_master.py`, `tests/test_repository.py` (extend) | tests. |

---

## Task 1: Deps + auth package scaffold

**Files:** Modify `pyproject.toml`, `requirements.txt`, `.gitignore`; Create `src/auth/__init__.py`

- [x] **Step 1: `pyproject.toml`** — add the dep and package:

```toml
dependencies = ["requests", "pydantic>=2", "pyyaml", "fastapi", "uvicorn", "APScheduler", "cryptography>=44"]
```
and
```toml
packages = ["src", "src.data", "src.analytics", "src.decisions", "src.interface", "src.auth"]
```

- [x] **Step 2: `requirements.txt`** — append:
```
cryptography>=44
```

- [x] **Step 3: `.gitignore`** — append (so the salt + verification token are never committed):
```
data/.salt
data/.verify
```

- [x] **Step 4: Create `src/auth/__init__.py`** (empty).

- [x] **Step 5: Reinstall + verify Argon2id is available**

```bash
.venv/bin/pip install -e ".[dev]" -q
.venv/bin/python -c "from cryptography.hazmat.primitives.kdf.argon2 import Argon2id; from cryptography.fernet import Fernet; print('argon2id+fernet ok')"
.venv/bin/pytest -q 2>&1 | tail -1
```
Expected: prints `argon2id+fernet ok`; suite still green (112). If the Argon2id import fails (cryptography too old), STOP and report — do not silently switch KDFs.

- [x] **Step 6: Commit**

```bash
git add pyproject.toml requirements.txt .gitignore src/auth/__init__.py
git commit -m "chore: cryptography dep + src/auth scaffold + gitignore salt/verify"
```

---

## Task 2: crypto.py (KDF + cipher)

**Files:** Create `src/auth/crypto.py`; Test `tests/test_crypto.py`

- [x] **Step 1: Write the failing tests** in `tests/test_crypto.py`

```python
import os
import pytest
from cryptography.fernet import InvalidToken
from src.auth import crypto


def test_derive_key_deterministic():
    salt = b"0123456789abcdef"
    k1 = crypto.derive_key("hunter2-throwaway", salt)
    k2 = crypto.derive_key("hunter2-throwaway", salt)
    assert k1 == k2
    assert crypto.derive_key("hunter2-throwaway", os.urandom(16)) != k1  # different salt -> different key


def test_encrypt_decrypt_roundtrip():
    key = crypto.derive_key("throwaway-pw", b"0123456789abcdef")
    assert crypto.decrypt(key, crypto.encrypt(key, "s3cr3t-value")) == "s3cr3t-value"


def test_wrong_key_fails_loudly():
    salt = b"0123456789abcdef"
    good = crypto.derive_key("right-pw", salt)
    bad = crypto.derive_key("wrong-pw", salt)
    token = crypto.encrypt(good, "value")
    with pytest.raises(InvalidToken):
        crypto.decrypt(bad, token)
```

- [x] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_crypto.py -v`
Expected: `ModuleNotFoundError: No module named 'src.auth.crypto'`.

- [x] **Step 3: Write `src/auth/crypto.py`**

```python
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

SALT_BYTES = 16
# Pinned Argon2id parameters (documented in the spec); memory_cost is in KiB.
_ARGON2 = dict(length=32, iterations=3, lanes=4, memory_cost=65536)


def derive_key(password, salt):
    """Argon2id(password, salt) -> a Fernet key (url-safe base64 of 32 raw bytes)."""
    raw = Argon2id(salt=salt, **_ARGON2).derive(password.encode())
    return base64.urlsafe_b64encode(raw)


def encrypt(key, plaintext):
    return Fernet(key).encrypt(plaintext.encode())


def decrypt(key, token):
    return Fernet(key).decrypt(token).decode()
```

- [x] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_crypto.py -v`
Expected: 3 passed.

- [x] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (was 112; now 115).

- [x] **Step 6: Commit**

```bash
git add src/auth/crypto.py tests/test_crypto.py
git commit -m "feat: auth crypto (Argon2id KDF + Fernet encrypt/decrypt)"
```

---

## Task 3: master.py (key lifecycle)

**Files:** Create `src/auth/master.py`; Test `tests/test_master.py`

- [x] **Step 1: Write the failing tests** in `tests/test_master.py`

```python
import pytest
from src.auth import master


def _paths(tmp_path):
    return tmp_path / ".salt", tmp_path / ".verify"


def test_is_initialized(tmp_path):
    s, v = _paths(tmp_path)
    assert master.is_initialized(s, v) is False
    master.init_master_password("throwaway-pw-123", s, v)
    assert master.is_initialized(s, v) is True


def test_init_then_load_simulating_restart(tmp_path):
    s, v = _paths(tmp_path)
    key1 = master.init_master_password("throwaway-pw-123", s, v)
    # "restart": new load with the same password derives a usable key
    key2 = master.load_key("throwaway-pw-123", s, v)
    from src.auth import crypto
    assert crypto.decrypt(key2, crypto.encrypt(key1, "x")) == "x"


def test_load_wrong_password_raises(tmp_path):
    s, v = _paths(tmp_path)
    master.init_master_password("right-pw-123456", s, v)
    with pytest.raises(master.MasterPasswordError):
        master.load_key("wrong-pw-123456", s, v)


def test_load_not_initialized_raises(tmp_path):
    s, v = _paths(tmp_path)
    with pytest.raises(master.MasterPasswordError):
        master.load_key("whatever-123456", s, v)


def test_get_master_key_env(tmp_path, monkeypatch):
    s, v = _paths(tmp_path)
    master.init_master_password("env-pw-12345678", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "env-pw-12345678")
    key = master.get_master_key(s, v)
    from src.auth import crypto
    assert crypto.decrypt(key, crypto.encrypt(key, "y")) == "y"


def test_secrets_not_logged(tmp_path, caplog):
    s, v = _paths(tmp_path)
    with caplog.at_level("DEBUG"):
        master.init_master_password("nolog-pw-123456", s, v)
        master.load_key("nolog-pw-123456", s, v)
    assert "nolog-pw-123456" not in caplog.text
```

- [x] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_master.py -v`
Expected: `ModuleNotFoundError: No module named 'src.auth.master'`.

- [x] **Step 3: Write `src/auth/master.py`**

```python
import os
import getpass
from cryptography.fernet import InvalidToken
from src import config
from .crypto import derive_key, encrypt, decrypt, SALT_BYTES

_DATA = config.ROOT / "data"
DEFAULT_SALT = _DATA / ".salt"
DEFAULT_VERIFY = _DATA / ".verify"
_VERIFY_PLAINTEXT = "fpl-autopilot-ok"


class MasterPasswordError(Exception):
    """Raised for a missing or wrong master password. Never carries the password value."""


def is_initialized(salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    return salt_path.exists() and verify_path.exists()


def init_master_password(password, salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_BYTES)
    salt_path.write_bytes(salt)
    key = derive_key(password, salt)
    verify_path.write_bytes(encrypt(key, _VERIFY_PLAINTEXT))
    return key


def load_key(password, salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    if not is_initialized(salt_path, verify_path):
        raise MasterPasswordError("master password not initialized; run `fpl-autopilot init-master-password`")
    key = derive_key(password, salt_path.read_bytes())
    try:
        decrypt(key, verify_path.read_bytes())
    except InvalidToken:
        raise MasterPasswordError("wrong master password")
    return key


def get_master_key(salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    password = os.getenv("MASTER_PASSWORD") or getpass.getpass("Master password: ")
    return load_key(password, salt_path, verify_path)
```

- [x] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_master.py -v`
Expected: 6 passed.

- [x] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (was 115; now 121).

- [x] **Step 6: Commit**

```bash
git add src/auth/master.py tests/test_master.py
git commit -m "feat: master-password key lifecycle (init/load/get, verified, in-memory)"
```

---

## Task 4: Encrypted credential store

**Files:** Modify `src/data/repository.py`; Test `tests/test_repository.py` (extend)

- [x] **Step 1: Write the failing tests** — append to `tests/test_repository.py`

```python
from src.auth import crypto


def test_set_get_encrypted_roundtrip(db):
    key = crypto.derive_key("throwaway", b"0123456789abcdef")
    token = crypto.encrypt(key, "you@example.com")
    repository.set_encrypted(db, "fpl_email_encrypted", token)
    back = repository.get_encrypted(db, "fpl_email_encrypted")
    assert crypto.decrypt(key, back) == "you@example.com"


def test_set_encrypted_updates_same_row(db):
    key = crypto.derive_key("throwaway", b"0123456789abcdef")
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "a"))
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "b"))
    rows = db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"]
    assert rows == 1  # single id=1 row, updated in place
    assert crypto.decrypt(key, repository.get_encrypted(db, "fpl_password_encrypted")) == "b"


def test_get_encrypted_missing_returns_none(db):
    assert repository.get_encrypted(db, "session_cookie_encrypted") is None


def test_encrypted_unknown_column_rejected(db):
    import pytest
    with pytest.raises(ValueError):
        repository.set_encrypted(db, "id; DROP TABLE credentials", b"x")
    with pytest.raises(ValueError):
        repository.get_encrypted(db, "not_a_column")
```

- [x] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: FAIL — `AttributeError: module 'src.data.repository' has no attribute 'set_encrypted'`.

- [x] **Step 3: Append to `src/data/repository.py`**

```python
_CRED_COLUMNS = {
    "fpl_email_encrypted", "fpl_password_encrypted",
    "session_cookie_encrypted", "csrf_token_encrypted",
}


def set_encrypted(conn, column, token):
    if column not in _CRED_COLUMNS:
        raise ValueError(f"unknown credential column: {column!r}")
    conn.execute(
        f"INSERT INTO credentials (id, {column}) VALUES (1, ?) "
        f"ON CONFLICT(id) DO UPDATE SET {column}=excluded.{column}",
        (token,),
    )
    conn.commit()


def get_encrypted(conn, column):
    if column not in _CRED_COLUMNS:
        raise ValueError(f"unknown credential column: {column!r}")
    row = conn.execute(f"SELECT {column} FROM credentials WHERE id=1").fetchone()
    return row[column] if row else None
```

- [x] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: all pass (the original 5 + 4 new = 9).

- [x] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (was 121; now 125).

- [x] **Step 6: Commit**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "feat: encrypted credential store (set_encrypted/get_encrypted, column whitelist)"
```

---

## Task 5: init-master-password CLI

**Files:** Modify `src/cli.py`; Test `tests/test_master.py` (extend)

- [x] **Step 1: Write the failing test** — append to `tests/test_master.py`

```python
def test_init_master_password_cli(tmp_path, monkeypatch, capsys):
    from src import cli
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    pws = iter(["throwaway-pw-123", "throwaway-pw-123"])  # entry + confirm
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(pws))
    cli._init_master_password_cli(salt_path=s, verify_path=v)
    assert s.exists() and v.exists()
    out = capsys.readouterr().out
    assert "UNRECOVERABLE" in out
    assert "throwaway-pw-123" not in out  # never echoes the password


def test_init_master_password_cli_mismatch(tmp_path, monkeypatch, capsys):
    from src import cli
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    pws = iter(["throwaway-pw-123", "different-pw-456"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(pws))
    cli._init_master_password_cli(salt_path=s, verify_path=v)
    assert not s.exists()  # aborted on mismatch
    assert "do not match" in capsys.readouterr().out
```

- [x] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_master.py -v`
Expected: FAIL — `AttributeError: module 'src.cli' has no attribute '_init_master_password_cli'`.

- [x] **Step 3: Add the CLI function + subcommand to `src/cli.py`.** Add this function (near `serve`):

```python
def _init_master_password_cli(salt_path=None, verify_path=None):
    import getpass
    from .auth import master
    kw = {}
    if salt_path is not None:
        kw["salt_path"] = salt_path
    if verify_path is not None:
        kw["verify_path"] = verify_path
    if master.is_initialized(**kw):
        if input("Master password already set. Overwrite (orphans existing creds)? [y/N]: ").strip().lower() != "y":
            print("Aborted.")
            return
    pw = getpass.getpass("Enter master password (min 12 chars): ")
    if len(pw) < 12:
        print("Password too short (min 12 characters).")
        return
    if pw != getpass.getpass("Confirm master password: "):
        print("Passwords do not match. Aborted.")
        return
    master.init_master_password(pw, **kw)
    print("Master password set; salt + verification token written.")
    print("IMPORTANT: this password is UNRECOVERABLE. Store it in your password manager NOW.")
    print("If lost, stored credentials become unreadable and you must re-run init-fpl after a reset.")
```

- [x] **Step 4: Register the subcommand in `main`.** After the `scheduler` subparser line `sub.add_parser("scheduler", ...)`, add:

```python
    sub.add_parser("init-master-password", help="set the master password that encrypts stored credentials")
```
And in the dispatch, after the `scheduler` branch, add:

```python
    elif args.command == "init-master-password":
        _init_master_password_cli()
```

- [x] **Step 5: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_master.py -v`
Expected: 8 passed (6 + 2 new).

- [x] **Step 6: Verify the suite + `--help`**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: suite green (127); top-level help lists `init-master-password`. Do NOT run the real `init-master-password` here (it would write `data/.salt`); the tests cover it with tmp paths. (You may run it yourself later with a real throwaway/master password.)

- [x] **Step 7: Commit**

```bash
git add src/cli.py tests/test_master.py
git commit -m "feat: init-master-password CLI (getpass, confirm, irrecoverable warning)"
```

---

## Self-Review notes (author)

- **Spec coverage:** deps + scaffold + gitignore (T1); crypto KDF/cipher (T2); master init/load/get + verification + not-initialized/wrong-password errors + secrets-not-logged (T3); encrypted store with column whitelist (T4); init-master-password CLI with confirm + warning (T5). 2.1b/2.1c (FPL login, session lifecycle) deferred per spec.
- **Security:** no secret is logged (T3 `test_secrets_not_logged`); key never persisted (only salt + verify token); `.salt`/`.verify` git-ignored (T1); column whitelist blocks SQL injection (T4); tests use throwaway passwords only; the real `init-master-password`/`init-fpl` are user-run.
- **Type/name consistency:** `crypto.derive_key/encrypt/decrypt`, `master.init_master_password/load_key/is_initialized/get_master_key(salt_path, verify_path)`, `MasterPasswordError`, `repository.set_encrypted/get_encrypted(conn, column, token)` used identically across tasks; `_init_master_password_cli(salt_path, verify_path)` matches its tests; Fernet `InvalidToken` caught in `load_key`.
```
