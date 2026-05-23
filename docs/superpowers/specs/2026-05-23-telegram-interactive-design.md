# Telegram Interactive Confirm — Design (Phase 2.4b)

**Status:** approved 2026-05-23
**Slice:** Phase 2.4b (Decision Automation — interactive one-tap confirm). The second half of 2.4;
delivers B9's "the primary interface during a gameweek" for Manual/Hybrid users.
**Depends on:** 2.4a Telegram outbound notifier (`src/interface/telegram.py`: `is_configured`,
`send_message` with `buttons=`, `notify`, `notify_plan`), 2.3 Mode Router (`route_gameweek` plan +
the `notify` route), 2.3c unattended scheduling (`auto_execute_job`, `_maybe_load_key`,
`build_scheduler`), 2.2 executors (`run_lineup`, `run_transfer` with `confirm_fn`), 2.1 auth
(`ensure_session`/`SessionExpired`).
**Builds on the 2.4a hook:** `send_message(text, *, buttons=...)` already accepts an
`inline_keyboard` payload; 2.4b adds the inbound half and the confirm→execute loop.

## Goal

A Manual/Hybrid user gets a "decision pending" ping with **Confirm / Reject** buttons before the
deadline and acts with one tap — **without being at a terminal**. The running daemon polls Telegram
`getUpdates`, and on Confirm re-runs the decision, verifies it still matches what the ping showed,
and executes (or re-notifies if it changed). The agent never runs the live poller or live execution
(R3) — the user runs `serve`/scheduler with the master password.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Inbound transport | **`getUpdates` long-poll as a recurring job inside the existing daemon** (no public URL / webhook). Fits the APScheduler model in `src/scheduler.py`. |
| Confirm semantics | **Re-run + verify; re-notify if changed.** On tap, recompute; if identity matches what was shown → execute with fresh data; if changed → do NOT execute, send a fresh pending ping. Never executes something stale or unapproved (B9 trust). |
| Button scope | **Confirm + Reject only.** Captain/lineup + single transfer. "Modify" deferred (a later 2.4c — needs a stateful multi-message flow). |
| Pending state | **Dedicated `pending_decisions` table** (status lifecycle) — clean idempotency + re-notify chain; keeps `activity_log` append-only (B10). |
| Idempotency | **Status gate** (a row executes at most once) + **durable update offset** (`telegram_state`) so a daemon restart can't replay an un-acked tap. |
| Opt-in | `telegram.interactive` config flag (default false). When enabled, the daemon loads the master key and registers the poll job. |

## Architecture

```
src/data/schema.sql           ← + pending_decisions, telegram_state tables
src/data/repository.py        ← + create/get/set pending_decision, get/set telegram_state
src/execution/router.py       ← plan entries gain `identity` (additive; no telegram import, no logic change)
src/interface/telegram.py     ← + get_updates(), answer_callback_query() (transport primitives)
src/interface/telegram_interactive.py  ← NEW: is_enabled, notify_plan, send_pending, poll_once, handle_callback
src/scheduler.py              ← auto_execute_job routes pending entries via interactive notify when enabled;
                                _maybe_load_key loads on interactive too; build_scheduler registers telegram_poll
src/config.py                 ← + telegram_interactive_enabled(cfg)
config.yaml                   ← telegram: { interactive: false }
```

Layering (B2): the router stays pure (emits data only). `telegram_interactive` is Interface; it may
consume the Decision layer (re-run ranker/suggester to verify) and call the executors. The decision
logic itself is unchanged → **no `decision-engine.md` change**.

