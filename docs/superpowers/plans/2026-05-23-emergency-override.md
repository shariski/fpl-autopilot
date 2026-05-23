# Emergency Override (Freeze / Kill-Switch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted freeze that halts all autonomous FPL writes (auto-mode `auto_execute_job` and the entire `run_deadguard_job`), toggleable by CLI and one-tap Telegram, plus a B7 auto-freeze after two consecutive re-login failures.

**Architecture:** A new `system_state` key/value table holds the freeze (row present = frozen). A new `src/execution/override.py` owns the gate (`is_frozen`/`status`/`freeze`/`unfreeze`) and the B7 policy (`maybe_auto_freeze`). The two autonomous jobs short-circuit at the top when frozen; `ensure_session` increments a `relogin_failures` counter on refresh failure and the orchestrators freeze at the threshold. A user's explicit Telegram **Confirm** is never gated (autonomous-only). Telegram exposes `f:`/`u:` callback buttons; the CLI exposes `freeze`/`unfreeze`/`freeze-status`.

**Tech Stack:** Python 3.11+, sqlite3, pytest (fixtures-only — never live), APScheduler (jobs), `requests` (Telegram transport). The `db` fixture in `tests/conftest.py` is an in-memory sqlite with `init_db` already run.

**Spec:** `docs/superpowers/specs/2026-05-23-emergency-override-design.md`

**Conventions (from the codebase, follow exactly):**
- Tests use the `db` fixture (in-memory sqlite, `init_db` applied) and assert with direct SQL or via repository/override calls.
- **NEVER `git add -A`** (it sweeps `.claude/worktrees/` gitlinks) — stage explicit paths in every commit.
- Run the suite with `.venv/bin/pytest -q` (baseline at the start of this plan: **329 passed**).
- B-rules: B2 (layering — `override` imports only the Data Layer, no Telegram), B7 (no secret/token logged), B8 (deadguard scope untouched), B9 (notify on action), B10 (log transitions, not per-tick skips).

---

### Task 1: `system_state` table + repository helpers (freeze state + relogin counter)

**Files:**
- Modify: `src/data/schema.sql` (add the `system_state` table at the end)
- Modify: `src/data/repository.py` (add 5 helpers near the existing `get/set_telegram_state` and the auth-state helpers)
- Test: `tests/test_repository.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repository.py`:

```python
def test_system_state_round_trip(db):
    from src.data import repository
    assert repository.get_system_state(db, "freeze") is None
    repository.set_system_state(db, "freeze", '{"a": 1}')
    assert repository.get_system_state(db, "freeze") == '{"a": 1}'
    repository.set_system_state(db, "freeze", '{"a": 2}')          # upsert in place
    assert repository.get_system_state(db, "freeze") == '{"a": 2}'
    assert db.execute("SELECT COUNT(*) c FROM system_state").fetchone()["c"] == 1


def test_clear_system_state(db):
    from src.data import repository
    repository.set_system_state(db, "freeze", "x")
    repository.clear_system_state(db, "freeze")
    assert repository.get_system_state(db, "freeze") is None
    repository.clear_system_state(db, "freeze")                    # idempotent: no error when absent


def test_relogin_failures_increment_and_get(db):
    from src.data import repository
    assert repository.get_relogin_failures(db) == 0               # no row yet
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    assert repository.get_relogin_failures(db) == 2


def test_mark_session_ok_resets_relogin_failures(db):
    from src.data import repository
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)
    repository.mark_session_ok(db)                                # existing helper resets to 0
    assert repository.get_relogin_failures(db) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_repository.py -k "system_state or relogin" -v`
Expected: FAIL with `AttributeError: module 'src.data.repository' has no attribute 'get_system_state'` (and the table does not exist).

- [ ] **Step 3: Add the table and the helpers**

In `src/data/schema.sql`, append at the end of the file (after the `telegram_state` table):

```sql
CREATE TABLE IF NOT EXISTS system_state (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

In `src/data/repository.py`, add these helpers (place the `system_state` trio next to `get_telegram_state`/`set_telegram_state`, and the counter pair next to `get_auth_state`/`set_auth_state`/`mark_session_ok`):

```python
def get_system_state(conn, key):
    row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_system_state(conn, key, value):
    conn.execute(
        "INSERT INTO system_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def clear_system_state(conn, key):
    conn.execute("DELETE FROM system_state WHERE key=?", (key,))
    conn.commit()


def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures = relogin_failures + 1"
    )
    conn.commit()
    return get_relogin_failures(conn)


