# Deadguard Late-News Re-Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After deadguard executes (H-30), periodically re-check the lineup decision against force-refreshed FPL data until the deadline; on a material captain/vice/bench change, auto-apply when >15 min out (free, reversible) or alert-only in the final 15-min lockout. Lineup-only — never a transfer (B8).

**Architecture:** The pure `evaluate()` state machine gains a `reeval`/`lockout` directive for `state == DEADGUARD_EXECUTED` in-window (gated by a `reeval_enabled` kwarg, default `False` for backward-compat). `run_deadguard_job` dispatches both to a new `_run_reevaluate(conn, key, gw, cfg, *, apply)`, which force-refreshes FPL `bootstrap-static`, recomputes the lineup, compares to what's set, and either re-applies via `run_lineup` (apply=True) or alerts once (apply=False, guarded by a new `gameweeks.deadguard_reeval_alerted_at` column). The 2.7 freeze checkpoint at the top of `run_deadguard_job` makes the whole job dormant when frozen, so re-eval is inherited-gated for free.

**Tech Stack:** Python 3.11+, sqlite3, pytest (fixtures-only — never live), APScheduler (the deadguard job). The `db` fixture in `tests/conftest.py` is an in-memory sqlite with `init_db` applied.

**Spec:** `docs/superpowers/specs/2026-05-23-deadguard-late-news-reeval-design.md`

**Conventions (follow exactly):**
- Tests use the `db` fixture and assert with direct SQL or via repository/module calls.
- **NEVER `git add -A`** — stage explicit paths in every commit.
- Run with `.venv/bin/pytest`. Baseline at the start of this plan: **361 passed**.
- The new executor function is named **`_run_reevaluate`** — use this exact, fully spelled-out name everywhere (do not abbreviate it; a shortened spelling collides with a repo security hook).
- B-rules: B8 (lineup-only, no transfer), B4 (decision-engine.md versioned), B6 (force-refresh is scoped to the pre-deadline window), B7 (no secret logged), B9 (notify on action), B10 (log transitions).

---

### Task 1: `gameweeks.deadguard_reeval_alerted_at` column + migration + repo helper

**Files:**
- Modify: `src/data/schema.sql` (gameweeks table)
- Modify: `src/data/db.py` (`_migrate_gameweeks`)
- Modify: `src/data/repository.py` (add `mark_deadguard_reeval_alerted`)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadguard.py` (the file already has `_seed_gw(db, gw=30)` and imports `repository`):

```python
def test_deadguard_reeval_alerted_column_and_mark(db):
    _seed_gw(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(gameweeks)")}
    assert "deadguard_reeval_alerted_at" in cols
    repository.mark_deadguard_reeval_alerted(db, 30)
    assert db.execute(
        "SELECT deadguard_reeval_alerted_at FROM gameweeks WHERE id=30").fetchone()["deadguard_reeval_alerted_at"] is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_deadguard.py -k reeval_alerted_column -v`
Expected: FAIL — `mark_deadguard_reeval_alerted` does not exist (and the column may be missing).

- [ ] **Step 3: Add the column, migration, and helper**

In `src/data/schema.sql`, in the `gameweeks` table, add the column after `deadguard_warned_at TIMESTAMP`. The table currently ends with `deadguard_warned_at TIMESTAMP` and then `);` — add a comma and the new column:

```sql
  deadguard_warned_at TIMESTAMP,
  deadguard_reeval_alerted_at TIMESTAMP
