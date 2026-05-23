# Emergency Override (Freeze / Kill-Switch) — Design (Phase 2.7)

**Status:** approved 2026-05-23
**Slice:** Phase 2.7 (the last Phase-2 safety capability). A single persisted freeze that halts ALL
autonomous FPL writes (the `auto_execute_job` auto-mode path and the entire `run_deadguard_job`),
toggleable by CLI and one-tap Telegram, plus the B7 auto-freeze after two consecutive re-login failures.
**Depends on:** 2.3 unattended scheduling (`src/scheduler.py: auto_execute_job`), 2.5a deadguard
(`src/interface/deadguard.py: run_deadguard_job`, `send_warning`), 2.4b Telegram interactive
(`src/interface/telegram_interactive.py: poll_once` callback router, chat whitelist), 2.1 session
(`src/auth/session.py: ensure_session`, the `credentials.relogin_failures` column, `mark_session_ok`).
**Source of truth:** CLAUDE.md B7 ("if re-login fails twice in a row, alert the user and freeze
auto-execution"), B8 (deadguard is a fallback), B9 (notifications), B10 (logging). No `docs/decision-engine.md`
change — the freeze gates *whether* autonomous execution runs; it changes no threshold/EP/FDR/alternative.

## Goal

Give the user a reliable "big red button" that stops the autonomous parts of the system from writing to
the real FPL account, and have the system freeze itself when authentication is broken. A lot now acts
unattended — auto-mode execution and deadguard (captain/vice, bench, transfer-if-flagged). 2.7 adds one
gate, checked at the top of each autonomous job, flipped from the phone (Telegram) or the terminal (CLI),
and flipped automatically on the B7 auth-failure condition.

## Decisions (locked — from brainstorming 2026-05-23)

| Decision | Choice |
|----------|--------|
| Blast radius | **Autonomous only.** Freeze blocks `auto_execute_job` (auto-mode execution) and the entire `run_deadguard_job`. A user's explicit one-tap **Confirm** in `handle_callback` is STILL honoured — freeze stops autonomy, not the user's deliberate action. |
| Persistence | **New `system_state` key/value table** (mirrors `telegram_state`). Read by the scheduler and deadguard. The existing `unattended.enabled`/`deadguard.enabled` config flags already cover permanent hard-disable, so no new config flag. |
| Freeze representation | Row under key `"freeze"` holding JSON `{since, reason, source}`. **Row present = frozen; absent = not frozen** (no tri-state bool). |
| Telegram surface | **Symmetric.** 🛑 Freeze button on the autonomous notifications (deadguard H-120 warning + auto-mode execution notices); the freeze-confirmation reply carries a ▶️ Unfreeze button. New `f:` / `u:` callback prefixes routed in `poll_once`. |
| CLI surface | `freeze [--reason "..."]`, `unfreeze`, `freeze-status` subcommands. **No master password prompt** — freeze is plaintext operational state; slamming the brakes must be ceremony-free. |
| Deadguard while frozen | **Fully dormant.** Freeze short-circuits `run_deadguard_job` at the very top: no H-120 warning, no H-30 trigger, no state transition. |
| B7 auto-freeze | Threshold **2** consecutive re-login failures. Same gate, `source="auto"`. `ensure_session` increments `credentials.relogin_failures` on `TokenRefreshError`; the autonomous orchestrators freeze when the counter reaches the threshold and alert once. `mark_session_ok` (successful refresh / re-init) already resets the counter to 0. |
| Unfreeze vs counter | Unfreeze does **not** reset `relogin_failures`. If auth is still broken, the next autonomous run re-freezes — correct. The real recovery is `init-fpl` (→ `mark_session_ok` → counter 0), then unfreeze. |

## Architecture

```
src/data/schema.sql              ← NEW: system_state(key TEXT PRIMARY KEY, value TEXT)
src/data/repository.py           ← NEW: get/set/clear_system_state; increment/get_relogin_failures
src/execution/override.py        ← NEW: is_frozen / status / freeze / unfreeze / maybe_auto_freeze (the gate + B7 policy)
src/scheduler.py                 ← auto_execute_job: freeze short-circuit at top; B7 maybe_auto_freeze on SessionExpired
src/interface/deadguard.py       ← run_deadguard_job: freeze short-circuit at top; _run_trigger: B7 on SessionExpired; send_warning gains 🛑 Freeze button
src/interface/telegram_interactive.py ← poll_once routes f:/u:; handle_freeze / handle_unfreeze
src/auth/session.py              ← ensure_session: increment_relogin_failures on TokenRefreshError
src/cli.py                       ← freeze / unfreeze / freeze-status subcommands; auth-status shows freeze + relogin_failures
docs/deadguard.md                ← "frozen → dormant" note
docs/runbook.md                  ← freeze/unfreeze operations + B7 auto-freeze recovery
```

**B2 layering holds.** `override` lives in `src/execution/` (alongside `router.py`/`executor.py`), imports
only the Data Layer (`repository`) — **no Telegram/interface import** (alerts are sent by the callers).
The auth layer only *maintains* the counter (`increment_relogin_failures`, a `repository` call it already
depends on); the freeze *policy* (≥2 → freeze) is in `override`, invoked by the orchestrators. The
scheduler and deadguard already import from `execution`, so the new `override` import adds no inversion.

## §1 Data model — `src/data/schema.sql` + `src/data/repository.py`

```sql
CREATE TABLE IF NOT EXISTS system_state (
  key TEXT PRIMARY KEY,
  value TEXT
);
```
`init_db` runs `executescript(schema.sql)` (idempotent `CREATE TABLE IF NOT EXISTS`), so the new table is
created on the next `init_db` for existing DBs — no `_migrate_*` helper needed (unlike the `ALTER TABLE`
column adds, a brand-new table just appears via the schema script).

Repository helpers (mirror the existing `get/set_telegram_state`):
```python
def get_system_state(conn, key):
    row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None

def set_system_state(conn, key, value):
    conn.execute(
        "INSERT INTO system_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()

def clear_system_state(conn, key):
    conn.execute("DELETE FROM system_state WHERE key=?", (key,))
    conn.commit()
```
B7 counter helpers over the existing `credentials.relogin_failures` column:
```python
def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures = relogin_failures + 1")
    conn.commit()
    return get_relogin_failures(conn)

def get_relogin_failures(conn):
    row = conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    return row["relogin_failures"] if row else 0
```

## §2 The gate — `src/execution/override.py`

```python
import json
from datetime import datetime, timezone
from src.data import repository

FREEZE_KEY = "freeze"
RELOGIN_FAILURE_THRESHOLD = 2          # B7: "twice in a row"

def is_frozen(conn):
    return repository.get_system_state(conn, FREEZE_KEY) is not None

def status(conn):
    raw = repository.get_system_state(conn, FREEZE_KEY)
    return json.loads(raw) if raw else None      # {since, reason, source} | None

def freeze(conn, *, reason, source):
    """Idempotent: a no-op (no re-log) if already frozen. source in {'user','auto'}."""
    if is_frozen(conn):
        return
    payload = {"since": datetime.now(timezone.utc).isoformat(), "reason": reason, "source": source}
    repository.set_system_state(conn, FREEZE_KEY, json.dumps(payload))
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"frozen ({source}): {reason}", executed=True)

def unfreeze(conn, *, source):
    """Idempotent: a no-op (no re-log) if not frozen. Does NOT reset relogin_failures."""
    if not is_frozen(conn):
        return
    repository.clear_system_state(conn, FREEZE_KEY)
    repository.log_activity(conn, decision_type="override", mode="override",
                            action_taken=f"unfrozen ({source})", executed=True)

def maybe_auto_freeze(conn):
    """B7 policy: freeze (source='auto') when consecutive re-login failures reach the threshold.
    Reads the counter (incremented by ensure_session); does NOT increment. Returns True only on the
    transition into a freeze, so the caller alerts exactly once."""
    if is_frozen(conn):
        return False
    if repository.get_relogin_failures(conn) >= RELOGIN_FAILURE_THRESHOLD:
        freeze(conn, reason="2 consecutive FPL re-login failures", source="auto")
        return True
    return False
```
No Telegram import: `freeze`/`unfreeze`/`maybe_auto_freeze` only touch the DB and the activity log.
Confirmation/alert messages are the callers' job (CLI prints; Telegram handlers reply; orchestrators
notify). `freeze`/`unfreeze` are idempotent so repeated taps/commands don't spam the activity log.

