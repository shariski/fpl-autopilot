# Deadguard Undo (Transfer) — Design (Phase 2.5c-2)

**Status:** approved 2026-05-24
**Slice:** Phase 2.5c-2 (second of the 2.5c bundle; 2.5c-1 late-news re-eval is done, 2.5c-3 dashboard is separate).
One-tap revert of the single free transfer deadguard made, before the deadline. Transfer-only.
**Depends on:** 2.5a/2.5b deadguard (`_run_trigger`, `_notify`, `run_deadguard_job`, `gameweeks.state`,
`touch_user_action`), 2.2 executor (`build_transfer_payload`, `apply_transfers`, `fetch_current_picks`,
`TRANSFERS_URL`, `ExecResult.request`), `transfers._next_gw`, 2.4b Telegram interactive (`poll_once`
callback router, chat whitelist, `handle_callback(key)`), the `players` table (current prices).
**Source of truth:** `docs/deadguard.md` §"User opens the dashboard after deadguard executed" — *"the dashboard
clearly shows what changed … with 'Undo' buttons where applicable (transfers can be reverted before deadline)."*
This slice delivers the **Telegram + CLI** half of that; the dashboard surface is 2.5c-3.

## Goal

After deadguard makes a single free transfer for an absent user, let the returning user reverse it in one tap
(or one CLI command) any time before the deadline — restoring the sold player and the free transfer. A reverse
transfer nets to **free** while still pending pre-deadline, which is exactly why deadguard.md scopes undo to
transfers. Lineup changes (captain/vice/bench) are trivially user-adjustable and out of scope.

## Decisions (locked — brainstorming 2026-05-24)

| Decision | Choice |
|----------|--------|
| Scope | **Transfer only.** Undo reverses deadguard's one free transfer. Captain/vice/bench are user-adjustable and not "undone." |
| Mechanism | **Reverse transfer** (sell the bought `in_id`, buy back the sold `out_id`) via the existing executor. FPL nets it to free pre-deadline (B6: no native "reset" endpoint). |
| Undo target source | **Recorded on the gameweek** when deadguard's transfer succeeds (`out_id`/`in_id` read from the live `ExecResult.request` body) — NOT parsed from `activity_log` (which `run_transfer` mislabels `mode="manual"`). |
| Surface | **Telegram ↩️ Undo button** (new `z:{gw}` callback on the deadguard transfer notice) **+ CLI** `undo-transfer` (dry-run default, `--live` + typed confirm). |
| Availability | Only when: a deadguard transfer is recorded, not already undone, and `now < deadline`. After undo → `USER_ACTED` (also stops 2.5c-1 re-eval). |
| Freeze (2.7) | **Not freeze-gated** — undo is a deliberate user action (like the interactive Confirm); the 2.7 autonomous-only freeze does not block it. |
| Failure safety | If `in_id` is no longer in the squad (user already moved it) or the deadline passed → notify "couldn't undo" and do NOT make a new transfer (never turn an undo into a 2nd transfer / a hit). |

## Architecture

