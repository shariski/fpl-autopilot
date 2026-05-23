# Mode Router — Design (Phase 2.3b)

**Status:** approved 2026-05-23
**Slice:** Phase 2.3b (Decision Automation — routing). Sits between the Decision Layer and the Action Executor.
**Depends on:** 2.3a confidence (`get_captain_picks`/`get_transfer_suggestions` return `confidence`), 2.2 executors (`run_lineup`, `run_transfer`), `repository.log_activity`, `config.yaml` (`mode.current`, `thresholds.confidence_floor`).

## Goal

Per the current mode + each decision's confidence, decide whether to **execute** a decision (via
the 2.2 executors) or **notify-and-wait** (record a pending decision). Dry-run-first; `--live`
acts. The agent never runs `--live`. This is the policy engine the unattended scheduler (2.3c) and
Telegram (2.4) build on.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Acts (not plan-only) | Execute-routes delegate to `run_lineup`/`run_transfer`. Dry-run default (prints plan, submits nothing); `--live` needs one upfront typed confirm. |
| Confidence gate | **Universal:** `confidence < floor (70)` → notify, in Auto AND Hybrid (documented in `decision-engine.md`). |
| Routed decisions | Captain/vice (via `run_lineup`) + the top transfer (via `run_transfer`). Bench-reorder and chips are out (no optimizer; chips never auto, B8). |
| Notify | One `activity_log` "pending" row per notify-route; Telegram (2.4) turns these into push notifications. |

## Pure routing policy — `src/execution/router.py`

