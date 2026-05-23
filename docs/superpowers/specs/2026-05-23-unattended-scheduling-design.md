# Unattended Scheduling — Design (Phase 2.3c)

**Status:** approved 2026-05-23
**Slice:** Phase 2.3c (Decision Automation — unattended execution). The keystone that lets Auto/Hybrid act without a human.
**Depends on:** 2.3b Mode Router (`route_gameweek`), 2.1 auth (`master.get_master_key`, `ensure_session`), the `gameweeks` table (`is_next`, `deadline_utc`, `last_system_action_at`), the existing `src/scheduler.py`.

## Goal

Run the Mode Router automatically: the long-running scheduler holds the master key in memory and
fires `route_gameweek(live=True)` once per gameweek, ~2h before the deadline, so Auto/Hybrid modes
act unattended. This is the foundation the deadguard (2.5) builds on. The agent never starts the
live daemon (R3); the user does.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Key supply | At scheduler startup, `master.get_master_key()` (`MASTER_PASSWORD` env, getpass fallback), held in memory for the process lifetime. Loaded **only** when `unattended.enabled`. |
| Timing | Deadline-aware: fire when the next GW's `deadline_utc` is within `hours_before_deadline` (default 2h). |
| Idempotency | **Once per GW**, guarded by `gameweeks.last_system_action_at` (set after a real execution). |
| Live confirm | **None** — unattended Auto-mode execution has no per-run human confirm (gated by config opt-in + env password + window + mode + confidence + bounded executors). |

## Architecture

```
src/config.py        ← unattended_enabled(cfg=None), unattended_hours_before(cfg=None)
config.yaml          ← unattended: { enabled: false, hours_before_deadline: 2 }
src/scheduler.py     ← auto_execute_job(...); build_scheduler registers it when a key is supplied;
                       serve() / run_scheduler_blocking() load the key (if enabled) at startup
```
Reuses `src/execution/router.route_gameweek`, `src/auth/master.get_master_key`, and the `gameweeks`
columns. No schema change (the columns already exist).

## The job — `auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None)`

```python
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
            return None                                   # no next GW / no deadline / already acted
        deadline = datetime.fromisoformat(row["deadline_utc"])
        now = now or datetime.now(timezone.utc)
        if not (now <= deadline <= now + timedelta(hours=hours)):
            return None                                   # outside the pre-deadline window
        plan = (route_fn or _default_route)(conn, key)    # router.route_gameweek(conn, key, live=True)
        if any(p["route"] == "execute" for p in plan):
            conn.execute("UPDATE gameweeks SET last_system_action_at=? WHERE id=?",
                         (now.isoformat(), row["id"]))
            conn.commit()
        return plan
    finally:
        if owns:
            conn.close()


def _default_route(conn, key):
    from src.execution import router
    return router.route_gameweek(conn, key, live=True)
```
`route_fn`/`now`/`conn`/`cfg` are injectable so tests exercise the window + guard + mark logic with
no network and no real routing. Manual mode → `route_gameweek` returns all-notify → nothing
executes → `last_system_action_at` stays null (Manual is left to the user / the 2.5 deadguard).

## Startup key loading + registration — `src/scheduler.py`

```python
def _maybe_load_key():
    if not config.unattended_enabled():
        return None
    from .auth import master
    return master.get_master_key()           # MASTER_PASSWORD env or getpass; in-memory only


def build_scheduler(scheduler=None, key=None):
    ... existing weekly_refresh + hourly_refresh jobs (unchanged) ...
    if key is not None:
        scheduler.add_job(lambda: auto_execute_job(key), CronTrigger(minute="*/15"),
                          id="auto_execute", replace_existing=True)
    return scheduler


def run_scheduler_blocking():
    from apscheduler.schedulers.blocking import BlockingScheduler
    build_scheduler(BlockingScheduler(timezone="UTC"), key=_maybe_load_key()).start()
```
`serve()` (in `src/cli.py`) likewise calls `build_scheduler(key=_maybe_load_key())`. The check runs
every 15 min; the window + `last_system_action_at` guard make repeated firing safe (acts at most
once per GW). If unattended is disabled → `key=None` → no auto-exec job, and the master password is
**never requested** (plain refresh keeps working headless).

## Safety — unattended LIVE execution (no per-run human confirm)

The CLI `--live` confirm doesn't apply (no human present) — that is the Auto-mode bargain. It is
gated by a stack of explicit opt-ins and structural bounds:
- `unattended.enabled` defaults **false** — off unless the user opts in.
- `MASTER_PASSWORD` must be supplied to the daemon — no key → no auto-exec job at all.
- Mode — Manual executes nothing (all notify); only Auto/Hybrid act.
- Confidence floor (router) — low-confidence decisions fall back to notify.
- Window + once-per-GW — acts at most once, ~2h pre-deadline.
- Bounded executors — captain/vice + one free transfer; `chip:None`, no hit, no multi (2.2).
- The agent never starts the live daemon (R3) — the user runs it; tests inject a fake `route_fn`.

## Config

`config.yaml` gains:
```yaml
unattended:
  enabled: false
  hours_before_deadline: 2
```
Accessors:
```python
def unattended_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("unattended", {}).get("enabled", False))


def unattended_hours_before(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("unattended", {}).get("hours_before_deadline", 2)
```

## Testing — fixtures only, never live

- `config.unattended_enabled` / `unattended_hours_before` — from a cfg dict + defaults when missing.
- `auto_execute_job` (in-memory `db` seeded with a `gameweeks` row `is_next=1`, injected `cfg`
  with unattended enabled, injected `now`, injected `route_fn`):
  1. in-window + `last_system_action_at` null + `route_fn` returns an execute → `route_fn` called,
     `last_system_action_at` set on that row.
  2. out-of-window (deadline far) → `route_fn` NOT called, nothing marked.
  3. already-acted (`last_system_action_at` set) → `route_fn` NOT called.
  4. `route_fn` returns only notify (manual) → `last_system_action_at` NOT set.
  5. unattended disabled in cfg → returns None immediately, `route_fn` NOT called.
- `_maybe_load_key` — disabled cfg → returns None without attempting getpass.
- `build_scheduler(key=b"x")` registers a job with id `auto_execute`; `build_scheduler(key=None)`
  does not (inspect `get_jobs()` on an un-started `BackgroundScheduler`). The existing refresh jobs
  remain registered in both cases.

## Scope boundary
- **IN:** `auto_execute_job` (window/guard/mark via `last_system_action_at`), startup key loading
  (`_maybe_load_key`), `build_scheduler` registration, config opt-in + accessors.
- **OUT → 2.5 deadguard:** the full per-GW `state` machine (PENDING/USER_ACTED/DEADGUARD_ACTIVE),
  warning-window notifications, USER_ACTED detection, and the conservative deadguard fallback for
  Manual users. 2.3c uses only `last_system_action_at` as a once-per-GW marker.
- **OUT → 2.4 Telegram:** notifying the user that an unattended action happened (executors already
  log to `activity_log`; the push is 2.4).

## Definition of done (CLAUDE.md B14)
- With `unattended.enabled: true` + `MASTER_PASSWORD`, the scheduler holds the key and
  `auto_execute_job` runs `route_gameweek(live=True)` once per GW within the pre-deadline window,
  marking `last_system_action_at`; disabled (default) → no key requested, no auto-exec job.
- All tests fixtures-only (injected `route_fn`/`cfg`/`now`); suite stays green; the agent never runs
  the live daemon.
- Manual smoke check (out of band, by the user): start the scheduler with the env var; confirm via
  `auth-status`/`activity_log` after the window.