def get_relogin_failures(conn):
    row = conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    return row["relogin_failures"] if row else 0
```

Note: `init_db` runs `executescript(schema.sql)` on every connect, so `CREATE TABLE IF NOT EXISTS system_state` creates the table for existing DBs with no `_migrate_*` helper needed (it's a new table, not a column add). `mark_session_ok` already resets `relogin_failures=0` — no change to it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_repository.py -k "system_state or relogin" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/repository.py tests/test_repository.py
git commit -m "feat: system_state table + freeze/relogin-counter repository helpers (2.7)"
```

---

### Task 2: `override` module — `is_frozen` / `status` / `freeze` / `unfreeze`

**Files:**
- Create: `src/execution/override.py`
- Test: `tests/test_override.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_override.py`:

```python
from src.execution import override


def test_not_frozen_by_default(db):
    assert override.is_frozen(db) is False
    assert override.status(db) is None


def test_freeze_sets_state_and_logs(db):
    override.freeze(db, reason="manual stop", source="user")
    assert override.is_frozen(db) is True
    st = override.status(db)
    assert st["reason"] == "manual stop" and st["source"] == "user" and "since" in st
    rows = db.execute(
        "SELECT decision_type, mode, action_taken, executed FROM activity_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["decision_type"] == "override" and rows[0]["mode"] == "override"
    assert "frozen (user): manual stop" == rows[0]["action_taken"]


def test_freeze_idempotent_no_double_log(db):
    override.freeze(db, reason="first", source="user")
    override.freeze(db, reason="second", source="user")   # no-op while frozen
    assert override.status(db)["reason"] == "first"        # original kept
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 1


def test_unfreeze_clears_and_logs(db):
    override.freeze(db, reason="x", source="user")
    override.unfreeze(db, source="user")
    assert override.is_frozen(db) is False
    actions = [r["action_taken"] for r in db.execute("SELECT action_taken FROM activity_log")]
    assert actions == ["frozen (user): x", "unfrozen (user)"]


def test_unfreeze_idempotent_when_not_frozen(db):
    override.unfreeze(db, source="user")                  # no-op, no error, no log
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_override.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.execution.override'`.

- [ ] **Step 3: Create the module**

Create `src/execution/override.py`:

```python
"""Emergency override (freeze / kill-switch) — Phase 2.7.

The gate that halts autonomous FPL execution. Imports only the Data Layer (B2): it
never sends notifications — confirmation/alert copy is the caller's job (CLI prints,
Telegram handlers reply, orchestrators notify).
"""
import json
from datetime import datetime, timezone

from src.data import repository

FREEZE_KEY = "freeze"
RELOGIN_FAILURE_THRESHOLD = 2          # B7: "if re-login fails twice in a row"


def is_frozen(conn):
    return repository.get_system_state(conn, FREEZE_KEY) is not None


def status(conn):
    """Return {since, reason, source} when frozen, else None."""
    raw = repository.get_system_state(conn, FREEZE_KEY)
    return json.loads(raw) if raw else None


def freeze(conn, *, reason, source):
    """Halt autonomous execution. Idempotent: a no-op (no re-log) if already frozen.
    source in {'user', 'auto'}."""
    if is_frozen(conn):
        return
    payload = {"since": datetime.now(timezone.utc).isoformat(), "reason": reason, "source": source}
    repository.set_system_state(conn, FREEZE_KEY, json.dumps(payload))
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"frozen ({source}): {reason}", executed=True)


def unfreeze(conn, *, source):
    """Resume autonomous execution. Idempotent. Does NOT reset relogin_failures (a
    successful refresh / re-init resets it via mark_session_ok)."""
    if not is_frozen(conn):
        return
    repository.clear_system_state(conn, FREEZE_KEY)
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"unfrozen ({source})", executed=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_override.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/execution/override.py tests/test_override.py
git commit -m "feat: override module — freeze/unfreeze/is_frozen/status gate (2.7)"
```

---

### Task 3: `maybe_auto_freeze` (B7 threshold policy) in `override`

**Files:**
- Modify: `src/execution/override.py` (add `maybe_auto_freeze`)
- Test: `tests/test_override.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_override.py`:

