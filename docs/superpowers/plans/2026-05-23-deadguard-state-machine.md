# Deadguard State Machine + Captain/Vice Safety Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a Manual/Hybrid user who goes silent before a deadline a captain/vice safety net: the daemon tracks each gameweek's deadguard `state`, warns at H-120 (with a one-tap "Keep as is" button), and at H-30 — if still untouched — sets captain & vice via the existing executor, then notifies.

**Architecture:** A new `src/interface/deadguard.py` holds a pure `evaluate(...)` state-transition function plus the orchestration (`user_acted`, `send_warning`, `handle_keep`, `run_deadguard_job`). A new `deadguard_job` runs it in the daemon. It reuses the existing `run_lineup` executor, the 2.4a notifier, and the 2.4b poll loop (which routes the `k:` Keep callback to deadguard). Only captain/vice is acted on (B8); bench/auto-sub/transfer are 2.5b.

**Tech Stack:** Python 3.11+, sqlite, APScheduler, `pytest` + `monkeypatch`. Run tests with `.venv/bin/pytest`.

**Branch:** `feat/deadguard-state-machine` (already created; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-05-23-deadguard-state-machine-design.md` · **Product doc:** `docs/deadguard.md`

**Conventions (binding):**
- TDD: test first, watch it fail, minimal impl, watch it pass, commit. Baseline before this plan: **272 passing**.
- **Never `git add -A`** — stage explicit paths only.
- The agent never runs the live daemon/poller or live execution (R3); tests use fakes + `monkeypatch`, no network.
- B8: 2.5a sets ONLY captain/vice via the existing `run_lineup` — no transfers/chips/hits/bench-reorder.
- `db` is an in-memory sqlite fixture in `tests/conftest.py`; `init_db` runs `schema.sql` + migrations.

---

### Task 1: `gameweeks.deadguard_warned_at` column + repository helpers

**Files:**
- Modify: `src/data/schema.sql` (the `gameweeks` CREATE TABLE)
- Modify: `src/data/db.py` (add `_migrate_gameweeks`, call it in `init_db`)
- Modify: `src/data/repository.py`
- Test: `tests/test_deadguard.py` (create)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_deadguard.py`:

```python
from src.data import repository


def _seed_gw(db, gw=30):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next) VALUES (?, '2026-05-23T18:00:00+00:00', 1)", (gw,))
    db.commit()


def test_deadguard_warned_at_column_exists(db):
    _seed_gw(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(gameweeks)")}
    assert "deadguard_warned_at" in cols


def test_set_gameweek_state(db):
    _seed_gw(db)
    repository.set_gameweek_state(db, 30, "DEADGUARD_ACTIVE")
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_ACTIVE"


def test_mark_deadguard_warned_and_triggered(db):
    _seed_gw(db)
    repository.mark_deadguard_warned(db, 30)
    repository.mark_deadguard_triggered(db, 30)
    row = db.execute("SELECT deadguard_warned_at, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_warned_at"] is not None
    assert row["deadguard_triggered_at"] is not None


def test_touch_user_action_sets_state_and_timestamp(db):
    _seed_gw(db)
    repository.touch_user_action(db, 30)
    row = db.execute("SELECT state, last_user_action_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED"
    assert row["last_user_action_at"] is not None
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `AttributeError: ... has no attribute 'set_gameweek_state'` (and the column-exists test fails too).

- [ ] **Step 3a: Add the column to `src/data/schema.sql`.** In the `gameweeks` CREATE TABLE, add `deadguard_warned_at TIMESTAMP` as the last column (after `deadguard_triggered_at TIMESTAMP`):

```sql
CREATE TABLE IF NOT EXISTS gameweeks (
  id INTEGER PRIMARY KEY,
  name TEXT,
  deadline_utc TIMESTAMP,
  is_current BOOLEAN,
  is_next BOOLEAN,
  finished BOOLEAN,
  state TEXT NOT NULL DEFAULT 'PENDING',
  last_user_action_at TIMESTAMP,
  last_system_action_at TIMESTAMP,
  deadguard_triggered_at TIMESTAMP,
  deadguard_warned_at TIMESTAMP
);
```

- [ ] **Step 3b: Add a migration for existing DBs in `src/data/db.py`** (mirrors `_migrate_credentials`). Add this function:

```python
def _migrate_gameweeks(conn):
    """Add deadguard_warned_at to an existing gameweeks table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(gameweeks)")}
    if "deadguard_warned_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_warned_at TIMESTAMP")
```

And call it in `init_db` (right after the existing `_migrate_credentials(conn)` line):

```python
def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate_credentials(conn)
    _migrate_gameweeks(conn)
    conn.commit()
```

- [ ] **Step 3c: Add the helpers to `src/data/repository.py`** (append at end; `_now()` returns an ISO-UTC string):

```python
def set_gameweek_state(conn, gw, state):
    conn.execute("UPDATE gameweeks SET state=? WHERE id=?", (state, gw))
    conn.commit()


def mark_deadguard_warned(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_warned_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def mark_deadguard_triggered(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_triggered_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def touch_user_action(conn, gw):
    conn.execute("UPDATE gameweeks SET last_user_action_at=?, state='USER_ACTED' WHERE id=?", (_now(), gw))
    conn.commit()
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → 4 passed.

- [ ] **Step 5: Commit.**
```bash
git add src/data/schema.sql src/data/db.py src/data/repository.py tests/test_deadguard.py
git commit -m "feat: gameweeks.deadguard_warned_at + deadguard repo helpers (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Config accessors

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_config.py`:

```python
def test_deadguard_accessors_from_config():
    assert config.deadguard_enabled({"deadguard": {"enabled": True}}) is True
    assert config.deadguard_enabled({}) is False
    assert config.deadguard_warning_minutes({"deadguard": {"warning_window_minutes": 90}}) == 90
    assert config.deadguard_warning_minutes({}) == 120     # default
    assert config.deadguard_trigger_minutes({"deadguard": {"trigger_window_minutes": 45}}) == 45
    assert config.deadguard_trigger_minutes({}) == 30      # default
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_config.py::test_deadguard_accessors_from_config -q` → `AttributeError`.

- [ ] **Step 3: Implement.** Append to `src/config.py` (mirrors `unattended_enabled`):

```python
def deadguard_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("deadguard", {}).get("enabled", False))


def deadguard_warning_minutes(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("deadguard", {}).get("warning_window_minutes", 120)


def deadguard_trigger_minutes(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("deadguard", {}).get("trigger_window_minutes", 30)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_config.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/config.py tests/test_config.py
git commit -m "feat: deadguard config accessors (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Pure `deadguard.evaluate(...)` state machine

**Files:**
- Create: `src/interface/deadguard.py`
- Test: `tests/test_deadguard.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_deadguard.py`:

```python
from datetime import datetime, timezone, timedelta
from src.interface import deadguard

_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _ev(deadline_mins, **kw):
    base = dict(deadline=_NOW + timedelta(minutes=deadline_mins), state="PENDING",
                last_system_action_at=None, user_acted=False, warned=False, triggered=False,
                warn_min=120, trigger_min=30)
    base.update(kw)
    return deadguard.evaluate(_NOW, **base)


def test_evaluate_before_warning_window_noop():
    assert _ev(300) == "noop"                       # 5h out


def test_evaluate_warning_window_warns_once():
    assert _ev(90) == "warn"                         # within 120, beyond 30
    assert _ev(90, warned=True) == "noop"


def test_evaluate_trigger_window_triggers_once():
    assert _ev(20) == "trigger"                      # within 30
    assert _ev(20, triggered=True) == "noop"


def test_evaluate_past_deadline_noop():
    assert _ev(-5) == "noop"


def test_evaluate_system_acted_takes_precedence():
    assert _ev(20, last_system_action_at="2026-05-23T11:00:00+00:00") == "system_acted"


def test_evaluate_user_acted_takes_precedence():
    assert _ev(20, user_acted=True) == "user_acted"


def test_evaluate_resolved_state_noop():
    for s in ("USER_ACTED", "SYSTEM_ACTED", "DEADGUARD_EXECUTED", "DEADGUARD_SKIPPED"):
        assert _ev(20, state=s) == "noop"
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `ModuleNotFoundError: No module named 'src.interface.deadguard'`.

- [ ] **Step 3: Implement.** Create `src/interface/deadguard.py`:

```python
RESOLVED = ("USER_ACTED", "SYSTEM_ACTED", "DEADGUARD_EXECUTED", "DEADGUARD_SKIPPED")


def evaluate(now, *, deadline, state, last_system_action_at, user_acted,
             warned, triggered, warn_min, trigger_min):
    """Return a directive: 'system_acted' | 'user_acted' | 'warn' | 'trigger' | 'noop'.
    Pure: no I/O, deterministic for frozen inputs (B11)."""
    if state in RESOLVED:
        return "noop"
    if last_system_action_at:
        return "system_acted"
    if user_acted:
        return "user_acted"
    mins = (deadline - now).total_seconds() / 60
    if mins <= 0:
        return "noop"
    if mins <= trigger_min:
        return "noop" if triggered else "trigger"
    if mins <= warn_min:
        return "noop" if warned else "warn"
    return "noop"
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard.evaluate pure state machine (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `user_acted`, `send_warning`, `handle_keep`

**Files:**
- Modify: `src/interface/deadguard.py`
- Test: `tests/test_deadguard.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_deadguard.py` (reuses `_seed_gw`, `repository`, `_NOW`):

```python
from src.interface import telegram


def _configure_tg(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "T")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "42")


def test_user_acted_false_when_nothing(db):
    _seed_gw(db)
    assert deadguard.user_acted(db, 30) is False


def test_user_acted_true_when_last_user_action_set(db):
    _seed_gw(db)
    repository.touch_user_action(db, 30)
    assert deadguard.user_acted(db, 30) is True


def test_user_acted_true_on_confirmed_pending(db):
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 1, "vice_id": 2}, summary="x")
    repository.set_pending_status(db, pid, "confirmed")
    assert deadguard.user_acted(db, 30) is True


def test_user_acted_false_on_superseded_pending(db):
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 1, "vice_id": 2}, summary="x")
    repository.set_pending_status(db, pid, "superseded")
    assert deadguard.user_acted(db, 30) is False


def test_send_warning_sends_keep_button(db, monkeypatch):
    _configure_tg(monkeypatch)
    sent = {}
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    deadguard.send_warning(db, 30, mins=30)
    assert "Keep" in sent["text"] or "keep" in sent["text"]
    assert sent["buttons"] == [[{"text": "✅ Keep as is", "callback_data": "k:30"}]]


def test_handle_keep_sets_user_acted(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw(db)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    cq = {"id": "cb", "data": "k:30", "message": {"chat": {"id": "42"}}}
    deadguard.handle_keep(db, cq)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "USER_ACTED"


def test_handle_keep_wrong_chat_ignored(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw(db)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    cq = {"id": "cb", "data": "k:30", "message": {"chat": {"id": "999"}}}
    deadguard.handle_keep(db, cq)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `AttributeError: ... has no attribute 'user_acted'`.

- [ ] **Step 3: Implement.** In `src/interface/deadguard.py`, add the imports at the top (above `RESOLVED`):

```python
import os
from src.data import repository
from src.interface import telegram
```

then append:

```python
def user_acted(conn, gw):
    g = conn.execute("SELECT last_user_action_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
    if g and g["last_user_action_at"]:
        return True
    n = conn.execute(
        "SELECT COUNT(*) c FROM pending_decisions WHERE gw=? AND status IN ('confirmed','rejected')",
        (gw,)).fetchone()["c"]
    return n > 0


def send_warning(conn, gw, *, mins):
    text = (f"⏳ Deadguard will set your captain in ~{mins} min if you don't act.\n"
            f"Tap to keep your team as-is.")
    buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}]]
    telegram.send_message(text, buttons=buttons)


def handle_keep(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    _, _, gw_s = cq.get("data", "").partition(":")
    if gw_s.isdigit():
        repository.touch_user_action(conn, int(gw_s))
    telegram.answer_callback_query(cq["id"], text="Kept as is ✅", session=session)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard user_acted + send_warning + handle_keep (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `run_deadguard_job` + `_run_trigger`

**Files:**
- Modify: `src/interface/deadguard.py`
- Test: `tests/test_deadguard.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_deadguard.py`:

```python
import types

_CFG = {"deadguard": {"enabled": True, "warning_window_minutes": 120, "trigger_window_minutes": 30}}


def _seed_gw_dl(db, deadline, gw=30, state="PENDING", last_system=None):
    db.execute("DELETE FROM gameweeks WHERE id=?", (gw,))
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, state, last_system_action_at) "
               "VALUES (?, ?, 1, ?, ?)", (gw, deadline.isoformat(), state, last_system))
    db.commit()


def test_job_warns_in_warning_window(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=90))
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: sent.append(text) or True)
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "warn"
    assert sent and db.execute("SELECT deadguard_warned_at FROM gameweeks WHERE id=30").fetchone()["deadguard_warned_at"] is not None


def test_job_triggers_and_sets_captain(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: called.append(k.get("live")) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "trigger" and called == [True]
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"


def test_job_skips_when_no_pick(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [], "vice_player_id": None, "confidence": 0})
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert called == []
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_SKIPPED"


def test_job_system_acted_suppresses(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), last_system="2026-05-23T11:00:00+00:00")
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "system_acted" and called == []
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "SYSTEM_ACTED"


def test_job_disabled_returns_none(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg={"deadguard": {"enabled": False}})
    assert out is None and called == []
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `AttributeError: ... has no attribute 'run_deadguard_job'`.

- [ ] **Step 3: Implement.** In `src/interface/deadguard.py`, add the remaining imports at the top (with the others), plus a module logger:

```python
import logging
from datetime import datetime, timezone
from src import config
from src.config import load_config, db_path
from src.data.db import connect, init_db
from src.decisions import captain
from src.execution import lineup
from src.auth.session import SessionExpired

log = logging.getLogger(__name__)   # add once, after the import block
```

then append:

```python
def _run_trigger(conn, key, gw):
    repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")
    caps = captain.get_captain_picks(conn)
    if not caps["picks"]:
        repository.set_gameweek_state(conn, gw, "DEADGUARD_SKIPPED")
        repository.mark_deadguard_triggered(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="skipped: no captain pick available", executed=False)
        _notify(conn, "info", "Deadguard ran — no safe action (no data). Team unchanged.")
        return
    try:
        lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True)
    except SessionExpired:
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        return                                          # leave un-triggered; retry next tick
    except Exception as e:
        _notify(conn, "alert", f"Deadguard failed: {type(e).__name__}")
        return
    repository.set_gameweek_state(conn, gw, "DEADGUARD_EXECUTED")
    repository.mark_deadguard_triggered(conn, gw)
    name = caps["picks"][0]["web_name"]
    repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                            action_taken=f"captain set: {name}", inputs={"pick": caps["picks"][0]},
                            executed=True)
    _notify(conn, "executed", f"Deadguard set your captain: {name}")


def _notify(conn, kind, summary):
    try:
        telegram.notify(conn, kind=kind, decision_type="deadguard", mode="deadguard", summary=summary)
    except Exception:
        log.exception("deadguard notify failed")


def run_deadguard_job(key, *, conn=None, now=None, cfg=None):
    cfg = cfg or load_config()
    if not config.deadguard_enabled(cfg):
        return None
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT id, deadline_utc, state, last_system_action_at, deadguard_warned_at, "
            "deadguard_triggered_at FROM gameweeks WHERE is_next=1").fetchone()
        if not row or not row["deadline_utc"]:
            return None
        gw = row["id"]
        now = now or datetime.now(timezone.utc)
        directive = evaluate(
            now, deadline=datetime.fromisoformat(row["deadline_utc"]), state=row["state"],
            last_system_action_at=row["last_system_action_at"], user_acted=user_acted(conn, gw),
            warned=bool(row["deadguard_warned_at"]), triggered=bool(row["deadguard_triggered_at"]),
            warn_min=config.deadguard_warning_minutes(cfg),
            trigger_min=config.deadguard_trigger_minutes(cfg))
        if directive == "system_acted":
            repository.set_gameweek_state(conn, gw, "SYSTEM_ACTED")
        elif directive == "user_acted":
            repository.set_gameweek_state(conn, gw, "USER_ACTED")
        elif directive == "warn":
            send_warning(conn, gw, mins=config.deadguard_trigger_minutes(cfg))
            repository.mark_deadguard_warned(conn, gw)
        elif directive == "trigger":
            _run_trigger(conn, key, gw)
        return directive
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard run_deadguard_job + captain/vice trigger (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Scheduler wiring (`_maybe_load_key` + `deadguard_job`)

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_scheduler.py`:

```python
def test_maybe_load_key_loads_when_deadguard(monkeypatch):
    monkeypatch.setattr(scheduler.config, "unattended_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: True)
    import src.auth.master as master
    monkeypatch.setattr(master, "get_master_key", lambda: b"k")
    assert scheduler._maybe_load_key() == b"k"


def test_build_scheduler_registers_deadguard_job_when_enabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: True)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "deadguard_job" in {j.id for j in sched.get_jobs()}


def test_build_scheduler_no_deadguard_job_when_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: False)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "deadguard_job" not in {j.id for j in sched.get_jobs()}
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_scheduler.py -q` → the three new tests fail.

- [ ] **Step 3: Implement.** In `src/scheduler.py`:

(a) Replace `_maybe_load_key`:
```python
def _maybe_load_key():
    if not (config.unattended_enabled() or config.telegram_interactive_enabled()
            or config.deadguard_enabled()):
        return None
    from .auth import master
    return master.get_master_key()
```

(b) In `build_scheduler`, after the `telegram_poll` block and before `return scheduler`, add:
```python
    if key is not None and config.deadguard_enabled():
        from .interface import deadguard
        scheduler.add_job(lambda: deadguard.run_deadguard_job(key),
                          CronTrigger(minute="*/5"), id="deadguard_job", replace_existing=True)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_scheduler.py -q` → all pass (existing tests unaffected; they don't set `deadguard_enabled` so the default `config.yaml` value applies — note `config.yaml` ships `deadguard.enabled: true`, so the pre-existing `test_build_scheduler_with_key_adds_autoexec` will ALSO now register `deadguard_job`; that test only asserts `auto_execute` is present, so it still passes).

- [ ] **Step 5: Commit.**
```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: register deadguard_job + load key on deadguard (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Route the `k:` Keep callback in `poll_once`

**Files:**
- Modify: `src/interface/telegram_interactive.py` (the `poll_once` loop)
- Test: `tests/test_telegram_interactive.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_telegram_interactive.py`:

```python
def test_poll_once_routes_keep_callback_to_deadguard(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 30, "callback_query": {"id": "k", "data": "k:30", "message": {"chat": {"id": "42"}}}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    from src.interface import deadguard
    kept = []
    monkeypatch.setattr(deadguard, "handle_keep", lambda conn, cq, **k: kept.append(cq["data"]))
    cr = []
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: cr.append(cq["data"]))
    ti.poll_once(b"key", conn=db)
    assert kept == ["k:30"] and cr == []   # routed to deadguard, NOT handle_callback
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_telegram_interactive.py::test_poll_once_routes_keep_callback_to_deadguard -q` → the `k:` callback currently goes to `handle_callback` (cr non-empty / kept empty).

- [ ] **Step 3: Implement.** In `src/interface/telegram_interactive.py`, change the dispatch inside the `poll_once` loop. The current loop body is:
```python
        for u in telegram.get_updates(offset, session=session):
            try:
                cq = u.get("callback_query")
                if cq:
                    handle_callback(conn, key, cq, session=session)
            except Exception:
                log.exception("telegram handle_callback failed; advancing offset to avoid a poison loop")
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
```
Change the inner dispatch to route the `k:` namespace to deadguard:
```python
        for u in telegram.get_updates(offset, session=session):
            try:
                cq = u.get("callback_query")
                if cq:
                    if cq.get("data", "").startswith("k:"):
                        from src.interface import deadguard
                        deadguard.handle_keep(conn, cq, session=session)
                    else:
                        handle_callback(conn, key, cq, session=session)
            except Exception:
                log.exception("telegram handle_callback failed; advancing offset to avoid a poison loop")
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: route deadguard k: keep callback in poll_once (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Manual-CLI USER_ACTED

**Files:**
- Modify: `src/cli.py` (`_execute_lineup_cli`, `_execute_transfer_cli`)
- Test: `tests/test_cli.py` (append; create if missing)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_cli.py` (create the file with `import types` + `from src import cli` + `from src.data import repository` at the top if it does not exist):

```python
import types
from src import cli
from src.data import repository


def _seed_next_gw(db, gw=30):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (?, '2026-05-23T18:00:00+00:00', 1, 0)", (gw,))
    db.commit()


def test_execute_lineup_live_success_marks_user_acted(db, monkeypatch):
    _seed_next_gw(db)
    # _execute_lineup_cli does `from .auth import master` / `from .execution import lineup`
    # internally, so patching those modules (same objects) is what matters:
    import src.auth.master as master
    import src.execution.lineup as lineup_mod
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    monkeypatch.setattr(master, "get_master_key", lambda **k: b"key")
    monkeypatch.setattr(lineup_mod, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200,
                                                                     request=None))
    cli._execute_lineup_cli(conn=db, live=True, confirm_fn=lambda d: True)
    row = db.execute("SELECT state, last_user_action_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED" and row["last_user_action_at"] is not None


def test_execute_lineup_dryrun_does_not_mark(db, monkeypatch):
    _seed_next_gw(db)
    import src.auth.master as master
    import src.execution.lineup as lineup_mod
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    monkeypatch.setattr(master, "get_master_key", lambda **k: b"key")
    monkeypatch.setattr(lineup_mod, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=False, dry_run=True, status=None,
                                                                     request={"method": "POST", "url": "u", "body": {}}))
    cli._execute_lineup_cli(conn=db, live=False)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"
```


- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_cli.py -q` → `test_execute_lineup_live_success_marks_user_acted` fails (state stays `PENDING`).

- [ ] **Step 3: Implement.** In `src/cli.py`, in BOTH `_execute_lineup_cli` and `_execute_transfer_cli`, change the live-success branch. Currently:
```python
    elif result.ok:
        print(f"Submitted. HTTP {result.status}.")
```
to:
```python
    elif result.ok:
        print(f"Submitted. HTTP {result.status}.")
        from .data import repository
        from .decisions.transfers import _next_gw
        gw = _next_gw(conn)
        if gw is not None:
            repository.touch_user_action(conn, gw)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_cli.py -q` → both pass.

- [ ] **Step 5: Commit.**
```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat: manual CLI execute marks USER_ACTED (deadguard suppression) (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: decision-engine changelog + full-suite verification

**Files:**
- Modify: `docs/decision-engine.md`

- [ ] **Step 1: Add a changelog entry.** In `docs/decision-engine.md`, find the changelog section (it goes up to v0.8) and append a new entry, matching the existing format:

```markdown
- **v0.9 (2026-05-23):** Deadguard (Phase 2.5a) consumes the captain ranker for its captain/vice
  safety action when a Manual/Hybrid user goes silent (H-30 trigger). No threshold change — deadguard
  reuses the existing captain selection. Transfer/bench scope is deferred to 2.5b.
```

(Match whatever the actual latest version/heading style is in the file; if entries are under a `## Changelog` heading with `### vX.Y` subheadings, follow that instead.)

- [ ] **Step 2: Full-suite verification.** Run `.venv/bin/pytest -q`. Expected: all prior 272 tests plus the new 2.5a tests pass (~298 total), zero failures, no network access.

- [ ] **Step 3: Secret-leak check.** Run `grep -n "log\|print" src/interface/deadguard.py` and confirm there is NO logging/printing of the bot token, chat id, or a token-bearing URL (the module reaches the network only via `telegram.*`, which is B7-clean; the only matches should be `repository.log_activity` calls, the module `log = logging.getLogger` line, and the fixed-string `log.exception("deadguard notify failed")` — none of which carry secrets).

- [ ] **Step 4: Commit.**
```bash
git add docs/decision-engine.md
git commit -m "docs: decision-engine changelog for deadguard captain/vice (2.5a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done (CLAUDE.md B14)

- [ ] `gameweeks.deadguard_warned_at` exists (fresh + migrated DBs); the four repo helpers + three config accessors work.
- [ ] `evaluate` is a pure, frozen-input-tested state machine returning the right directive for every window/state.
- [ ] `user_acted` detects the Keep button / manual CLI (`last_user_action_at`) and 2.4b confirm/reject; SYSTEM_ACTED from `last_system_action_at`.
- [ ] `run_deadguard_job`: warns once in the warning window, triggers once in the trigger window setting captain/vice via `run_lineup(live=True)` → DEADGUARD_EXECUTED + notify; no pick → DEADGUARD_SKIPPED + notify; system/user-acted → suppressed; disabled → no-op. Failures notify and leave it to retry, never crash.
- [ ] The H-120 warning carries a working `k:<gw>` Keep button; `poll_once` routes it to `deadguard.handle_keep`; a manual CLI execute marks USER_ACTED.
- [ ] `deadguard_job` registered only when key present + `deadguard_enabled`; `_maybe_load_key` loads on deadguard.
- [ ] Full `pytest -q` green; no token/chat logged; `decision-engine.md` changelog updated; the agent never ran the live daemon.
- [ ] Manual smoke check (out of band, by the user): force a GW into the trigger window with the daemon up; confirm the captain is set + notified, and that tapping "Keep as is" beforehand suppresses it.