## §3 Checkpoints — `src/scheduler.py` + `src/interface/deadguard.py`

`auto_execute_job` (after `init_db(conn)`, before reading the gameweek row):
```python
from .execution import override
...
init_db(conn)
try:
    if override.is_frozen(conn):
        log.info("auto_execute_job skipped: frozen")
        return None
    ...
```
`run_deadguard_job` (after `init_db(conn)`, before the gameweek SELECT) — **fully dormant**:
```python
from src.execution import override
...
init_db(conn)
try:
    if override.is_frozen(conn):
        log.info("deadguard skipped: frozen")
        return None
    ...
```
Both skip with a `log.info` line only — **no `activity_log` row per tick** (auto runs every 15 min,
deadguard every 5 min; logging each skip would flood the log). Only freeze/unfreeze *transitions* are
logged (§2). `handle_callback` is **not** touched (autonomous-only).

## §4 B7 auto-freeze wiring — `src/auth/session.py` + the orchestrators

`ensure_session` today, on a failed refresh:
```python
except TokenRefreshError:
    repository.set_auth_state(conn, "expired")
    raise SessionExpired("refresh token no longer valid; re-run init-fpl")
```
Add the counter bump (auth layer only maintains the count — no `override` import, no freeze decision here):
```python
except TokenRefreshError:
    repository.set_auth_state(conn, "expired")
    repository.increment_relogin_failures(conn)
    raise SessionExpired("refresh token no longer valid; re-run init-fpl")
```
The autonomous orchestrators that catch `SessionExpired` then ask the gate to freeze at the threshold and
alert once. The freeze (a safety action) runs **first**, before any notification, so the brake is never
contingent on a Telegram send. **`auto_execute_job`** (covers the auto-mode config):
```python
except SessionExpired:
    froze = override.maybe_auto_freeze(conn)        # safety first, independent of telegram
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
**`deadguard._run_trigger`** SessionExpired branch (covers the common manual/hybrid + deadguard config,
where `unattended.enabled` is false so `auto_execute_job` returns early and never reaches its handler):
```python
except SessionExpired:
    froze = override.maybe_auto_freeze(conn)
    _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
    if froze:
        _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                               "Re-run init-fpl, then unfreeze.")
    return
