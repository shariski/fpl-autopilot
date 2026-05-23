# Telegram Outbound Notifier — Design (Phase 2.4a)

**Status:** approved 2026-05-23
**Slice:** Phase 2.4a (Decision Automation — outbound notifications). The first half of 2.4 Telegram; delivers B9 "notifications are the product" for the unattended path. Outbound only.
**Depends on:** 2.3b Mode Router (`route_gameweek` and its `plan`), 2.3c unattended scheduling (`auto_execute_job`), 2.1 auth (`SessionExpired` from `ensure_session`), `repository.log_activity`.
**Splits from:** 2.4b Interactive confirm (inbound `getUpdates` callbacks + confirm→execute loop) — a later slice. 2.4a builds the send client (with inline-keyboard support) so 2.4b reuses it.

## Goal

When the autopilot acts (or decides not to) on the unattended path, tell the user — what changed,
why, and the impact (B9). Three outbound triggers plus mandatory failure-to-send logging. The user
isn't watching during the pre-deadline window; this is the anti-drop-off nudge the product exists for.
Outbound only: no buttons that do anything yet (that's 2.4b). The agent never sends live (R3) — with
the channel unconfigured the notifier is a silent no-op, so dry-run and the existing suite stay
network-silent.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Token/chat storage | **Env vars** `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. **Not** encrypted-at-rest — decoupled from the master key so alerts work when the key isn't loaded. A leaked bot token spams your chat; it is not FPL account takeover (different blast radius from the B7 session). `config.yaml` is in git, so the token cannot live there. |
| Configured ≠ loaded | `notify` is a **silent no-op** when the channel is unconfigured (no send, **no** log row). Disabled ≠ failed. |
| Triggers in 2.4a | (1) post-execution confirmation, (2) pending-decision info (`route="notify"`), (3) auth-failure alert — plus failure-to-send logging (always). |
| Auth alert scope | Sent at the **existing** detection point: a live auto-run that hits `SessionExpired`. The B7 "fail twice → freeze" counter/state is **not** built here — it overlaps 2.7 Emergency Override and lands there. 2.4a uses only real signals. |
| Where `notify` is called | **Caller-driven (Approach A).** The router stays pure (no interface import); the Interface-layer caller (`auto_execute_job`) notifies. Respects B2 (lower layers never import Interface). |
| Wiring surface | **Unattended path only** (`auto_execute_job`). Not the interactive `route-gameweek` CLI (user sees screen output; avoids dry-run spam). CLI wiring is a deferred one-liner. |

## Architecture

```
src/interface/telegram.py   ← is_configured(), send_message(), notify(), notify_plan()  [NEW]
src/execution/router.py     ← plan entries gain `summary` + `executed` (additive; no telegram import)
src/scheduler.py            ← auto_execute_job: try/except SessionExpired→notify(alert); notify_plan after
```

B2 layering holds: only Interface code (`scheduler`, `telegram`) imports `telegram`. The router emits
the `route` signal it already emits; 2.4a is its first real consumer. **No `decision-engine.md`
change** — 2.4a touches no decision logic; `summary`/`executed` are presentation only.

## Module — `src/interface/telegram.py`

```python
BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV   = "TELEGRAM_CHAT_ID"
API_BASE      = "https://api.telegram.org"
TIMEOUT       = 10

def is_configured() -> bool:
    """Both env vars present and non-empty."""

def send_message(text, *, buttons=None, session=None) -> bool:
    """Pure transport. No-op (return False) if not is_configured().
    POST {API_BASE}/bot{token}/sendMessage with {chat_id, text}; add
    reply_markup={"inline_keyboard": buttons} when buttons given (2.4b hook;
    2.4a passes None). Return True only on HTTP 200 + JSON ok:true. Catch ALL
    network/HTTP errors -> return False. Never raises. Never logs the token,
    chat_id, or the URL (the URL embeds the token — B7)."""

def notify(conn, *, kind, decision_type, mode, summary, session=None) -> bool:
    """kind in {"executed","info","alert"}. If not is_configured(): return False
    (silent, no log). Else send _format(kind, summary); on failure log ONE
    activity row (B9/B10) and return False; on success return True. Never raises."""

def notify_plan(conn, plan, *, mode, session=None) -> None:
    """For each plan entry: kind = "executed" if entry["executed"] else "info";
    notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
           summary=entry["summary"], session=session)."""
```

`buttons` shape: `[[{"text": str, "callback_data": str}]]` — present for 2.4b reuse; unused in 2.4a.

### Message formatting — `_format(kind, summary)` (B9 copy)

`summary` (built by the caller / router enrichment) carries action + reason + impact. `_format` adds
a functional icon + header. Functional icons only (✅ ❌ 📊); no chatter (B9).

| kind | renders |
|------|---------|
| `executed` | `✅ Executed\n{summary}` |
| `info` | `📊 Decision pending\n{summary}\nReview before the deadline.` |
| `alert` | `❌ Autopilot blocked\n{summary}` |

## Router enrichment — `src/execution/router.py` (additive)

Each `plan` entry gains `summary` (human-readable action) + `executed` (bool). No telegram import;
existing tests assert `decision`/`route`/`confidence` and are unaffected by new keys.

```python
pick = caps["picks"][0]                      # already in scope in route_gameweek
# captain, execute branch:
plan.append({"decision": "captain", "route": r, "confidence": caps["confidence"],
             "summary": f"Captain: {pick['web_name']} (confidence {caps['confidence']})",
             "executed": True})
# captain, notify branch:  summary f"Captain pending: {pick['web_name']} (confidence {caps['confidence']})", executed=False
# transfer (top = sugg["suggestions"][0]):
#   execute: summary f"Transfer: OUT {top['out']['web_name']} IN {top['in']['web_name']} "
#                    f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})", executed=True
#   notify:  summary f"Transfer pending: OUT {top['out']['web_name']} IN {top['in']['web_name']} "
#                    f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})", executed=False
```

## Caller wiring — `auto_execute_job` (scheduler), unattended path only

```python
from .interface import telegram
...
try:
    plan = (route_fn or _default_route)(conn, key)
except SessionExpired:
    telegram.notify(conn, kind="alert", decision_type="auth", mode=config.mode(cfg),
                    summary="FPL session expired — re-run init-fpl. No changes were made.")
    raise                                          # preserve existing error propagation
if any(p["route"] == "execute" for p in plan):
    conn.execute("UPDATE gameweeks SET last_system_action_at=? WHERE id=?", (now.isoformat(), row["id"]))
    conn.commit()
telegram.notify_plan(conn, plan, mode=config.mode(cfg))    # best-effort, after the act
return plan
```

`SessionExpired` propagates from inside the executor (`ensure_session` in `lineup`/`transfer`) up to
this caller, so the same site covers post-exec notifications *and* the auth alert. The notify calls
are best-effort and never alter control flow (no-op when unconfigured; failures logged, not raised).

This covers both product needs: **Auto/Hybrid** → execution confirmations; **Manual under unattended
scheduling** → `route_gameweek` returns all-notify → "decision pending" pings (the anti-drop-off nudge).

## Error handling & B-rule compliance

- **Failure-to-send is a logged event, never an exception** (B9/B10): when configured and
  `send_message` returns False, `notify` writes one `activity_log` row via `repository.log_activity`
  with a **fixed** `decision_type="notification"` (so send-failures are filterable as a class),
  `executed=False`, and the triggering context in `inputs={"kind", "summary", "decision_type"}` (the
  original `decision_type` — captain/transfer/auth — stashed there). Unconfigured → no row.
- **B2 layering:** only Interface code imports `telegram`; router stays pure. ✅
- **B7 secrets:** token/chat from env, never persisted, never logged (not even the URL). ✅
- **B9:** post-exec mandatory; functional copy + functional icons; failure-to-send logged. ✅
- **R3 / dry-run:** the agent never sets env vars or runs the live daemon — the user does. Env unset →
  `notify` no-op → all 212 existing tests stay network-silent with zero changes. ✅
- **B4:** no decision logic touched → no `decision-engine.md` change.

## Config

No `config.yaml` change. Two new env vars (documented in README / handoff):
```
TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
TELEGRAM_CHAT_ID=<your chat id>
```

## Testing — fixtures only, never live

New `tests/test_telegram.py`:
- `is_configured` — env present / absent (monkeypatch).
- `send_message` — unset → False, no HTTP performed; configured + fake session → asserts correct URL
  & payload, True on `ok:true`, False on non-ok response and on a session that raises.
- `notify` — unset → False + **no** log row; configured + failing send → False + exactly one
  `notification` activity row whose stored text contains **no token**; configured + success → no row.
- `notify_plan` — `executed`→`executed` kind, pending→`info`; one `notify` per entry (fake notify/send).

Extended:
- `test_router` — assert plan entries carry `summary` + `executed` for execute and notify branches.
- `test_scheduler` — `auto_execute_job` with monkeypatched env + fake send: `notify_plan` fires for
  executed/pending; `SessionExpired` path → one `alert` send **then** re-raise.

**Verification:** full `pytest -q` stays green (expect 212 + new tests).

## Scope boundary

- **IN:** `telegram.py` (transport + `notify`/`notify_plan` + `_format`), router `summary`/`executed`
  enrichment, `auto_execute_job` wiring (post-exec + pending + auth alert), failure-to-send logging,
  env-var config.
- **OUT → 2.4b:** inline confirm/reject/modify buttons that *act*, inbound `getUpdates` callback
  handling, the confirm→execute one-tap loop. (2.4a builds the `buttons`-capable client they reuse.)
- **OUT → 2.7:** the B7 "fail twice → freeze auto-execution" counter/state machine. 2.4a only *alerts*
  at the existing single-failure `SessionExpired` point.
- **OUT (deferred one-liner):** notifying from the interactive `route-gameweek` CLI.

## Definition of done (CLAUDE.md B14)

- With `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` set, an unattended run that executes sends a `✅ Executed`
  message; a run that routes to notify sends a `📊 Decision pending` message; a live run that hits
  `SessionExpired` sends a `❌ Autopilot blocked` alert and re-raises. Channel unset → silent no-op.
- A send failure (configured) writes one `notification` row to `activity_log` and never raises.
- All tests fixtures-only; suite stays green; no token/chat ever logged; the agent never sends live.
- Manual smoke check (out of band, by the user): set the env vars, trigger an unattended run in the
  window, confirm the message arrives and `activity_log` records the action.
