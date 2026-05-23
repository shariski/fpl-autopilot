# Deadguard State Machine + Captain/Vice Safety Net — Design (Phase 2.5a)

**Status:** approved 2026-05-23
**Slice:** Phase 2.5a (Decision Automation — deadguard, slice 1 of 3). The conservative fallback that
acts for a Manual/Hybrid user who goes silent before a deadline. 2.5a delivers the state machine,
windows, the "Keep as is" affordance, and the one already-executable safe action: **captain/vice**.
**Depends on:** the `gameweeks` table (`state`, `last_user_action_at`, `last_system_action_at`,
`deadguard_triggered_at` columns already exist), 2.2 executors (`lineup.run_lineup`), 2.3c scheduler
(`build_scheduler`, `_maybe_load_key`, `auto_execute_job` sets `last_system_action_at`), 2.4a notifier
(`telegram.notify`/`send_message`), 2.4b interactive (`poll_once` dispatch, `pending_decisions`).
**Source of truth:** `docs/deadguard.md` (full product spec). 2.5a implements its state machine +
windows + the "always-allowed" captain/vice action only.

## Goal

A Manual/Hybrid user who does not act before a deadline still gets a sane captain. The daemon tracks
each gameweek's deadguard `state`, warns at H-120 (with a one-tap "Keep as is" button), and at H-30 —
if still untouched — sets captain & vice via the existing bounded executor, then notifies. The agent
never runs the live daemon (R3); the user runs it with the master password.

## Decomposition (from `docs/deadguard.md`)

- **2.5a (this):** state machine + USER_ACTED/SYSTEM_ACTED detection + H-120 warning (Keep-as-is
  button) + H-30 trigger → captain/vice via `run_lineup` → EXECUTED/SKIPPED + notify.
- **2.5b (next):** bench-order optimization + auto-sub of flagged players (new decision logic +
  position-reorder payload) + single-transfer-if-flagged (flagged detection + stricter threshold).