```
Counting lives in one place (`ensure_session`), so there is no double-count regardless of which path hit
the failure; the threshold check is read-only. The interactive confirm path (`handle_callback`) still
*increments* via `ensure_session` but is intentionally **not** a freeze trigger (it's user-initiated and
a freeze wouldn't block it anyway). Once frozen, the §3 checkpoints make the next autonomous tick dormant,
so `maybe_auto_freeze`'s "only on transition" guard means the alert fires exactly once.

## §5 Telegram surface — `src/interface/{telegram_interactive,deadguard}.py`

`poll_once` routes by callback-data prefix (today: `k:` → keep, else confirm/reject). Add `f:`/`u:`:
```python
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
```
`handle_freeze` / `handle_unfreeze` reuse the existing chat-whitelist + `answer_callback_query` pattern
from `handle_callback`/`handle_keep`:
```python
def handle_freeze(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session); return
    override.freeze(conn, reason="frozen from Telegram", source="user")
    buttons = [[{"text": "▶️ Unfreeze", "callback_data": "u:1"}]]
    telegram.send_message("🛑 Auto-execution FROZEN. No autonomous changes will be made.",
                          buttons=buttons, session=session)
    telegram.answer_callback_query(cq["id"], text="Frozen", session=session)

def handle_unfreeze(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session); return
    override.unfreeze(conn, source="user")
    telegram.send_message("▶️ Auto-execution resumed.", session=session)
    telegram.answer_callback_query(cq["id"], text="Unfrozen", session=session)