```

In `src/data/db.py`, in `_migrate_gameweeks`, add an idempotent column-add (mirrors the existing `deadguard_warned_at` block):

```python
def _migrate_gameweeks(conn):
    """Add deadguard columns to an existing gameweeks table (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(gameweeks)")}
    if "deadguard_warned_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_warned_at TIMESTAMP")
    if "deadguard_reeval_alerted_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_reeval_alerted_at TIMESTAMP")
```

In `src/data/repository.py`, add next to `mark_deadguard_warned` (which is `UPDATE gameweeks SET deadguard_warned_at=? WHERE id=?` with `_now()`):

```python
def mark_deadguard_reeval_alerted(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_reeval_alerted_at=? WHERE id=?", (_now(), gw))
    conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_deadguard.py -k reeval_alerted_column -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/db.py src/data/repository.py tests/test_deadguard.py
git commit -m "feat: gameweeks.deadguard_reeval_alerted_at column + mark helper (2.5c-1)"
```

---

### Task 2: Config accessors + `config.yaml`

**Files:**
- Modify: `src/config.py` (add two accessors)
- Modify: `config.yaml` (add two keys under `deadguard`)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` (the file imports `from src import config`):

```python
def test_deadguard_reeval_accessors():
    assert config.deadguard_reeval_enabled({"deadguard": {"reeval_if_late_news": False}}) is False
    assert config.deadguard_reeval_enabled({"deadguard": {}}) is True       # default on
    assert config.deadguard_reeval_enabled({}) is True                      # explicit {} must not fall back
    assert config.deadguard_reeval_lockout_minutes({"deadguard": {"reeval_lockout_minutes": 20}}) == 20
    assert config.deadguard_reeval_lockout_minutes({}) == 15                # default
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -k reeval -v`
Expected: FAIL — `config.deadguard_reeval_enabled` does not exist.

- [ ] **Step 3: Add the accessors and config keys**

In `src/config.py`, add next to the other `deadguard_*` accessors (use the `cfg is not None` pattern so an explicit `{}` does NOT fall back to `config.yaml`):

```python
def deadguard_reeval_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("deadguard", {}).get("reeval_if_late_news", True))


def deadguard_reeval_lockout_minutes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("reeval_lockout_minutes", 15)
```

In `config.yaml`, add two keys to the `deadguard:` block (top-level under `deadguard`, alongside `warning_window_minutes`/`trigger_window_minutes` — NOT under `scope`):

```yaml
deadguard:
  enabled: true
  warning_window_minutes: 120
  trigger_window_minutes: 30
  reeval_if_late_news: true
  reeval_lockout_minutes: 15
  scope:
    ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -k reeval -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py config.yaml tests/test_config.py
git commit -m "feat: deadguard reeval config accessors + config.yaml keys (2.5c-1)"
```

---

### Task 3: `evaluate()` gains the `reeval`/`lockout` directive

**Files:**
- Modify: `src/interface/deadguard.py` (`evaluate`)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadguard.py` (the file has the `_ev(deadline_mins, **kw)` helper that calls `deadguard.evaluate(_NOW, ...)` with `state="PENDING"` defaults):

```python
def test_evaluate_reeval_when_executed_and_outside_lockout():
    assert _ev(20, state="DEADGUARD_EXECUTED", reeval_enabled=True) == "reeval"   # 20 > lockout 15


def test_evaluate_lockout_when_executed_inside_lockout():
    assert _ev(10, state="DEADGUARD_EXECUTED", reeval_enabled=True) == "lockout"  # 0 < 10 <= 15


def test_evaluate_reeval_past_deadline_noop():
    assert _ev(-5, state="DEADGUARD_EXECUTED", reeval_enabled=True) == "noop"


def test_evaluate_reeval_disabled_executed_noop():
    assert _ev(20, state="DEADGUARD_EXECUTED") == "noop"   # reeval_enabled defaults False


def test_evaluate_reeval_lockout_min_boundary():
    assert _ev(15, state="DEADGUARD_EXECUTED", reeval_enabled=True) == "lockout"  # mins == lockout_min -> lockout
```

Note: the pre-existing `test_evaluate_resolved_state_noop` loops over resolved states *without* `reeval_enabled`, so `DEADGUARD_EXECUTED` still returns `"noop"` there — it stays green.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "reeval or lockout" -v`
Expected: FAIL — `evaluate` doesn't accept `reeval_enabled` / doesn't return `reeval`/`lockout`.

- [ ] **Step 3: Add the directive to `evaluate`**

In `src/interface/deadguard.py`, change `evaluate` to add the two kwargs and the `DEADGUARD_EXECUTED` branch **before** the `if state in RESOLVED:` check:

```python
def evaluate(now, *, deadline, state, last_system_action_at, user_acted,
             warned, triggered, warn_min, trigger_min,
             reeval_enabled=False, lockout_min=15):
    """Return a directive: 'system_acted' | 'user_acted' | 'warn' | 'trigger'
    | 'reeval' | 'lockout' | 'noop'. Pure: no I/O, deterministic for frozen inputs (B11)."""
    if state == "DEADGUARD_EXECUTED":
        if not reeval_enabled:
            return "noop"
        mins = (deadline - now).total_seconds() / 60
        if mins <= 0:
            return "noop"
        return "lockout" if mins <= lockout_min else "reeval"
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "evaluate" -v`
Expected: PASS — the 5 new tests plus all existing `evaluate` tests (including `test_evaluate_resolved_state_noop`).

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: evaluate() reeval/lockout directive for DEADGUARD_EXECUTED (2.5c-1)"
```

---

### Task 4: `_current_lineup` + `_run_reevaluate`

**Files:**
- Modify: `src/interface/deadguard.py` (new imports; `_current_lineup`; `_run_reevaluate`)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadguard.py` (the file has `_seed_gw_dl(db, deadline, gw=30, state="PENDING", last_system=None)`, `_NOW`, `_configure_tg`, `timedelta`, `types`, and imports `deadguard`, `telegram`, `repository`):

```python
def _fake_picks(captain, vice, bench):
    """FPL /my-team picks shape: element, position, is_captain, is_vice_captain."""
    picks = [{"element": captain, "position": 1, "is_captain": True, "is_vice_captain": False},
             {"element": vice, "position": 2, "is_captain": False, "is_vice_captain": True}]
    for i, el in enumerate(bench):
        picks.append({"element": el, "position": 13 + i, "is_captain": False, "is_vice_captain": False})
    return picks


def _wire_reeval(monkeypatch, *, cur_cap, cur_vice, cur_bench, want_cap, want_vice, want_bench):
    monkeypatch.setattr("src.cli.refresh", lambda **k: None)
    monkeypatch.setattr(deadguard.xp, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(deadguard, "ensure_session", lambda conn, key: object())
    monkeypatch.setattr(deadguard.config, "team_id", lambda cfg=None: 1)
    monkeypatch.setattr(deadguard.executor, "fetch_current_picks",
                        lambda session, entry: _fake_picks(cur_cap, cur_vice, cur_bench))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": want_cap, "web_name": "Cap"}],
                                      "vice_player_id": want_vice, "confidence": 80})
    monkeypatch.setattr(deadguard.bench, "rank_bench", lambda conn, current: list(want_bench))


def test_reeval_apply_material_change_resets_lineup(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    _wire_reeval(monkeypatch, cur_cap=5, cur_vice=6, cur_bench=[20, 21, 22],
                 want_cap=7, want_vice=6, want_bench=[20, 21, 22])     # captain 5 -> 7
    notes = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    ran = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: ran.append(k) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    deadguard._run_reevaluate(db, b"key", 30, _CFG, apply=True)
    assert ran and ran[0].get("optimize_bench") is True and ran[0].get("live") is True
    assert "executed" in notes


def test_reeval_no_change_is_noop(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    _wire_reeval(monkeypatch, cur_cap=5, cur_vice=6, cur_bench=[20, 21, 22],
                 want_cap=5, want_vice=6, want_bench=[20, 21, 22])     # identical
    notes = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    ran = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: ran.append(1))
    deadguard._run_reevaluate(db, b"key", 30, _CFG, apply=True)
    assert ran == [] and notes == []


def test_reeval_lockout_alerts_once_no_apply(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=10), state="DEADGUARD_EXECUTED")
    _wire_reeval(monkeypatch, cur_cap=5, cur_vice=6, cur_bench=[20, 21, 22],
                 want_cap=7, want_vice=6, want_bench=[20, 21, 22])
    notes = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    ran = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: ran.append(1))
    deadguard._run_reevaluate(db, b"key", 30, _CFG, apply=False)
    deadguard._run_reevaluate(db, b"key", 30, _CFG, apply=False)      # second tick
    assert ran == []                                                  # never applied
    assert notes.count("alert") == 1                                  # alerted exactly once
    assert db.execute(
        "SELECT deadguard_reeval_alerted_at FROM gameweeks WHERE id=30").fetchone()["deadguard_reeval_alerted_at"] is not None


def test_reeval_session_expired_alerts(db, monkeypatch):
    from src.auth.session import SessionExpired
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    monkeypatch.setattr("src.cli.refresh", lambda **k: None)
    monkeypatch.setattr(deadguard.xp, "compute_and_store", lambda conn: None)

    def boom(conn, key):
        raise SessionExpired("expired")

    monkeypatch.setattr(deadguard, "ensure_session", boom)
    alerts = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    deadguard._run_reevaluate(db, b"key", 30, _CFG, apply=True)       # must NOT raise
    assert "alert" in alerts
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "reeval_apply or reeval_no_change or reeval_lockout or reeval_session" -v`
Expected: FAIL — `deadguard._run_reevaluate` / `deadguard.xp` / `deadguard.executor` / `deadguard.ensure_session` / `deadguard.bench` do not exist yet.

- [ ] **Step 3: Add the imports, `_current_lineup`, and `_run_reevaluate`**

In `src/interface/deadguard.py`, extend the imports near the top. Change `from src.auth.session import SessionExpired` to also import `ensure_session`, and add `xp`, `bench`, `executor`. The decision/execution import block becomes:

```python
from src.analytics import xp
from src.decisions import captain
from src.decisions import transfers
from src.decisions import bench
from src.execution import lineup
from src.execution import transfer as transfer_exec
from src.execution import executor
from src.execution import override
from src.interface import telegram
from src.auth.session import SessionExpired, ensure_session
```

(Keep the existing `from src import config`, `from src.config import load_config, db_path`, `from src.data import repository`, `from src.data.db import connect, init_db` lines unchanged. Only add `xp`/`bench`/`executor` and extend the `session` import.)

Add `_current_lineup` and `_run_reevaluate` (place them after `_run_trigger`):

```python
def _current_lineup(picks):
    """(captain_id, vice_id, [bench element ids at positions 13/14/15, in order]) from FPL /my-team picks."""
    captain_id = next((p["element"] for p in picks if p.get("is_captain")), None)
    vice_id = next((p["element"] for p in picks if p.get("is_vice_captain")), None)
    benched = [p["element"] for p in sorted(picks, key=lambda p: p["position"]) if p["position"] in (13, 14, 15)]
    return (captain_id, vice_id, benched)


def _run_reevaluate(conn, key, gw, cfg, *, apply):
    # 1. force-fresh FPL availability data so the ranker sees late news (cache-bypassed; FPL only, B6).
    #    Lazy import mirrors scheduler.refresh_and_recompute (cli<->scheduler cycle).
    try:
        from src.cli import refresh
        refresh(cfg=cfg, conn=conn, sources=("fpl",), full=True)
        xp.compute_and_store(conn)
    except Exception:
        log.exception("deadguard re-eval refresh failed")
        return                                          # stale data -> skip this tick, retry next

    # 2. desired vs current lineup
    try:
        session = ensure_session(conn, key)
        current = executor.fetch_current_picks(session, config.team_id(cfg))
        caps = captain.get_captain_picks(conn)
        if not caps["picks"]:
            return
        desired = (caps["picks"][0]["player_id"], caps["vice_player_id"], bench.rank_bench(conn, current))
        cur = _current_lineup(current)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Deadguard re-eval: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return
    except Exception:
        log.exception("deadguard re-eval compare failed")
        return

    if desired == cur:
        return                                          # no material change -> idempotent no-op

    name = caps["picks"][0]["web_name"]
    if apply:
        # >15 min out: re-apply the corrected lineup (free, reversible) and notify
        try:
            result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True,
                                       optimize_bench=True, session=session)
        except Exception as e:
            _notify(conn, "alert", f"Deadguard re-eval failed: {type(e).__name__}")
            return
        if getattr(result, "ok", False):
            repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                    action_taken=f"late-news re-eval: captain/bench updated (captain {name})",
                                    inputs={"desired": desired, "previous": cur}, executed=True)
            _notify(conn, "executed",
                    f"Late news: re-set captain {name} + bench. You can change it back before the deadline.")
    else:
        # <=15 min lockout: do NOT change; alert once
        row = conn.execute(
            "SELECT deadguard_reeval_alerted_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
        if row["deadguard_reeval_alerted_at"]:
            return
        repository.mark_deadguard_reeval_alerted(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="late-news re-eval: missed update (within lockout)",
                                inputs={"desired": desired, "previous": cur}, executed=False)
        _notify(conn, "alert",
                f"Late news: your lineup may need a change (captain {name}), but it's too close to the "
                f"deadline for me to change it safely. You may want to act.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py -v`
Expected: PASS — the 4 new `_run_reevaluate` tests plus all existing deadguard tests.

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: _run_reevaluate — late-news lineup re-eval (apply + lockout alert) (2.5c-1)"
```

---

### Task 5: Wire `run_deadguard_job` to dispatch `reeval`/`lockout`

**Files:**
- Modify: `src/interface/deadguard.py` (`run_deadguard_job`)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadguard.py`:

```python
def test_job_dispatches_reeval(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    called = []
    monkeypatch.setattr(deadguard, "_run_reevaluate",
                        lambda conn, key, gw, cfg, *, apply: called.append(apply))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "reeval" and called == [True]


def test_job_dispatches_lockout(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=10), state="DEADGUARD_EXECUTED")
    called = []
    monkeypatch.setattr(deadguard, "_run_reevaluate",
                        lambda conn, key, gw, cfg, *, apply: called.append(apply))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "lockout" and called == [False]


def test_job_reeval_disabled_noop(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    called = []
    monkeypatch.setattr(deadguard, "_run_reevaluate", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW,
                                      cfg={"deadguard": {"enabled": True, "reeval_if_late_news": False}})
    assert out == "noop" and called == []


def test_job_frozen_skips_reeval(db, monkeypatch):
    from src.execution import override
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="DEADGUARD_EXECUTED")
    override.freeze(db, reason="x", source="user")
    called = []
    monkeypatch.setattr(deadguard, "_run_reevaluate", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out is None and called == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "dispatches_reeval or dispatches_lockout or reeval_disabled or frozen_skips_reeval" -v`
Expected: FAIL — `run_deadguard_job` doesn't pass `reeval_enabled` to `evaluate` nor dispatch `reeval`/`lockout`. (`test_job_frozen_skips_reeval` may already pass via the 2.7 checkpoint — confirm it stays green.)

- [ ] **Step 3: Wire the dispatch**

In `src/interface/deadguard.py`, in `run_deadguard_job`, (a) pass the two new kwargs into the `evaluate(...)` call, and (b) add the two `elif` branches. The `evaluate` call becomes:

```python
        directive = evaluate(
            now, deadline=datetime.fromisoformat(row["deadline_utc"]), state=row["state"],
            last_system_action_at=row["last_system_action_at"], user_acted=user_acted(conn, gw),
            warned=bool(row["deadguard_warned_at"]), triggered=bool(row["deadguard_triggered_at"]),
            warn_min=config.deadguard_warning_minutes(cfg),
            trigger_min=config.deadguard_trigger_minutes(cfg),
            reeval_enabled=config.deadguard_reeval_enabled(cfg),
            lockout_min=config.deadguard_reeval_lockout_minutes(cfg))
```

And add the dispatch branches alongside the existing `elif directive == "trigger":` (keep the existing `system_acted`/`user_acted`/`warn`/`trigger` branches unchanged):

```python
        elif directive == "trigger":
            _run_trigger(conn, key, gw, cfg)
        elif directive == "reeval":
            _run_reevaluate(conn, key, gw, cfg, apply=True)
        elif directive == "lockout":
            _run_reevaluate(conn, key, gw, cfg, apply=False)
        return directive
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py -v`
Expected: PASS — the 4 new dispatch tests plus all existing deadguard tests (the 2.7 `test_job_skips_when_frozen` and all 2.5a/2.5b tests stay green).

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: run_deadguard_job dispatches reeval/lockout to _run_reevaluate (2.5c-1)"
```

---

### Task 6: `decision-engine.md` v0.11 + full-suite verification

**Files:**
- Modify: `docs/decision-engine.md` (changelog)

- [ ] **Step 1: Add the v0.11 changelog row**

In `docs/decision-engine.md`, in the `## Changelog (this document)` table, add a new row after the `v0.10` row:

```markdown
| v0.11 | 2026-05-23 | Deadguard 2.5c-1 late-news re-evaluation: after DEADGUARD_EXECUTED, `evaluate` returns a `reeval` directive (>lockout) or `lockout` directive (<= `reeval_lockout_minutes`, default 15) until the deadline. Re-eval force-refreshes FPL availability + recomputes the lineup; material change (captain/vice/bench differs from what's set) -> auto-apply via the existing captain/bench rankers when >15 min out, else alert-only. Lineup-only - no transfer (B8). Rankers reused unchanged (no threshold edits). |
```

- [ ] **Step 2: Run the FULL suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — all tests green (361 baseline + the ~16 new 2.5c-1 tests ≈ 377). If anything fails, fix it before committing; do not commit a red suite.

- [ ] **Step 3: Commit**

```bash
git add docs/decision-engine.md
git commit -m "docs: decision-engine v0.11 — deadguard late-news re-eval (2.5c-1)"
```

---

## Definition of done (CLAUDE.md B14)
- Code matches the spec: after a deadguard execution, a material lineup change from late news auto-applies (>15 min out, one notification, reversible) or alert-only (≤15-min lockout, once); lineup-only (no transfer/hit/chip); a frozen system does no re-eval; `reeval_if_late_news: false` disables it.
- All tests pass (`.venv/bin/pytest -q` green); every new test is fixtures-only (in-memory DB, faked refresh/session/picks/ranker/run_lineup/notify, frozen clock) — the agent never runs the live daemon or any `--live` (R3).
- `activity_log` captures every applied re-eval and every lockout missed-update (`mode="deadguard"`); a no-change tick is silent.
- `decision-engine.md` v0.11 added (B4). No secret/token logged (B7).

## Self-review notes (checked against the spec)
- **Spec coverage:** §1 evaluate directive → Task 3; §2 dispatch → Task 5; §3 `_run_reevaluate` + `_current_lineup` → Task 4; §4 config + schema → Tasks 1-2; decision-engine.md v0.11 → Task 6. All mapped.
- **Type/name consistency:** `_run_reevaluate(conn, key, gw, cfg, *, apply)`, `_current_lineup(picks)`, `evaluate(..., reeval_enabled=False, lockout_min=15)`, directives `"reeval"`/`"lockout"`, `config.deadguard_reeval_enabled`/`deadguard_reeval_lockout_minutes`, `repository.mark_deadguard_reeval_alerted`, column `deadguard_reeval_alerted_at` — identical across all tasks. The executor is `_run_reevaluate` everywhere (full spelling).
- **Backward-compat:** `evaluate`'s new kwargs default off (`reeval_enabled=False`), so the pre-existing `test_evaluate_resolved_state_noop` (which omits them) keeps `DEADGUARD_EXECUTED → noop`. No existing test is modified.
```