```python
from src.data import repository


def test_maybe_auto_freeze_below_threshold_does_nothing(db):
    repository.increment_relogin_failures(db)             # 1 failure
    assert override.maybe_auto_freeze(db) is False
    assert override.is_frozen(db) is False


def test_maybe_auto_freeze_at_threshold_freezes(db):
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)             # 2 failures
    assert override.maybe_auto_freeze(db) is True
    st = override.status(db)
    assert st["source"] == "auto" and "re-login" in st["reason"]


def test_maybe_auto_freeze_returns_false_when_already_frozen(db):
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)
    override.maybe_auto_freeze(db)                        # first call freezes -> True
    assert override.maybe_auto_freeze(db) is False        # transition only fires once
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 1


def test_maybe_auto_freeze_does_not_increment(db):
    repository.increment_relogin_failures(db)             # 1
    override.maybe_auto_freeze(db)
    assert repository.get_relogin_failures(db) == 1       # read-only of the counter
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_override.py -k maybe_auto_freeze -v`
Expected: FAIL with `AttributeError: module 'src.execution.override' has no attribute 'maybe_auto_freeze'`.

- [ ] **Step 3: Add `maybe_auto_freeze`**

Append to `src/execution/override.py`:

```python
def maybe_auto_freeze(conn):
    """B7 policy: freeze (source='auto') when consecutive re-login failures reach the
    threshold. Reads the counter (incremented by ensure_session) — does NOT increment.
    Returns True only on the transition into a freeze, so the caller alerts exactly once."""
    if is_frozen(conn):
        return False
    if repository.get_relogin_failures(conn) >= RELOGIN_FAILURE_THRESHOLD:
        freeze(conn, reason="2 consecutive FPL re-login failures", source="auto")
        return True
    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_override.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/execution/override.py tests/test_override.py
git commit -m "feat: override.maybe_auto_freeze — B7 freeze at 2 re-login failures (2.7)"
```

---

### Task 4: `ensure_session` increments `relogin_failures` on refresh failure

**Files:**
- Modify: `src/auth/session.py` (the `except TokenRefreshError` branch in `ensure_session`, ~line 98-100)
- Test: `tests/test_session.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session.py`:

```python
def test_ensure_session_refresh_failure_increments_relogin(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=past)
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.SessionExpired):
        session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_relogin_failures(db) == 1          # counted this failure


def test_ensure_session_success_resets_relogin(tmp_path, db):
    key = _key(tmp_path)
    repository.increment_relogin_failures(db)                # pretend a prior failure
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT-old", access_token="AT-old", expires_at=past)
    fake = _FakeTokenSession(payload={"access_token": "AT-new", "expires_in": 28800, "refresh_token": "RT-new"})
    session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_relogin_failures(db) == 0          # store_tokens -> mark_session_ok resets
```

Note: `store_tokens` already calls `mark_session_ok` (which resets the counter), so the second test verifies the existing reset path holds end-to-end.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_session.py -k relogin -v`
Expected: the increment test FAILS (`assert 0 == 1`); the reset test PASSES (reset already works). Confirm the increment one fails.

- [ ] **Step 3: Add the increment**

In `src/auth/session.py`, change the `except TokenRefreshError` branch inside `ensure_session` from:

```python
    try:
        tok = refresh_access_token(decrypt(key, refresh_blob), session=refresh_session)
    except TokenRefreshError:
        repository.set_auth_state(conn, "expired")
        raise SessionExpired("refresh token no longer valid; re-run init-fpl")
```

to:

```python
    try:
        tok = refresh_access_token(decrypt(key, refresh_blob), session=refresh_session)
    except TokenRefreshError:
        repository.set_auth_state(conn, "expired")
        repository.increment_relogin_failures(conn)
        raise SessionExpired("refresh token no longer valid; re-run init-fpl")