```
The `f:`/`u:` callback-data payloads carry a constant suffix (`f:1`/`u:1`) so they parse uniformly with
the prefix router; the freeze is global, not per-GW, so no id is needed. **🛑 Freeze button placement:**
- `deadguard.send_warning` — add as a second button row beside "✅ Keep as is":
  `buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}], [{"text": "🛑 Freeze", "callback_data": "f:1"}]]`
- Auto-mode execution notices — append a Freeze button to the auto post-exec notification (`telegram.notify_plan` /
  `telegram_interactive.notify_plan` as used by `auto_execute_job`). (Manual/hybrid *pending* proposals keep
  Confirm/Reject only — they're user-controlled, not autonomous.)

## §6 CLI — `src/cli.py`

Three subcommands in `main` (subparser + `_*_cli` helper, following `_auth_status_cli`). No master password:
```python
p_freeze = sub.add_parser("freeze", help="halt all autonomous FPL execution (auto + deadguard)")
p_freeze.add_argument("--reason", default="frozen from CLI")
sub.add_parser("unfreeze", help="resume autonomous FPL execution")
sub.add_parser("freeze-status", help="show whether autonomous execution is frozen")
...
elif args.command == "freeze":      _freeze_cli(reason=args.reason)
elif args.command == "unfreeze":    _unfreeze_cli()
elif args.command == "freeze-status": _freeze_status_cli()
```
```python
def _freeze_cli(*, reason, conn=None):
    from .execution import override
    owns = conn is None; conn = conn or connect(cfg_db_path()); init_db(conn)
    override.freeze(conn, reason=reason, source="user")
    print("🛑 Frozen — autonomous execution (auto + deadguard) halted.")
    if owns: conn.close()