- **2.5c (future):** late-news re-evaluation, undo, dashboard banner, multi-device.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Structure | New `src/interface/deadguard.py` (orchestration/jobs layer, like the scheduler): pure `evaluate(...)` + `user_acted`/`send_warning`/`run_deadguard_job`/`handle_keep`. Own scheduler `deadguard_job` (Approach A). |
| State eval | A **pure** `evaluate(...)` returning a directive (`system_acted`/`user_acted`/`warn`/`trigger`/`noop`); the job performs the side effect. Deterministic, frozen-input tests (B11). |
| USER_ACTED | `last_user_action_at` set (Keep button **or** manual CLI execute) **or** a `confirmed`/`rejected` `pending_decisions` row for the GW (a 2.4b tap). |
| Keep-as-is | Telegram button on the H-120 warning, `callback_data="k:<gw>"`; `poll_once` routes `k:` to `deadguard.handle_keep`. |
| 2.5a action scope | **Captain/vice only** (the doc's "safe mode"), via the existing `run_lineup(live=True)`. No transfers/bench-reorder/chips/hits (2.5b). |
| Warning idempotency | New additive `gameweeks.deadguard_warned_at` column (don't re-send the warning each poll). |
| Trigger idempotency | `gameweeks.deadguard_triggered_at` (already exists) + terminal `state`. |

## Architecture

```
src/interface/deadguard.py     ← NEW: evaluate(), user_acted(), send_warning(), run_deadguard_job(), handle_keep()
src/data/schema.sql            ← + gameweeks.deadguard_warned_at
src/data/repository.py         ← + set_gameweek_state, mark_deadguard_warned, mark_deadguard_triggered, touch_user_action
src/config.py                  ← + deadguard_enabled / deadguard_warning_minutes / deadguard_trigger_minutes
src/scheduler.py               ← _maybe_load_key also loads on deadguard; build_scheduler registers deadguard_job
src/interface/telegram_interactive.py ← poll_once routes "k:" -> deadguard.handle_keep BEFORE handle_callback (so handle_callback's c/r guard is untouched)
src/cli.py                     ← execute-lineup/execute-transfer set last_user_action_at on live success
```
Layering: `interface/deadguard.py` imports `decisions.captain`, `execution.lineup`, `interface.telegram`,
`repository`, `config` — never `telegram_interactive`/`scheduler` (no cycles). The pure `evaluate` is a
self-contained decision helper. B2 holds (the router and decision layer gain nothing that imports the
interface).

## §1 Data model

`src/data/schema.sql` — additive column on the existing `gameweeks` table:
```sql
ALTER TABLE gameweeks ADD COLUMN deadguard_warned_at TIMESTAMP;
```
Note: `init_db` runs `schema.sql` via `executescript`. Because `gameweeks` is created with
`CREATE TABLE IF NOT EXISTS`, a plain `ALTER TABLE` would fail on an existing DB and re-error on a
fresh one after the first run. Implement this the way `_migrate_credentials` already does it in
`src/data/db.py`: add the column inside `init_db`'s migration step guarded by a "column exists?" check
(PRAGMA table_info), NOT as a raw line in `schema.sql`. (States are stored in the existing free-text
`state` column; no enum table.)

Repository helpers (mirror existing `_now()` + `conn.execute`/`commit` style):
- `set_gameweek_state(conn, gw, state)` — `UPDATE gameweeks SET state=? WHERE id=?`.
- `mark_deadguard_warned(conn, gw)` — `SET deadguard_warned_at=_now()`.
- `mark_deadguard_triggered(conn, gw)` — `SET deadguard_triggered_at=_now()`.
- `touch_user_action(conn, gw)` — `SET last_user_action_at=_now(), state='USER_ACTED'`.

## §2 State evaluation — pure, in `src/interface/deadguard.py`

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
        return "noop"                                  # deadline passed
    if mins <= trigger_min:
        return "noop" if triggered else "trigger"
    if mins <= warn_min:
        return "noop" if warned else "warn"
    return "noop"                                       # before the warning window
```

## §3 Detection — `user_acted(conn, gw)`

```python
def user_acted(conn, gw):
    g = conn.execute("SELECT last_user_action_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
    if g and g["last_user_action_at"]:
        return True
    n = conn.execute(
        "SELECT COUNT(*) c FROM pending_decisions WHERE gw=? AND status IN ('confirmed','rejected')",
        (gw,)).fetchone()["c"]
    return n > 0
```
SYSTEM_ACTED is read directly from the `gameweeks.last_system_action_at` column inside the job (passed
to `evaluate`).

## §4 The job — `run_deadguard_job(key, *, conn=None, now=None, cfg=None)`

Registered as `deadguard_job` in `build_scheduler` (every ~5 min) when a key is present and
`config.deadguard_enabled()`. Opens its own conn (`owns` pattern, like `auto_execute_job`).

```python
def run_deadguard_job(key, *, conn=None, now=None, cfg=None):
    from datetime import datetime, timezone
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

`_run_trigger(conn, key, gw)`:
- `repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")`.
- `caps = captain.get_captain_picks(conn)`; if not `caps["picks"]` →
  `set_gameweek_state(DEADGUARD_SKIPPED)` + `mark_deadguard_triggered` +
  `telegram.notify(kind="info", decision_type="deadguard", mode="deadguard",
  summary="Deadguard ran — no safe action (no data). Team unchanged.")` and return.
- else `try: lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True)` →
  on success `set_gameweek_state(DEADGUARD_EXECUTED)` + `mark_deadguard_triggered` +
  `telegram.notify(kind="executed", decision_type="deadguard", mode="deadguard",
  summary=f"Deadguard set captain: {caps['picks'][0]['web_name']}")`.
- `except SessionExpired` → `telegram.notify(kind="alert", ...)`; do **not** mark triggered (retry next
  tick); leave state DEADGUARD_ACTIVE. `except Exception as e` → `notify(kind="alert",
  summary=f"Deadguard failed: {type(e).__name__}")`; do not mark triggered.

All branches log to `activity_log` via the existing `run_lineup` (which logs `mode="manual"` — a known
pre-existing quirk) plus a `repository.log_activity(decision_type="deadguard", mode="deadguard", ...)`
entry for the resolution (B10). Notifications are best-effort (wrap in try/except, never crash the job —
the 2.4b pattern).

## §5 Keep-as-is button + manual-CLI USER_ACTED

`send_warning(conn, gw, *, mins)`:
```python
text = f"⏳ Deadguard will set your captain ~{mins} min before the deadline if you don't act.\nTap to keep your team as-is."
buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}]]
telegram.send_message(text, buttons=buttons)
```
(No-op when Telegram unconfigured — `send_message` already guards. The warning still gets `mark_warned`
so we don't loop; an unconfigured user simply gets no warning but deadguard still triggers at H-30.)

`handle_keep(conn, cq, *, session=None)`:
```python
chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
if chat_id != os.getenv(telegram.CHAT_ID_ENV):
    telegram.answer_callback_query(cq["id"], text="Not authorized", session=session); return
_, _, gw_s = cq.get("data", "").partition(":")
if gw_s.isdigit():
    repository.touch_user_action(conn, int(gw_s))     # idempotent: sets last_user_action_at + USER_ACTED
telegram.answer_callback_query(cq["id"], text="Kept as is ✅", session=session)
```

`telegram_interactive.poll_once` dispatch tweak — route the `k:` namespace to deadguard:
```python
cq = u.get("callback_query")
if cq:
    if cq.get("data", "").startswith("k:"):
        from src.interface import deadguard            # local import (no cycle)
        deadguard.handle_keep(conn, cq, session=session)
    else:
        handle_callback(conn, key, cq, session=session)
```

Manual-CLI USER_ACTED — in `src/cli.py`, the `execute-lineup` and `execute-transfer` commands call
`repository.touch_user_action(conn, <next_gw>)` after a successful **live** execution (these CLIs are
user-initiated by definition; this prevents deadguard from overriding a captain the user set on
purpose). The next GW id is `transfers._next_gw(conn)` (already used by the transfer executor).

## §6 Scheduler / config

```python
# config.py (mirrors unattended_enabled)
def deadguard_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("deadguard", {}).get("enabled", False))
def deadguard_warning_minutes(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("deadguard", {}).get("warning_window_minutes", 120)
def deadguard_trigger_minutes(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("deadguard", {}).get("trigger_window_minutes", 30)

# scheduler.py
def _maybe_load_key():
    if not (config.unattended_enabled() or config.telegram_interactive_enabled()
            or config.deadguard_enabled()):
        return None
    from .auth import master
    return master.get_master_key()

# build_scheduler, after the telegram_poll block:
if key is not None and config.deadguard_enabled():
    from .interface import deadguard
    scheduler.add_job(lambda: deadguard.run_deadguard_job(key),
                      CronTrigger(minute="*/5"), id="deadguard_job", replace_existing=True)
```
`config.yaml` already ships `deadguard: { enabled: true, warning_window_minutes: 120,
trigger_window_minutes: 30, ... }` — no config change needed. (Note: deadguard is enabled by default,
so a daemon run with the master key will register the job; without a key, no job and no key requested.)

## Safety & B-rule compliance
- **B8:** 2.5a sets only captain/vice via the existing bounded `run_lineup` — no transfers, chips,
  hits, or bench-reorder (those are 2.5b). The trigger path cannot escalate scope.
- **B4:** captain selection reuses the existing `captain.get_captain_picks` (no threshold change). Add a
  short `docs/decision-engine.md` changelog entry: "deadguard (2.5a) consumes the captain ranker for
  its captain/vice safety action; no threshold change."
- **B7:** token/chat/URL never logged; deadguard reaches the network only via `telegram.*`.
- **B9:** every deadguard outcome notifies (EXECUTED ✅ / SKIPPED 📊 / failure ❌); the H-120 warning
  notifies with the Keep button.
- **B10:** state transitions + the deadguard action log to `activity_log` (`mode="deadguard"`).
- **R3 / dry-run:** the agent never runs the live daemon or live execution; all tests inject fakes
  (frozen `now`, fake `run_lineup`/notify, in-memory `db`). No network/live.

## Testing — fixtures only, never live
- `evaluate` — frozen-time table: before-warning-window → noop; in warning window + not warned → warn;
  + warned → noop; in trigger window + not triggered → trigger; + triggered → noop; past deadline →
  noop; `last_system_action_at` set → system_acted; `user_acted` True → user_acted; terminal state →
  noop.
- `user_acted` — `last_user_action_at` set → True; a `confirmed`/`rejected` pending row → True; a
  `superseded`/`expired` pending row + no last_user_action_at → False; nothing → False.
- repository helpers — `set_gameweek_state`/`mark_deadguard_warned`/`mark_deadguard_triggered`/
  `touch_user_action` round-trip on a seeded `gameweeks` row (incl. the new column via the migration).
- `run_deadguard_job` (seeded GW + injected `now`/`cfg`/`conn`, monkeypatched `telegram.send_message`/
  `telegram.notify`, fake `run_lineup`): warn → sends warning + sets `deadguard_warned_at`; trigger →
  `run_lineup` called live + state DEADGUARD_EXECUTED + executed notify; no captain pick → state
  DEADGUARD_SKIPPED + info notify; `last_system_action_at` set → state SYSTEM_ACTED, no execute;
  `user_acted` → state USER_ACTED, no execute; deadguard disabled in cfg → returns None, no execute.
- `handle_keep` — wrong chat ignored (no state change); valid `k:<gw>` → state USER_ACTED +
  last_user_action_at; non-digit payload → no crash, just acks.
- `poll_once` — a `k:` callback routes to `deadguard.handle_keep` (monkeypatched), a `c:`/`r:` callback
  still routes to `handle_callback`.
- scheduler — `deadguard_job` registered when key present + `deadguard_enabled` (monkeypatched), not
  when disabled; `_maybe_load_key` loads when only deadguard is enabled.
- Full `pytest -q` stays green (deadguard enabled in config but the daemon/key never loaded in tests).

## Scope boundary
- **IN:** state machine (`evaluate`), USER_ACTED/SYSTEM_ACTED detection, H-120 warning + Keep-as-is
  button, H-30 trigger → captain/vice via `run_lineup`, EXECUTED/SKIPPED/notify, config accessors,
  `deadguard_warned_at` column + repo helpers, scheduler job, manual-CLI USER_ACTED.
- **OUT → 2.5b:** bench-order optimization, auto-sub of flagged players, single-transfer-if-flagged
  (new decision logic + position-reorder payload + stricter thresholds).
- **OUT → 2.5c:** late-news re-evaluation, undo, dashboard banner, multi-device.
- **OUT → 2.7:** B7 "freeze after repeated re-login failure".

## Definition of done (CLAUDE.md B14)
- With the daemon running (master key loaded) and `deadguard.enabled: true`: a Manual GW left untouched
  gets an H-120 warning with a working "Keep as is" button, and at H-30 (still untouched) deadguard
  sets captain/vice and notifies; a tapped "Keep as is", a 2.4b confirm/reject, a manual CLI execute,
  or an auto-exec all suppress deadguard (USER_ACTED/SYSTEM_ACTED); deadguard runs at most once per GW.
- All tests fixtures-only; suite stays green; no token/chat logged; `decision-engine.md` changelog
  updated; the agent never ran the live daemon.
- Manual smoke check (out of band, by the user): with the daemon up, force a GW into the trigger window
  and confirm the captain is set + notified, and that the Keep button suppresses it.