```

(The auth layer only *maintains* the counter — it does not import `override` or decide to freeze. That's the orchestrators' job, Tasks 5–6.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: PASS (all session tests, including the two new ones). The existing `test_ensure_session_network_error_is_not_expiry` still passes — a `requests.ConnectionError` is not a `TokenRefreshError`, so the counter is not touched on a network blip.

- [ ] **Step 5: Commit**

```bash
git add src/auth/session.py tests/test_session.py
git commit -m "feat: ensure_session increments relogin_failures on refresh failure (2.7)"
```

---

### Task 5: `auto_execute_job` — freeze checkpoint + B7 auto-freeze wiring

**Files:**
- Modify: `src/scheduler.py` (`auto_execute_job`, ~lines 81-124)
- Test: `tests/test_scheduler.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py` (the file already has `_seed_gw`, `_CFG`, `_NOW`, and imports `scheduler`, `tg`):

```python
def test_auto_execute_skips_when_frozen(db):
    from src.execution import override
    _seed_gw(db, _NOW + timedelta(hours=1))
    override.freeze(db, reason="test", source="user")
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert called == []                                    # route never invoked
    assert db.execute(
        "SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_session_expired_auto_freezes_at_threshold(db, monkeypatch):
    from src.auth.session import SessionExpired
    from src.execution import override
    from src.data import repository
    _seed_gw(db, _NOW + timedelta(hours=1))
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)              # already at 2 (this run would be the 2nd consecutive)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert override.is_frozen(db) is True
    assert any("FROZEN" in t for t in sent)               # the freeze alert went out


def test_auto_execute_session_expired_no_freeze_below_threshold(db, monkeypatch):
    from src.auth.session import SessionExpired
    from src.execution import override
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    monkeypatch.setattr(tg, "send_message", lambda text, **k: True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert override.is_frozen(db) is False                 # only 1st failure (counter 0 in test) -> no freeze
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scheduler.py -k "frozen or auto_freeze or no_freeze" -v`
Expected: `test_auto_execute_skips_when_frozen` FAILS (route is still called); the freeze-at-threshold test FAILS (not frozen). `no_freeze_below_threshold` may pass incidentally — that's fine.

- [ ] **Step 3: Add the checkpoint and the B7 wiring**

In `src/scheduler.py`, in `auto_execute_job`, add the `override` import to the function's existing local-import block and the freeze checkpoint right after `init_db(conn)`:

```python
def auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None):
    from datetime import datetime, timezone, timedelta
    from .interface import telegram
    from .auth.session import SessionExpired
    from .execution import override
    cfg = cfg or load_config()
    if not config.unattended_enabled(cfg):
        return None
    hours = config.unattended_hours_before(cfg)
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        if override.is_frozen(conn):
            log.info("auto_execute_job skipped: frozen")
            return None
        row = conn.execute(
            "SELECT id, deadline_utc, last_system_action_at FROM gameweeks WHERE is_next=1"
        ).fetchone()
        ...
```

Then change the existing `except SessionExpired:` block (the freeze runs first, before any notification):

```python
        try:
            plan = (route_fn or _default_route)(conn, key)
        except SessionExpired:
            froze = override.maybe_auto_freeze(conn)
            try:
                telegram.notify(conn, kind="alert", decision_type="auth", mode=mode,
                                summary="FPL session expired — re-run init-fpl. No changes were made.")
                if froze:
                    telegram.notify(conn, kind="alert", decision_type="override", mode="override",
                                    summary="Auto-execution FROZEN — 2 consecutive auth failures. "
                                            "Re-run init-fpl, then unfreeze.")
            except Exception:
                log.exception("telegram auth/freeze alert failed")
            raise
```

(`mode` is already computed above this block as `mode = config.mode(cfg)`. Leave the rest of the function unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: PASS — the three new tests plus the existing `auto_execute` tests (the existing `test_auto_execute_session_expired_alerts_and_raises` still asserts the `❌ Autopilot blocked` auth alert; with `relogin_failures` 0 in that test, `maybe_auto_freeze` returns False and no second alert is sent).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: auto_execute_job freeze checkpoint + B7 auto-freeze on SessionExpired (2.7)"
```

---

### Task 6: `run_deadguard_job` — freeze checkpoint + `_run_trigger` B7 wiring

**Files:**
- Modify: `src/interface/deadguard.py` (module import; `run_deadguard_job` top; `_run_trigger` SessionExpired branch)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadguard.py` (the file already has `_seed_gw_dl`, `_CFG`, `_NOW`, `_configure_tg`, imports `deadguard`, `telegram`, `repository`, `types`):

```python
def test_job_skips_when_frozen(db, monkeypatch):
    from src.execution import override
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    override.freeze(db, reason="test", source="user")
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out is None and called == []
    # fully dormant: state never advanced past PENDING
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"