## §1 Data model — `src/data/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS pending_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gw INTEGER,
  decision_type TEXT NOT NULL,        -- 'lineup' | 'transfer'
  identity_json TEXT NOT NULL,        -- lineup: {"captain_id","vice_id"}; transfer: {"out_id","in_id"}
  summary TEXT NOT NULL,              -- the human text shown in the ping
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|rejected|superseded|expired|failed
  created_at TIMESTAMP,
  resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_state (
  key TEXT PRIMARY KEY,              -- 'update_offset'
  value TEXT
);
```

Repository helpers (in `src/data/repository.py`, mirroring existing style):
- `create_pending_decision(conn, *, gw, decision_type, identity, summary) -> int` (inserts, returns id, commits).
- `get_pending_decision(conn, pid) -> row | None`.
- `set_pending_status(conn, pid, status) -> None` (sets `status` + `resolved_at`, commits).
- `get_telegram_state(conn, key) -> str | None`; `set_telegram_state(conn, key, value) -> None`.

Router enrichment (additive, like 2.4a `summary`/`executed`): each plan entry also carries
`identity` — captain: `{"captain_id": caps["picks"][0]["player_id"], "vice_id": caps["vice_player_id"]}`;
transfer: `{"out_id": top["out"]["player_id"], "in_id": top["in"]["player_id"]}`.

## §2 Transport primitives — `src/interface/telegram.py`

```python
def get_updates(offset, *, session=None) -> list:
    """Telegram getUpdates. Returns [] when unconfigured or on any error (never raises,
    never logs token). offset (int|None) acks prior updates. Returns the 'result' list."""

def answer_callback_query(callback_query_id, *, text=None, session=None) -> bool:
    """Ack a callback so the client stops spinning. Returns False when unconfigured/on error."""
```
Both follow the 2.4a `send_message` pattern: `is_configured()` gate → `session or requests.Session()`
→ **POST** `{API_BASE}/bot{token}/...` with a json body (matching `send_message`, so the existing
`_FakeSession.post` test double works) → list/True on `ok`, else `[]`/False; catch all; never raise;
never log token/chat/URL. `get_updates` posts `{"offset": offset, "timeout": 0}` and returns the
`result` list.

## §3 Outbound interactive ping — `src/interface/telegram_interactive.py`

```python
def is_enabled(cfg=None) -> bool:
    """telegram.is_configured() AND config.telegram_interactive_enabled(cfg)."""

def notify_plan(conn, plan, *, gw, mode):
    """Interactive variant. For each entry: executed -> telegram.notify(kind='executed', ...)
    (the 2.4a ✅ confirmation, reused); pending -> send_pending(...)."""

def send_pending(conn, entry, *, gw, mode):
    """Create a pending_decisions row, then send the buttoned ping."""
    pid = repository.create_pending_decision(conn, gw=gw, decision_type=_dtype(entry["decision"]),
                                             identity=entry["identity"], summary=entry["summary"])
    buttons = [[{"text": "✅ Confirm", "callback_data": f"c:{pid}"},
                {"text": "❌ Reject",  "callback_data": f"r:{pid}"}]]
    text = f"📊 Decision pending\n{entry['summary']}\nConfirm or reject below."
    telegram.send_message(text, buttons=buttons)
```
`_dtype` maps the plan's `decision` ("captain"→"lineup", "transfer"→"transfer"). `callback_data`
`c:<id>`/`r:<id>` is far under Telegram's 64-byte cap.

Scheduler wiring (`auto_execute_job`, guarded best-effort, replacing the bare 2.4a notify call):
```python
if telegram_interactive.is_enabled(cfg):
    telegram_interactive.notify_plan(conn, plan, gw=row["id"], mode=mode)
else:
    telegram.notify_plan(conn, plan, mode=mode)        # unchanged 2.4a path (still used + tested)
```

## §4 Inbound poll + callback handler — `src/interface/telegram_interactive.py`

```python
def poll_once(key, *, conn=None, session=None):
    if not is_enabled():
        return
    owns = conn is None
    conn = conn or connect(db_path())
    init_db(conn)
    try:
        offset = repository.get_telegram_state(conn, "update_offset")
        offset = int(offset) if offset is not None else None
        for u in telegram.get_updates(offset, session=session):
            cq = u.get("callback_query")
            if cq:
                handle_callback(conn, key, cq, session=session)
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
    finally:
        if owns:
            conn.close()
