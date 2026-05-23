# Unattended Scheduling Implementation Plan — Phase 2.3c

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Run the Mode Router unattended — the scheduler holds the master key in memory and fires `route_gameweek(live=True)` once per gameweek, ~2h before the deadline.

**Architecture:** A config opt-in (`unattended.enabled`), a `scheduler.auto_execute_job` that checks the pre-deadline window + a once-per-GW guard (`gameweeks.last_system_action_at`) and runs the router live, and startup wiring so `serve`/`scheduler` load the key (only when enabled) and register the job. All testable with injected `cfg`/`now`/`route_fn` — no network.

**Tech Stack:** Python 3.11+, APScheduler, `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-unattended-scheduling-design.md`

**Baseline:** suite is green at 202 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config.yaml` | Modify | `unattended: { enabled: false, hours_before_deadline: 2 }` |
| `src/config.py` | Modify | `unattended_enabled()`, `unattended_hours_before()` |
| `src/scheduler.py` | Modify | `auto_execute_job`, `_default_route`, `_maybe_load_key`; `build_scheduler(key=...)` registers the job |
| `src/cli.py` | Modify | `serve()` passes `key=_maybe_load_key()` to `build_scheduler` |
| `tests/test_config.py` | Modify | accessor tests |
| `tests/test_scheduler.py` | Create | `auto_execute_job` + registration tests |

Reused: `src/execution/router.route_gameweek`, `src/auth/master.get_master_key`, the `gameweeks` table.

---

### Task 1: config opt-in

**Files:** Modify `config.yaml`, `src/config.py`; Test `tests/test_config.py`

- [x] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_unattended_enabled_from_config():
    assert config.unattended_enabled({"unattended": {"enabled": True}}) is True
    assert config.unattended_enabled({}) is False  # default off


def test_unattended_hours_before_from_config():
    assert config.unattended_hours_before({"unattended": {"hours_before_deadline": 5}}) == 5
    assert config.unattended_hours_before({}) == 2  # default
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'src.config' has no attribute 'unattended_enabled'`.

- [x] **Step 3: Implement** — append to `src/config.py`:

```python
def unattended_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("unattended", {}).get("enabled", False))


def unattended_hours_before(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("unattended", {}).get("hours_before_deadline", 2)
```

Append to `config.yaml` (top level):
```yaml
unattended:
  enabled: false
  hours_before_deadline: 2
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: passed (incl. the 2 new).

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 204 passed (202 + 2).

- [x] **Step 6: Commit**

```bash
git add src/config.py config.yaml tests/test_config.py
git commit -m "feat: unattended config opt-in (enabled, hours_before_deadline)"
```

---

### Task 2: `auto_execute_job`

**Files:** Modify `src/scheduler.py`; Test `tests/test_scheduler.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_scheduler.py`:

```python
from datetime import datetime, timezone, timedelta
from src import scheduler

_CFG = {"unattended": {"enabled": True, "hours_before_deadline": 2}}
_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _seed_gw(db, deadline, last_action=None):
    db.execute(
        "INSERT INTO gameweeks (id, deadline_utc, is_next, last_system_action_at) VALUES (1, ?, 1, ?)",
        (deadline.isoformat(), last_action))
    db.commit()


def test_auto_execute_in_window_executes(db):
    _seed_gw(db, _NOW + timedelta(hours=1))  # within 2h

    def route_fn(conn, key):
        route_fn.called_with = key
        return [{"decision": "captain", "route": "execute", "confidence": 80}]

    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=route_fn, cfg=_CFG)
    assert route_fn.called_with == b"key"
    row = db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()
    assert row["last_system_action_at"] is not None


def test_auto_execute_out_of_window_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=10))  # >2h away
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert not called
    assert db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_already_acted_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=1), last_action="2026-05-23T10:00:00+00:00")
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert not called


def test_auto_execute_manual_notify_not_marked(db):
    _seed_gw(db, _NOW + timedelta(hours=1))
    scheduler.auto_execute_job(
        b"key", conn=db, now=_NOW,
        route_fn=lambda c, k: [{"decision": "captain", "route": "notify", "confidence": 50}], cfg=_CFG)
    assert db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_disabled_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=1))
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: called.append(1),
                               cfg={"unattended": {"enabled": False}})
    assert not called
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: FAIL — `AttributeError: module 'src.scheduler' has no attribute 'auto_execute_job'`.

- [x] **Step 3: Implement** — in `src/scheduler.py`, add `from . import config` to the imports at the top (alongside the existing `from .config import load_config, db_path`), then add these two functions:

```python
def _default_route(conn, key):
    from .execution import router
    return router.route_gameweek(conn, key, live=True)


def auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None):
    from datetime import datetime, timezone, timedelta
    cfg = cfg or load_config()
    if not config.unattended_enabled(cfg):
        return None
    hours = config.unattended_hours_before(cfg)
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT id, deadline_utc, last_system_action_at FROM gameweeks WHERE is_next=1"
        ).fetchone()
        if not row or not row["deadline_utc"] or row["last_system_action_at"]:
            return None
        deadline = datetime.fromisoformat(row["deadline_utc"])
        now = now or datetime.now(timezone.utc)
        if not (now <= deadline <= now + timedelta(hours=hours)):
            return None
        plan = (route_fn or _default_route)(conn, key)
        if any(p["route"] == "execute" for p in plan):
            conn.execute("UPDATE gameweeks SET last_system_action_at=? WHERE id=?",
                         (now.isoformat(), row["id"]))
            conn.commit()
        return plan
    finally:
        if owns:
            conn.close()
```
(`connect`/`init_db` are already imported at the top of `scheduler.py`.)

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: 5 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 209 passed (204 + 5).