def test_trigger_session_expired_auto_freezes_at_threshold(db, monkeypatch):
    from src.auth.session import SessionExpired
    from src.execution import override
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)              # at threshold; NOT yet frozen
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["summary"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})

    def boom(conn, key, **k):
        raise SessionExpired("expired")

    monkeypatch.setattr(deadguard.lineup, "run_lineup", boom)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert override.is_frozen(db) is True
    assert any("FROZEN" in s for s in alerts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "skips_when_frozen or auto_freezes" -v`
Expected: both FAIL (frozen job still triggers; no freeze on SessionExpired).

- [ ] **Step 3: Add the checkpoint and the B7 wiring**

In `src/interface/deadguard.py`, add the import alongside the other `src.execution` imports (after line 12 `from src.execution import transfer as transfer_exec`):

```python
from src.execution import override
```

In `run_deadguard_job`, add the checkpoint right after `init_db(conn)` (before the `gameweeks` SELECT):

```python
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        if override.is_frozen(conn):
            log.info("deadguard skipped: frozen")
            return None
        row = conn.execute(
            "SELECT id, deadline_utc, state, last_system_action_at, deadguard_warned_at, "
            ...
```

In `_run_trigger`, change the `except SessionExpired:` branch (the first one, on the lineup write) from:

```python
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True, optimize_bench=True)
    except SessionExpired:
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        return
```

to:

```python
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True, optimize_bench=True)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py -v`
Expected: PASS — the two new tests plus all existing deadguard tests (`test_job_session_expired_leaves_retryable` has `relogin_failures` 0, so `maybe_auto_freeze` returns False and only the original alert is asserted — still green).

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard freeze checkpoint (dormant) + B7 auto-freeze on SessionExpired (2.7)"
```

---

### Task 7: Telegram `f:`/`u:` handlers + `poll_once` routing

**Files:**
- Modify: `src/interface/telegram_interactive.py` (import `override`; add `handle_freeze`/`handle_unfreeze`; route `f:`/`u:` in `poll_once`)
- Test: `tests/test_telegram_interactive.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_interactive.py` (the file has `_configure`, `_cq`, imports `telegram`, `ti`, `repository`):

```python
def test_handle_freeze_sets_frozen_and_offers_unfreeze(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)                               # CHAT_ID=42
    sent = {}
    monkeypatch.setattr(telegram, "send_message",
                        lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    ti.handle_freeze(db, _cq("f:1"))
    assert override.is_frozen(db) is True
    assert sent["buttons"] == [[{"text": "▶️ Unfreeze", "callback_data": "u:1"}]]


def test_handle_freeze_wrong_chat_ignored(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    ti.handle_freeze(db, _cq("f:1", chat_id="999"))
    assert override.is_frozen(db) is False


def test_handle_unfreeze_clears(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)
    override.freeze(db, reason="x", source="user")
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    ti.handle_unfreeze(db, _cq("u:1"))
    assert override.is_frozen(db) is False


def test_poll_once_routes_freeze_and_unfreeze(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 40, "callback_query": {"id": "f", "data": "f:1", "message": {"chat": {"id": "42"}}}},
               {"update_id": 41, "callback_query": {"id": "u", "data": "u:1", "message": {"chat": {"id": "42"}}}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    froze, thawed, confirms = [], [], []
    monkeypatch.setattr(ti, "handle_freeze", lambda conn, cq, **k: froze.append(cq["id"]))
    monkeypatch.setattr(ti, "handle_unfreeze", lambda conn, cq, **k: thawed.append(cq["id"]))
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: confirms.append(cq["id"]))
    ti.poll_once(b"key", conn=db)
    assert froze == ["f"] and thawed == ["u"] and confirms == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telegram_interactive.py -k "freeze or unfreeze" -v`
Expected: FAIL (`ti.handle_freeze` does not exist; `poll_once` routes `f:` to `handle_callback`).

- [ ] **Step 3: Add the handlers and the routing**

In `src/interface/telegram_interactive.py`, add the import near the other `src.execution` imports:

```python
from src.execution import override
```

Add the two handlers (place them just above `poll_once`):