```
`poll_once` opens/closes its own conn per run when none is passed (the `owns` pattern, like
`auto_execute_job`); tests pass `conn=db`. Offset persisted **after each handled update** →
at-least-once; the status gate makes reprocessing safe (no double-execute).

`handle_callback(conn, key, cq, *, session=None)`:
1. **Chat whitelist:** `chat_id = str(cq["message"]["chat"]["id"])`; if `!= os.getenv(CHAT_ID_ENV)` →
   `telegram.answer_callback_query(cq["id"], text="Not authorized")` and return (the bot can be
   messaged by anyone).
2. Parse `cq["data"]` → `action` ("c"/"r"), `pid`. `row = get_pending_decision(conn, pid)`.
   **Idempotency:** if `row is None` or `row["status"] != "pending"` → answer "Already handled", return.
3. **Reject** (`r`): `set_pending_status(pid, "rejected")`; `repository.log_activity(decision_type=row["decision_type"],
   mode=config.mode(), action_taken="rejected via telegram", executed=False)`; answer "Rejected ❌";
   `telegram.notify(..., kind="info", summary="Rejected — no change made.")`; return.
4. **Confirm** (`c`):
   - **Deadline guard:** read the GW deadline; if `now > deadline` → `set_pending_status(pid,"expired")`,
     answer "Deadline passed", return.
   - **Re-run + verify** (recompute via the existing Decision layer, same calls the router uses):
     - lineup: `caps = captain.get_captain_picks(conn)`; `match = caps["picks"] and caps["picks"][0]["player_id"] == identity["captain_id"]`.
     - transfer: `sugg = transfers.get_transfer_suggestions(conn)`; `top = sugg["suggestions"][0]`;
       `match = top["out"]["player_id"]==identity["out_id"] and top["in"]["player_id"]==identity["in_id"]`.
   - **match** → execute live via the existing executor with an auto-approve confirm:
     `run_lineup(conn, key, live=True, confirm_fn=lambda d: True)` /
     `run_transfer(conn, key, rank=1, live=True, confirm_fn=lambda d: True)`; on success
     `set_pending_status(pid,"confirmed")` + `telegram.notify(kind="executed", summary=<diff>)`.
   - **changed** → `set_pending_status(pid,"superseded")`; build a fresh plan-like entry for the new
     recommendation and `send_pending(...)` (new row + new ping); answer "Recommendation changed — see new message".
   - **execution failure** (`SessionExpired`/`ExecutorError`/any) → caught; `set_pending_status(pid,"failed")`;
     `telegram.notify(kind="alert", summary=...)` (for `SessionExpired` reuse the 2.4a auth-alert copy);
     never crash the poller.
5. Always `telegram.answer_callback_query(cq["id"], ...)` to clear the spinner.

## §5 Scheduler / config — `src/scheduler.py`, `src/config.py`, `config.yaml`

```python
# config.py
def telegram_interactive_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("telegram", {}).get("interactive", False))

# scheduler.py
def _maybe_load_key():
    if not (config.unattended_enabled() or config.telegram_interactive_enabled()):
        return None
    from .auth import master
    return master.get_master_key()

def build_scheduler(scheduler=None, key=None):
    ... existing refresh + (auto_execute when key) ...
    if key is not None and config.telegram_interactive_enabled():
        scheduler.add_job(lambda: telegram_interactive.poll_once(key),
                          CronTrigger(second="*/20"), id="telegram_poll", replace_existing=True)
    return scheduler
```
Reuses the existing `CronTrigger` import (the auto_execute job uses `CronTrigger(minute="*/15")`).
Off by default → no key requested, no poll job (headless refresh keeps working).

`config.yaml` gains:
```yaml
telegram:
  interactive: false