- [x] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: auto_execute_job (deadline-window, once-per-GW router run)"
```

---

### Task 3: scheduler + serve key wiring

**Files:** Modify `src/scheduler.py`, `src/cli.py`; Test `tests/test_scheduler.py`

- [x] **Step 1: Write the failing tests** — append to `tests/test_scheduler.py`:

```python
def test_build_scheduler_no_key_no_autoexec():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=None)
    ids = {j.id for j in sched.get_jobs()}
    assert "auto_execute" not in ids
    assert "weekly_refresh" in ids and "hourly_refresh" in ids


def test_build_scheduler_with_key_adds_autoexec():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "auto_execute" in {j.id for j in sched.get_jobs()}


def test_maybe_load_key_disabled_returns_none():
    # config.yaml ships with unattended.enabled: false
    assert scheduler._maybe_load_key() is None
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_scheduler.py -k "build_scheduler or maybe_load_key" -v`
Expected: FAIL — `build_scheduler()` rejects the `key` keyword (`TypeError`) and `_maybe_load_key` is missing.

- [x] **Step 3: Implement** — in `src/scheduler.py`:

Add `_maybe_load_key` (e.g. after `auto_execute_job`):
```python
def _maybe_load_key():
    if not config.unattended_enabled():
        return None
    from .auth import master
    return master.get_master_key()
```

Replace the existing `build_scheduler` with the key-aware version (the two refresh-job lines are unchanged; only the `key` param + the conditional job are added):
```python
def build_scheduler(scheduler=None, key=None):
    """Register the cron jobs and return the (un-started) scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    scheduler = scheduler or BackgroundScheduler(timezone="UTC")
    scheduler.add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0),
                      id="weekly_refresh", replace_existing=True)
    scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                      id="hourly_refresh", replace_existing=True)
    if key is not None:
        scheduler.add_job(lambda: auto_execute_job(key), CronTrigger(minute="*/15"),
                          id="auto_execute", replace_existing=True)
    return scheduler
```

Replace the existing `run_scheduler_blocking` with:
```python
def run_scheduler_blocking():
    """Run the cadence headless (blocks)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    build_scheduler(BlockingScheduler(timezone="UTC"), key=_maybe_load_key()).start()
```

- [x] **Step 4: Wire `serve()`** — in `src/cli.py`, in `serve()`, find:
```python
    if scheduler:
        from .scheduler import build_scheduler
        sched = build_scheduler()
        sched.start()
```
Replace with:
```python
    if scheduler:
        from .scheduler import build_scheduler, _maybe_load_key
        sched = build_scheduler(key=_maybe_load_key())
        sched.start()
```

- [x] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: 8 passed (5 + 3).

- [x] **Step 6: Run the full suite + CLI help**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: 212 passed (209 + 3); `--help` still lists `serve`/`scheduler`. Do NOT start the real scheduler with `unattended.enabled` + `MASTER_PASSWORD`.

- [x] **Step 7: Commit**

```bash
git add src/scheduler.py src/cli.py tests/test_scheduler.py
git commit -m "feat: wire master key + auto_execute job into scheduler/serve"
```

---

## Self-Review

**Spec coverage:**
- Config opt-in (`unattended.enabled`, `hours_before_deadline`) → Task 1.
- Deadline window + once-per-GW guard + mark `last_system_action_at` → Task 2 `auto_execute_job`.
- `route_gameweek(live=True)` via `_default_route`; injectable `route_fn` → Task 2.
- Key loaded only when enabled; held in memory → Task 3 `_maybe_load_key`.
- `build_scheduler` registers the job only with a key; refresh jobs unchanged → Task 3.
- `serve` + `run_scheduler_blocking` pass the key → Task 3.
- Manual mode no-op (notify → not marked) → Task 2 `test_auto_execute_manual_notify_not_marked`.
- Disabled → no job, no key request → Task 2 (`disabled_skips`) + Task 3 (`no_key_no_autoexec`, `maybe_load_key_disabled`).
- Agent never starts the live daemon → tests inject `route_fn`; no network.

**Placeholder scan:** none — every code step complete; run steps have commands + expected counts (204 -> 209 -> 212).

**Type consistency:** `auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None)` and `route_fn(conn, key) -> plan` (list of `{"decision","route","confidence"}`) consistent between the Task 2 def, `_default_route` (`router.route_gameweek(conn, key, live=True)`), and the tests. `build_scheduler(scheduler=None, key=None)` consistent between the Task 3 def, the `serve` call, and `run_scheduler_blocking`. `config.unattended_enabled`/`unattended_hours_before` from Task 1 used in Task 2/3. The `gameweeks` columns (`is_next`, `deadline_utc`, `last_system_action_at`) match the schema.