```python
def handle_freeze(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    override.freeze(conn, reason="frozen from Telegram", source="user")
    telegram.send_message("🛑 Auto-execution FROZEN. No autonomous changes will be made.",
                          buttons=[[{"text": "▶️ Unfreeze", "callback_data": "u:1"}]], session=session)
    telegram.answer_callback_query(cq["id"], text="Frozen", session=session)


def handle_unfreeze(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    override.unfreeze(conn, source="user")
    telegram.send_message("▶️ Auto-execution resumed.", session=session)
    telegram.answer_callback_query(cq["id"], text="Unfrozen", session=session)
```

In `poll_once`, replace the dispatch block:

```python
            try:
                cq = u.get("callback_query")
                if cq:
                    data = cq.get("data", "")
                    if data.startswith("k:"):
                        from src.interface import deadguard
                        deadguard.handle_keep(conn, cq, session=session)
                    elif data.startswith("f:"):
                        handle_freeze(conn, cq, session=session)
                    elif data.startswith("u:"):
                        handle_unfreeze(conn, cq, session=session)
                    else:
                        handle_callback(conn, key, cq, session=session)
            except Exception:
                log.exception("telegram handle_callback failed; advancing offset to avoid a poison loop")
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_telegram_interactive.py -v`
Expected: PASS — the four new tests plus all existing ones (the existing `k:`/`c:`/`r:` routing tests are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: telegram f:/u: freeze+unfreeze handlers and poll_once routing (2.7)"
```

---

### Task 8: 🛑 Freeze buttons on autonomous notifications

**Files:**
- Modify: `src/interface/deadguard.py` (`send_warning` — add a Freeze button row)
- Modify: `src/scheduler.py` (`auto_execute_job` — append a Freeze button message in auto mode after an execution)
- Test: `tests/test_deadguard.py` (UPDATE the existing `test_send_warning_sends_keep_button`), `tests/test_scheduler.py` (append)

- [ ] **Step 1: Update the existing warning-button test and write the auto-notice test**

In `tests/test_deadguard.py`, **replace** the existing assertion in `test_send_warning_sends_keep_button`:

```python
    assert sent["buttons"] == [[{"text": "✅ Keep as is", "callback_data": "k:30"}]]
```

with:

```python
    assert sent["buttons"] == [[{"text": "✅ Keep as is", "callback_data": "k:30"}],
                               [{"text": "🛑 Freeze", "callback_data": "f:1"}]]
```

Append to `tests/test_scheduler.py`:

```python
def test_auto_execute_auto_mode_sends_freeze_button(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    from src.interface import telegram_interactive as ti
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    monkeypatch.setattr(ti, "notify_plan", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append((text, k.get("buttons"))) or True)
    cfg = {"mode": {"current": "auto"}, "unattended": {"enabled": True, "hours_before_deadline": 2}}
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=cfg)
    assert any(btns == [[{"text": "🛑 Freeze", "callback_data": "f:1"}]] for _, btns in sent)


def test_auto_execute_manual_mode_no_freeze_button(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(k.get("buttons")) or True)
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)  # mode=manual
    assert all(b != [[{"text": "🛑 Freeze", "callback_data": "f:1"}]] for b in sent)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py::test_send_warning_sends_keep_button tests/test_scheduler.py -k "freeze_button or send_warning" -v`
Expected: the warning-button test FAILS (only one button row), the auto-mode test FAILS (no freeze button sent). `manual_mode_no_freeze_button` passes incidentally.

- [ ] **Step 3: Add the buttons**

In `src/interface/deadguard.py`, change `send_warning`:

```python
def send_warning(conn, gw, *, mins):
    text = (f"⏳ Deadguard will set your captain when ~{mins} min remain before the deadline, "
            f"unless you act.\nTap to keep your team as-is.")
    buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}],
               [{"text": "🛑 Freeze", "callback_data": "f:1"}]]
    telegram.send_message(text, buttons=buttons)
```

In `src/scheduler.py`, in `auto_execute_job`, after the existing `telegram notify_plan` try/except block and before `return plan`, append:

```python
        if config.mode(cfg) == "auto" and any(p["route"] == "execute" for p in plan):
            try:
                from .interface import telegram_interactive
                if telegram_interactive.is_enabled(cfg):
                    telegram.send_message(
                        "🛑 Tap to freeze further auto-execution.",
                        buttons=[[{"text": "🛑 Freeze", "callback_data": "f:1"}]])
            except Exception:
                log.exception("telegram freeze-button send failed")
        return plan