```python
def route(mode, decision_type, *, confidence, ep_delta=None, is_hit=False, floor=70):
    if mode == "manual":
        return "notify"
    if mode == "auto":
        eligible = True
    elif mode == "hybrid":
        if decision_type == "captain":
            eligible = True
        elif decision_type == "transfer":
            eligible = (not is_hit) and ((ep_delta or 0) >= HYBRID_TRANSFER_EP_FLOOR)  # 4.0
        else:
            eligible = False
    else:
        eligible = False
    if not eligible:
        return "notify"
    if confidence is None or confidence < floor:
        return "notify"
    return "execute"
```
`HYBRID_TRANSFER_EP_FLOOR = 4.0` (per `decision-engine.md`'s Hybrid table). Pure, table-testable.
Mirrors the doc: Manual → always notify; Auto → eligible then confidence-gated; Hybrid → captain
auto, transfer auto only if free & EP≥4, then confidence-gated; chips/unknown → notify.

## Orchestration — `route_gameweek(conn, key, *, live=False, mode=None, session=None, ranker=None, suggester=None)`

```
mode = mode or config.mode()                  # config.yaml mode.current
floor = config.confidence_floor()             # thresholds.confidence_floor (70)
plan = []
caps = (ranker or captain.get_captain_picks)(conn)
if caps["picks"]:
    r = route(mode, "captain", confidence=caps["confidence"], floor=floor)
    plan.append({"decision": "captain", "route": r, "confidence": caps["confidence"]})
    if r == "execute":
        lineup.run_lineup(conn, key, live=live, confirm_fn=_AUTO_APPROVE, session=session, ranker=ranker)
    else:
        repository.log_activity(conn, decision_type="captain", mode=mode,
                                action_taken=f"pending: captain {caps['picks'][0]['web_name']}",
                                inputs={"confidence": caps["confidence"], "pick": caps["picks"][0]},
                                executed=False)
sugg = (suggester or transfers.get_transfer_suggestions)(conn)
if sugg["suggestions"]:
    top = sugg["suggestions"][0]
    r = route(mode, "transfer", confidence=top["confidence"],
              ep_delta=top["ep_delta_5gw"], is_hit=top["hit_cost"] < 0, floor=floor)
    plan.append({"decision": "transfer", "route": r, "confidence": top["confidence"]})
    if r == "execute":
        transfer.run_transfer(conn, key, rank=1, live=live, confirm_fn=_AUTO_APPROVE,
                              session=session, suggester=suggester)
    else:
        repository.log_activity(conn, decision_type="transfer", mode=mode,
                                action_taken=f"pending: OUT {top['out']['web_name']} IN {top['in']['web_name']}",
                                inputs={"confidence": top["confidence"], "suggestion": top}, executed=False)
return plan
```
- `_AUTO_APPROVE = lambda diff: True` — the routing already decided to execute, so the executor's
  per-decision confirm is auto-approved; the router-level `--live` + upfront confirm is the gate.
- In dry-run, the executor logs a dry-run row + the would-be request (no POST); in `--live` it POSTs.
- `session`/`ranker`/`suggester` injectable for tests. The router reads decisions for routing and
  passes the same `ranker`/`suggester` to the executors (single source; executors re-read the same
  deterministic DB state).

## CLI — `route-gameweek [--live] [--mode auto|manual|hybrid]`

`_route_gameweek_cli(...)` mirrors the other execute CLIs: master gate → `get_master_key` →
`route_gameweek(...)`. Dry-run default: print the plan (per decision: route, confidence, reason)
and the executor dry-run requests. `--live`: one upfront typed `yes` ("execute the auto-routed
decisions live?") then run with `live=True`. `--mode` overrides `config.mode.current` for the run.
Register `route-gameweek` with `--live` (store_true) + `--mode` (choices); dispatch
`_route_gameweek_cli(live=args.live, mode=args.mode)`.

## Config accessors (`src/config.py`)
```python
def mode(cfg=None):              return (cfg or load_config()).get("mode", {}).get("current", "manual")
def confidence_floor(cfg=None):  return (cfg or load_config()).get("thresholds", {}).get("confidence_floor", 70)
```

## Doc update (B4)
`decision-engine.md` — under mode routing, note that the **confidence floor is a universal gate**:
`confidence < floor` falls back to notify in Hybrid as well as Auto (not only Auto, despite the
Hybrid table not restating it). Add a changelog entry.

## Error handling
- No captain pick / no suggestions → that decision is skipped (not in the plan); the other still routes.
- `SessionExpired`/`SessionNotInitialized`/`ExecutorError` from an execute-route propagate to the
  CLI, which prints a clean message (nothing further submitted). No retry.
- The agent never runs `--live`; the user does.

## Testing — fixtures only, never live
- `route` — full table: manual→notify (all); auto high-conf→execute, low-conf→notify; hybrid
  captain→execute (conf-gated to notify when low), transfer EP≥4→execute, transfer EP<4→notify,
  hit→notify; chip/unknown→notify; `confidence None`→notify.
- `route_gameweek` (inject fake `session`, `ranker`, `suggester`): auto + high confidence → both
  executors invoked (dry-run logs `executed=0`; live → `posted`); manual → both logged pending,
  executors NOT called (fake session `.post`/`.get` untouched for execution); hybrid → captain
  executes, weak (EP<4) transfer pending; low-confidence captain in hybrid → pending (universal gate).
- CLI `route-gameweek` — dry-run prints a plan; `--live` (injected confirm) runs; no master password → message, nothing done.

## Scope boundary
- **IN:** `route` policy, `route_gameweek` orchestration, `route-gameweek` CLI, pending logging,
  `config.mode`/`config.confidence_floor`, the `decision-engine.md` universal-gate note.
- **OUT → 2.3c:** unattended scheduling — the scheduler running `route_gameweek` with the master
  key held in the long-running process, pre-deadline.
- **OUT → 2.4:** turning pending `activity_log` rows into Telegram one-tap notifications.
- **OUT (future):** bench-order optimizer; chip routing (chips never auto-execute regardless).

## Definition of done (CLAUDE.md B14)
- `route` matches the documented tables (incl. universal confidence gate); `route_gameweek` routes
  captain + top transfer per mode, executing execute-routes via the 2.2 executors and logging
  notify-routes as pending.
- `route-gameweek` dry-run prints the plan; `--live` (user-run) executes the auto-routes after one
  typed confirm; the agent does not run `--live`.
- `decision-engine.md` records the universal gate; all tests fixtures-only; suite stays green.