```

## Safety & B-rule compliance
- **B7:** token/chat/URL never logged (transport builds the URL internally; no logging in the module).
- **B8:** Confirm only ever triggers a single captain set or single transfer through the existing
  bounded executors (`run_lineup`/`run_transfer`, which forbid chips/hits/multi). The verify path
  cannot escalate scope.
- **B9:** every outcome notifies the user (confirmed ✅ / rejected / superseded / expired / failed ❌).
- **B10:** rejects/executions log to the append-only `activity_log`; mutable lifecycle lives only in
  `pending_decisions`.
- **B4:** decision logic unchanged; verify reuses the existing ranker/suggester. No `decision-engine.md` change.
- **Idempotency:** status gate (execute-at-most-once) + durable offset (no replay on restart).
- **R3 / dry-run:** the agent never runs the live poller or live execution; the user runs the daemon.
  All tests inject fakes (fake `get_updates` payloads, fake ranker/suggester/executors); no network/live.

## Testing — fixtures only, never live
- **repository:** create/get/set `pending_decision`; get/set `telegram_state` offset (round-trip + defaults).
- **router:** notify plan entries carry `identity` (captain ids / out+in ids).
- **telegram transport:** `get_updates` (unconfigured → `[]`; parses `result`; passes `offset`;
  network error → `[]`); `answer_callback_query` (unconfigured → False; posts when configured).
- **telegram_interactive:**
  - `send_pending` creates one `pending_decisions` row (status `pending`) and sends a message whose
    buttons are `c:<id>`/`r:<id>`; no-op when not enabled.
  - `handle_callback`: reject → status `rejected`, no execution; confirm+match (fake ranker/suggester)
    → executor called (fake), status `confirmed`; confirm+changed → status `superseded`, a NEW pending
    row created, executor NOT called; non-pending row → ignored (idempotent); wrong chat id → ignored,
    no execution; past deadline → status `expired`, no execution; executor raises `SessionExpired`
    → status `failed`, alert sent, no crash.
  - `poll_once`: reads offset, dispatches a fake callback update, advances + persists offset; no-op when disabled.
- **scheduler/config:** `telegram_interactive_enabled` accessor; `_maybe_load_key` loads when
  interactive enabled (and returns None when both off); `build_scheduler` registers `telegram_poll`
  only with key present + interactive enabled (and not otherwise); existing jobs still present.
- Full `pytest -q` stays green; the existing 2.4a scheduler tests still pass (interactive off by default).

## Scope boundary
- **IN:** the two tables + repository helpers, router `identity` enrichment, the two transport
  primitives, `telegram_interactive` (send_pending/poll_once/handle_callback/notify_plan), scheduler +
  config opt-in, confirm/reject for captain & single transfer with re-run+verify+re-notify.
- **OUT → 2.4c (future):** the "Modify" button (cycle transfer rank / pick vice) and its stateful
  multi-message flow.
- **OUT → 2.5 deadguard:** using `gameweeks.state` / `last_user_action_at` as a real state machine;
  2.4b only writes `pending_decisions` + `activity_log` (it may set `last_user_action_at` opportunistically
  on a confirm, but the PENDING/USER_ACTED/DEADGUARD_ACTIVE machine is 2.5).
- **OUT → 2.7:** the B7 "freeze after repeated failure" behavior.

## Definition of done (CLAUDE.md B14)
- With `telegram.interactive: true` + `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` + the daemon holding the
  master key: a Manual/Hybrid pending decision arrives with Confirm/Reject buttons; Confirm re-runs +
  verifies and either executes (✅ confirmation) or re-notifies (recommendation changed); Reject logs a
  dismissal; each decision executes at most once; taps from other chats are ignored; past-deadline taps
  don't execute.
- All tests fixtures-only; suite stays green; no token/chat ever logged; the agent never runs the live
  poller or live execution.
- Manual smoke check (out of band, by the user): enable interactive, run `serve`, trigger a pending
  decision in-window, tap Confirm on the phone, verify execution + the `activity_log`/`pending_decisions` rows.