```

(The freeze button is only sent in auto mode and only when interactive polling is on — otherwise no `poll_once` job would process the callback. In manual/hybrid the user already controls each proposal via Confirm/Reject, so no Freeze button there.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py tests/test_scheduler.py -v`
Expected: PASS (updated warning test + the two new scheduler tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py src/scheduler.py tests/test_deadguard.py tests/test_scheduler.py
git commit -m "feat: freeze button on deadguard warning + auto-mode execution notice (2.7)"
```

---

### Task 9: CLI `freeze` / `unfreeze` / `freeze-status` + `auth-status` lines

**Files:**
- Modify: `src/cli.py` (3 helpers; `_auth_status_cli` additions; `main` subparsers + dispatch)
- Test: `tests/test_cli_freeze.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_freeze.py`:

```python
from src import cli
from src.execution import override


def test_freeze_cli_freezes(db):
    cli._freeze_cli(reason="going on holiday", conn=db)
    assert override.is_frozen(db) is True
    assert override.status(db)["reason"] == "going on holiday"


def test_unfreeze_cli_clears(db):
    override.freeze(db, reason="x", source="user")
    cli._unfreeze_cli(conn=db)
    assert override.is_frozen(db) is False


def test_freeze_status_cli_reports(db, capsys):
    cli._freeze_status_cli(conn=db)
    assert "not frozen" in capsys.readouterr().out
    override.freeze(db, reason="boom", source="auto")
    cli._freeze_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "FROZEN" in out and "boom" in out and "auto" in out


def test_auth_status_shows_freeze_and_relogin(db, capsys):
    from src.data import repository
    override.freeze(db, reason="auth gone", source="auto")
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "frozen: yes" in out and "relogin_failures: 2" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_freeze.py -v`
Expected: FAIL (`cli._freeze_cli` does not exist; `_auth_status_cli` prints no freeze line).

- [ ] **Step 3: Add the helpers, the auth-status lines, and the subcommands**

In `src/cli.py`, add the three helpers (place them near `_auth_status_cli`):

```python
def _freeze_cli(*, reason="frozen from CLI", conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    override.freeze(conn, reason=reason, source="user")
    print("🛑 Frozen — autonomous execution (auto + deadguard) halted.")
    if owns:
        conn.close()


def _unfreeze_cli(conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    override.unfreeze(conn, source="user")
    print("▶️ Unfrozen — autonomous execution resumed.")
    if owns:
        conn.close()


def _freeze_status_cli(conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    st = override.status(conn)
    if st is None:
        print("not frozen")
    else:
        print(f"FROZEN since {st['since']} (source: {st['source']}) — {st['reason']}")
    if owns:
        conn.close()
```

In `_auth_status_cli`, add the freeze + counter lines just before `if owns_conn:` (still inside the function, after the if/else that prints the auth state):

```python
    from .execution import override
    fr = override.status(conn)
    print(f"frozen: {('yes (' + fr['source'] + ') — ' + fr['reason']) if fr else 'no'}")
    print(f"relogin_failures: {repository.get_relogin_failures(conn)}")
    if owns_conn:
        conn.close()
```

(`repository` is already imported at the top of `_auth_status_cli` via `from .data import repository`.)

In `main`, add the subparsers (near the other `sub.add_parser(...)` calls):

```python
    p_freeze = sub.add_parser("freeze", help="halt all autonomous FPL execution (auto + deadguard)")
    p_freeze.add_argument("--reason", default="frozen from CLI")
    sub.add_parser("unfreeze", help="resume autonomous FPL execution")
    sub.add_parser("freeze-status", help="show whether autonomous execution is frozen")
```

and the dispatch branches (at the end of the `if/elif` chain in `main`):

```python
    elif args.command == "freeze":
        _freeze_cli(reason=args.reason)
    elif args.command == "unfreeze":
        _unfreeze_cli()
    elif args.command == "freeze-status":
        _freeze_status_cli()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli_freeze.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_freeze.py
git commit -m "feat: CLI freeze/unfreeze/freeze-status + auth-status freeze lines (2.7)"
```

---

### Task 10: Docs + full-suite verification

**Files:**
- Modify: `docs/deadguard.md` (note: frozen → dormant)
- Modify: `docs/runbook.md` (freeze/unfreeze operations + B7 recovery)

- [ ] **Step 1: Add the deadguard dormancy note**

In `docs/deadguard.md`, add this subsection (place it near the state-machine / "Scope of deadguard actions" discussion — grep for a "Scope" or "State" heading and add after it):

```markdown
## Emergency override (freeze) interaction (Phase 2.7)

When an emergency freeze is active, `run_deadguard_job` short-circuits at the very top and is
**fully dormant**: no H-120 warning, no H-30 trigger, no state transition. The user has taken
manual control (or auth broke and the system froze itself per B7); deadguard does nothing until
the freeze is cleared (`fpl-autopilot unfreeze` or the ▶️ Unfreeze Telegram button). Freezing does
**not** change deadguard's scope (B8) — it only stops it from running.
```

- [ ] **Step 2: Add the runbook operations**

In `docs/runbook.md`, add this section (grep for an operations / "Auth" / "Recovery" heading and add a sibling section):

```markdown
## Emergency override (freeze / kill-switch)

A freeze halts ALL autonomous FPL writes — auto-mode `auto_execute_job` and the entire
`run_deadguard_job`. A user's explicit Telegram **Confirm** is still honoured (freeze stops
autonomy, not deliberate action).

- **Freeze:** `fpl-autopilot freeze [--reason "..."]`, or tap 🛑 Freeze on the deadguard warning /
  an auto-mode execution notice (Telegram, requires `telegram.interactive`).
- **Unfreeze:** `fpl-autopilot unfreeze`, or tap ▶️ Unfreeze on the freeze confirmation message.
- **Status:** `fpl-autopilot freeze-status`, or the `frozen:` / `relogin_failures:` lines in
  `fpl-autopilot auth-status`.
- **No master password is required** to freeze/unfreeze — the freeze is plaintext operational state.

### Automatic freeze (B7)
After **two consecutive** failed token refreshes, the system freezes itself (`source="auto"`) and
alerts once. Unfreezing alone will not help — the refresh token is still bad. Recover by:
1. `fpl-autopilot init-fpl` (paste a fresh refresh token) — this resets `relogin_failures` to 0.
2. `fpl-autopilot unfreeze`.
```

- [ ] **Step 3: Run the FULL suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — **all** tests green (329 baseline + the ~24 new 2.7 tests). If anything fails, fix it before committing; do not commit a red suite.

- [ ] **Step 4: Commit**

```bash
git add docs/deadguard.md docs/runbook.md
git commit -m "docs: freeze/kill-switch — deadguard dormancy + runbook ops (2.7)"
```

---

## Definition of done (CLAUDE.md B14)
- Code matches the spec: a freeze halts `auto_execute_job` (auto mode) and `run_deadguard_job` (fully dormant); the interactive Confirm still executes while frozen; B7 freezes after 2 consecutive refresh failures and alerts once; freeze/unfreeze via CLI and Telegram.
- All tests pass (`.venv/bin/pytest -q` green); every new test is fixtures-only (in-memory DB, fake route/session/notify) — the agent never runs the live daemon or any `--live` (R3).
- The activity log captures freeze/unfreeze/auto-freeze transitions (`decision_type="override"`); per-tick skips are `log.info` only.
- No `decision-engine.md` change (execution gate, not decision logic). `deadguard.md` + `runbook.md` updated.
- No secret/token/cookie logged by any 2.7 code (B7).

## Self-review notes (checked against the spec)
- **Spec coverage:** §1 table+helpers → Task 1; §2 override → Tasks 2-3; §3 checkpoints → Tasks 5-6; §4 B7 (ensure_session + orchestrators) → Tasks 4-6; §5 Telegram → Tasks 7-8; §6 CLI → Task 9; §8 docs → Task 10. All sections mapped.
- **Type/name consistency:** `get_system_state`/`set_system_state`/`clear_system_state`, `increment_relogin_failures`/`get_relogin_failures`, `override.is_frozen`/`status`/`freeze(reason=,source=)`/`unfreeze(source=)`/`maybe_auto_freeze`, callback prefixes `f:`/`u:` — used identically across all tasks.
- **Existing tests touched:** only `test_send_warning_sends_keep_button` (Task 8, button row added) is intentionally updated; all other existing tests stay green because new behavior is gated (not frozen by default; `relogin_failures` 0 in legacy SessionExpired tests → `maybe_auto_freeze` returns False).
```