# _unfreeze_cli / _freeze_status_cli analogous; status prints override.status(conn) or "not frozen".
```
`_auth_status_cli` gains two lines: `frozen: <yes/no, since/reason/source>` and
`relogin_failures: <n>` so a single command shows both the auth and the freeze picture.

## Safety & B-rule compliance
- **B2:** `override` (execution) imports only the Data Layer; it sends no notifications. Auth maintains
  the counter via `repository`; the freeze policy is invoked by the orchestrators. No interface→lower or
  lower→interface inversion introduced.
- **B7:** the freeze IS B7's "freeze auto-execution after re-login fails twice in a row," now wired
  (counter incremented in `ensure_session`, threshold-frozen by the orchestrators, alerted once). Secrets
  unaffected — no token/cookie is read or logged by any 2.7 code; the freeze JSON holds only a reason string.
- **B8:** unchanged — deadguard's scope is untouched; freeze only makes it *not run*. No new chip/hit/multi path.
- **B9:** every freeze/unfreeze action notifies (Telegram reply on tap; CLI prints; B7 auto-freeze alerts);
  the autonomous-skip is silent-by-design (a per-tick "skipped: frozen" notification would be noise).
- **B10:** freeze/unfreeze/auto-freeze transitions log to `activity_log` (`decision_type="override"`,
  `mode="override"`). Per-tick skips are `log.info` only (no append-only spam).
- **B4:** no `docs/decision-engine.md` change — no threshold/EP/FDR/alternative is added or changed; this
  is an execution gate, not decision logic.
- **R3 / dry-run:** the agent never runs the live daemon or any `--live`; all tests inject fakes
  (in-memory DB, fake `route_fn`, fake notify/session). The CLI freeze/unfreeze touch only local DB state.

## Testing — fixtures only, never live
- `override` unit: `freeze`/`is_frozen`/`status` round-trip (`since`/`reason`/`source`); `freeze` idempotent
  (second call no-op, one log row); `unfreeze` clears + idempotent; `unfreeze` leaves `relogin_failures`
  untouched; `maybe_auto_freeze` — 1 failure → False/not frozen, 2nd → True/frozen `source="auto"`, a 3rd
  call while frozen → False (no re-log), and it never increments.
- Checkpoints: `auto_execute_job` with `override.freeze` set → returns `None`, the fake `route_fn` is NOT
  called (assert no execution); unfrozen → routes as today. `run_deadguard_job` frozen → returns `None`,
  `evaluate`/`send_warning`/`_run_trigger` not reached; unfrozen → behaves as 2.5. Existing auto/deadguard
  tests stay green (default = not frozen).
- Autonomous-only guarantee: `handle_callback` confirm path **executes** even when frozen (freeze does not
  gate it).
- B7 end-to-end (fakes): `ensure_session` with a refreshing session that 4xxs → `SessionExpired` raised +
  `relogin_failures` incremented; `auto_execute_job`/`_run_trigger` SessionExpired branch → on the 2nd
  consecutive failure `maybe_auto_freeze` freezes and the frozen alert is sent once (not on the 1st, not
  again on the 3rd); a subsequent `mark_session_ok` resets the counter to 0 (existing behavior, asserted).
- Telegram: `poll_once` routes `f:`/`u:` to the freeze/unfreeze handlers (state flips); a foreign chat id
  is rejected ("Not authorized", state unchanged); the freeze reply carries the Unfreeze button;
  `send_warning` payload includes the 🛑 Freeze button.
- CLI: `_freeze_cli`/`_unfreeze_cli`/`_freeze_status_cli` against an in-memory DB flip and report state with
  no master-password prompt; `_auth_status_cli` prints the freeze + `relogin_failures` lines.
- Full `pytest -q` green.

## Scope boundary
- **IN:** `system_state` table + repo helpers, `relogin_failures` counter helpers, `override` module
  (gate + B7 policy), the two job checkpoints, `ensure_session` increment + orchestrator freeze/alert,
  Telegram `f:`/`u:` (handlers + buttons on deadguard warning + auto notices), CLI `freeze`/`unfreeze`/
  `freeze-status` + `auth-status` lines, `deadguard.md`/`runbook.md` notes.
- **OUT (explicitly not now):** gating the interactive Confirm (`handle_callback`) — autonomous-only by
  decision; a config flag for freeze (the existing `unattended.enabled`/`deadguard.enabled` cover
  hard-disable); a freeze auto-expiry/TTL; per-capability partial freezes (freeze is all-or-nothing);
  a freeze button on manual/hybrid pending proposals (user already controls those).
- **OUT → 2.5c (later):** late-news re-evaluation, undo, dashboard banner, multi-device — a dashboard
  freeze indicator naturally belongs there.

## Definition of done (CLAUDE.md B14)
- With the daemon running: `fpl-autopilot freeze` (or the 🛑 Telegram button) halts the next
  `auto_execute_job` (auto mode) and `run_deadguard_job` — both return without any FPL write, and the
  deadguard sends no H-120 warning while frozen; `fpl-autopilot unfreeze` (or ▶️ Telegram) resumes them.
  A user's explicit Telegram **Confirm** still executes while frozen.
- B7: after two consecutive failed token refreshes the system freezes itself, alerts once, and stays
  dormant until `init-fpl` (counter → 0) + unfreeze; the freeze JSON carries `source="auto"`.
- All tests fixtures-only; suite stays green; no secret/token logged; no `decision-engine.md` change;
  `deadguard.md`/`runbook.md` updated; the agent never ran the live daemon.
- Manual smoke check (out of band, by the user): `freeze` then watch a scheduled auto/deadguard tick
  no-op in the logs; `freeze-status` reflects it; `unfreeze` restores normal behavior.