```
src/data/schema.sql           ← gameweeks.deadguard_transfer_json TEXT, deadguard_transfer_undone_at TIMESTAMP
src/data/db.py                ← _migrate_gameweeks adds both columns (idempotent)
src/data/repository.py        ← set_deadguard_transfer / get_deadguard_transfer / mark_deadguard_transfer_undone
src/execution/transfer.py     ← NEW run_undo_transfer(conn, key, *, out_id, in_id, live, confirm_fn, session)
src/interface/deadguard.py    ← _run_trigger records the transfer + sends the ↩️ Undo button; NEW run_undo(conn, key, gw, *, live, confirm_fn, now)
src/interface/telegram_interactive.py ← poll_once routes z:; NEW handle_undo(conn, key, cq, session)
src/cli.py                    ← undo-transfer subcommand + _undo_transfer_cli
docs/deadguard.md             ← undo behavior note
```
B2 holds: `run_undo_transfer` is execution (reuses `build_transfer_payload`/`apply_transfers`); `run_undo`
orchestrates in the interface/jobs layer (like `handle_keep`); Telegram glue in `telegram_interactive`; CLI in
`cli`. No new lower→interface inversion. **No `decision-engine.md` change** — undo is an execution action, not a
decision (no xP/threshold; B4 doesn't apply).

## §1 Storage + recording — `gameweeks` columns, repo helpers, `_run_trigger`

Two new `gameweeks` columns (mirror the existing `deadguard_warned_at`/`deadguard_reeval_alerted_at` pattern):
`deadguard_transfer_json TEXT` and `deadguard_transfer_undone_at TIMESTAMP`. `db._migrate_gameweeks` adds both
idempotently; `schema.sql` carries them for fresh DBs.

Repository helpers:
```python
def set_deadguard_transfer(conn, gw, out_id, in_id):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_json=? WHERE id=?",
                 (json.dumps({"out_id": out_id, "in_id": in_id}), gw))
    conn.commit()

def get_deadguard_transfer(conn, gw):
    row = conn.execute("SELECT deadguard_transfer_json FROM gameweeks WHERE id=?", (gw,)).fetchone()
    return json.loads(row["deadguard_transfer_json"]) if row and row["deadguard_transfer_json"] else None

def mark_deadguard_transfer_undone(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_undone_at=? WHERE id=?", (_now(), gw))
    conn.commit()
```

In `_run_trigger`, the transfer step records the undo target and flags that a transfer was applied. Today the
block sets `transfer_note = "transfer applied"` on `tr.ok`; add the recording from the live result body and a
boolean, and after the existing executed `_notify`, send the Undo button when a transfer was applied:
```python
    transfer_applied = False
    try:
        rank = _pick_flagged_transfer(conn, cfg)
        if rank is not None:
            tr = transfer_exec.run_transfer(conn, key, rank=rank, live=True, confirm_fn=lambda d: True)
            if getattr(tr, "ok", False):
                transfer_note = "transfer applied"
                transfer_applied = True
                body = tr.request["body"]["transfers"][0]      # {element_in, element_out, ...}
                repository.set_deadguard_transfer(conn, gw, body["element_out"], body["element_in"])
            else:
                transfer_note = "transfer failed"
                _notify(conn, "alert", "Deadguard: flagged-player transfer did not complete.")
    except Exception as e:
        transfer_note = f"transfer failed ({type(e).__name__})"
        log.exception("deadguard transfer step failed")
        _notify(conn, "alert", f"Deadguard transfer failed: {type(e).__name__}")
    # ... existing summary log_activity + executed _notify unchanged ...
    if transfer_applied:
        try:
            telegram.send_message("↩️ Undo the transfer? Free before the deadline.",
                                  buttons=[[{"text": "↩️ Undo", "callback_data": f"z:{gw}"}]])
        except Exception:
            log.exception("deadguard undo-button send failed")
```
(The Undo button is a separate buttoned message, mirroring 2.7's auto-mode freeze-button pattern; it only does
anything when Telegram is configured + `telegram.interactive` is on so `poll_once` processes the callback.)

## §2 Reverse-transfer executor — `src/execution/transfer.py`

```python
def run_undo_transfer(conn, key, *, out_id, in_id, live=False, confirm_fn=None, session=None):
    """Reverse a transfer: sell in_id (bought earlier), buy back out_id. Free pre-deadline (FPL nets it)."""
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)
    selling_price = next((p["selling_price"] for p in current if p["element"] == in_id), None)
    if selling_price is None:                       # user already moved it / not in squad
        raise executor.ExecutorError(f"player {in_id} not in current squad — cannot undo")
    row = conn.execute("SELECT price FROM players WHERE id=?", (out_id,)).fetchone()
    if row is None:
        raise executor.ExecutorError(f"player {out_id} not found — cannot undo")
    purchase_price = round(row["price"] * 10)
    event = transfers._next_gw(conn)
    payload = executor.build_transfer_payload(entry=entry, event=event, element_out=in_id, element_in=out_id,
                                              selling_price=selling_price, purchase_price=purchase_price)
    diff = f"UNDO: OUT {in_id} -> IN {out_id}"
    url = executor.TRANSFERS_URL.format(entry=entry)
    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken="undo aborted",
                                executed=False, exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True, request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)
    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    repository.log_activity(conn, decision_type="transfer", mode="manual",
                            action_taken=(f"undo: OUT {in_id} IN {out_id}" if live else "undo dry-run"),
                            executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
```
Mirrors `run_transfer`'s shape (dry-run-first, typed-confirm gate, `ExecResult`). Uses the live picks for
`in_id`'s selling price and the `players` table for `out_id`'s current price.

## §3 Orchestration — `deadguard.run_undo`

```python
def run_undo(conn, key, gw, *, live=True, confirm_fn=None, now=None):
    target = repository.get_deadguard_transfer(conn, gw)
    if target is None:
        _notify(conn, "info", "Nothing to undo — deadguard made no transfer this gameweek."); return
    row = conn.execute("SELECT deadline_utc, deadguard_transfer_undone_at FROM gameweeks WHERE id=?",
                       (gw,)).fetchone()
    if row["deadguard_transfer_undone_at"]:
        _notify(conn, "info", "Already undone."); return
    now = now or datetime.now(timezone.utc)
    if row["deadline_utc"] and now >= datetime.fromisoformat(row["deadline_utc"]):
        _notify(conn, "info", "Too late to undo — the deadline has passed."); return
    try:
        result = transfer_exec.run_undo_transfer(conn, key, out_id=target["out_id"], in_id=target["in_id"],
                                                 live=live, confirm_fn=confirm_fn)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Undo: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. Re-run init-fpl, then unfreeze.")
        return
    except Exception as e:
        log.exception("deadguard undo failed")
        _notify(conn, "alert", f"Undo failed: {type(e).__name__} — the squad may have changed."); return
    if getattr(result, "ok", False) and not getattr(result, "dry_run", False):
        repository.mark_deadguard_transfer_undone(conn, gw)
        repository.touch_user_action(conn, gw)          # -> USER_ACTED (also stops 2.5c-1 re-eval)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"undo transfer: restored {target['out_id']}, removed {target['in_id']}",
                                inputs=target, executed=True)
        _notify(conn, "executed", "Reverted deadguard's transfer — sold player restored, free transfer back.")
    elif not getattr(result, "dry_run", False):
        _notify(conn, "alert", "Undo did not complete — the squad may have changed.")
    return result
```
Guards (nothing-to-undo / already-undone / past-deadline) notify-and-return. Success → `mark_*` +
`touch_user_action` (USER_ACTED) + log + notify. `live=True` from the Telegram tap; the CLI passes `live`/confirm.

## §4 Telegram — `handle_undo` + `poll_once` routing

`poll_once` already routes `k:`/`f:`/`u:`/`c:`/`r:`. Add `z:` → `handle_undo(conn, key, cq, session=session)`
(undo executes a live write, so it needs `key`, which `poll_once` already passes to `handle_callback`):
```python
def handle_undo(conn, key, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session); return
    action, _, gw_s = cq.get("data", "").partition(":")
    if not gw_s.isdigit():
        telegram.answer_callback_query(cq["id"], text="Unknown action", session=session); return
    from src.interface import deadguard
    deadguard.run_undo(conn, key, int(gw_s), live=True, confirm_fn=lambda d: True)
    telegram.answer_callback_query(cq["id"], text="Undo requested", session=session)
```
(`run_undo` sends the actual outcome notification; the callback answer is just the tap ack. The chat-whitelist +
`gw_s.isdigit()` guards mirror `handle_keep`.)

## §5 CLI — `undo-transfer`

`undo-transfer [--live]` subcommand + `_undo_transfer_cli(live=..., conn=None, confirm_fn=None)`: loads the
master key (undo is a live FPL write, unlike the plaintext freeze), resolves the next GW (`transfers._next_gw`),
and calls `run_undo(conn, key, gw, live=live, confirm_fn=<typed-confirm>)`. Dry-run by default; `--live` requires
a typed confirmation, matching `execute-transfer`/`execute-lineup`. (The agent never runs `--live` — R3.)

## Safety & B-rule compliance
- **B8:** undo is a single **free** reverse transfer via the bounded executor — no hit/chip/multi. On any doubt
  (`in_id` gone, deadline passed) it refuses rather than making a new transfer.
- **B6:** no native FPL "reset" — undo submits the reverse transfer (unofficial-API-safe); selling/purchase
  prices read from live picks + the `players` table.
- **B7:** undo never logs a token/cookie; a `SessionExpired` during undo feeds `override.maybe_auto_freeze`.
- **B9:** every undo outcome notifies (success / nothing-to-undo / already-undone / too-late / failure).
- **B10:** the undo logs to `activity_log` (`mode="deadguard"`); the executor logs its own transfer row.
- **R3 / dry-run:** the agent never runs the live daemon or `--live`; tests are fixtures-only (fake
  session/picks/apply, in-memory DB, frozen clock).

## Testing — fixtures only
- `run_undo_transfer`: builds the reverse payload (element_out=in_id, element_in=out_id; selling from picks,
  purchase from players); `in_id` not in squad → `ExecutorError`; dry-run vs live; logs.
- `run_undo` guards: no recorded transfer → "nothing to undo" notify, no executor call; already-undone → notify;
  `now >= deadline` → notify, no executor call. Success → `run_undo_transfer` called, `deadguard_transfer_undone_at`
  set, state `USER_ACTED`, "executed" notify. Not-ok → alert. `SessionExpired` → alert (+ maybe_auto_freeze).
- `_run_trigger`: a successful transfer records `{out_id,in_id}` (from the fake `tr.request` body) and sends the
  ↩️ Undo button (`z:{gw}`); no transfer → no record, no button. (Existing 2.5b trigger tests stay green.)
- `poll_once` routes `z:` → `handle_undo`; wrong chat → "Not authorized", no undo; non-digit gw → "Unknown action".
- CLI: dry-run does not write; `--live` calls `run_undo(live=True)`.
- column + migration + repo helpers round-trip. Full `pytest -q` green.

## Scope boundary
- **IN:** undo storage (2 columns + helpers + recording in `_run_trigger`), `run_undo_transfer`, `run_undo`,
  Telegram `z:` button/handler, CLI `undo-transfer`, deadguard.md note.
- **OUT → 2.5c-3:** the dashboard "what changed + Undo" surface + multi-device + the dashboard freeze/deadguard banner.
- **OUT (deferred):** undo of the lineup (captain/bench) — user-adjustable, low value; undo of a *user's own*
  transfer (only deadguard's recorded transfer is undoable here).

## Definition of done (CLAUDE.md B14)
- With the daemon running: after deadguard makes a transfer, the user gets an ↩️ Undo button; tapping it before
  the deadline reverses the transfer (sold player restored, free transfer back), transitions the GW to
  USER_ACTED, and notifies; tapping after the deadline (or when the squad changed) refuses safely with a notice.
  `fpl-autopilot undo-transfer --live` does the same from the terminal. No hit/chip/multi ever.
- All tests fixtures-only; suite green; no token logged; no `decision-engine.md` change; `deadguard.md` updated;
  the agent never ran live.
- Manual smoke check (out of band, by the user): force a GW to a deadguard transfer with a synthetic future
  deadline, tap Undo (or run `undo-transfer --live`), confirm the reverse transfer + USER_ACTED + notification;
  re-tap → "already undone".
