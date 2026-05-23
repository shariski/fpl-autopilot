# Deadguard Late-News Re-Evaluation — Design (Phase 2.5c-1)

**Status:** approved 2026-05-23
**Slice:** Phase 2.5c-1 (first of the 2.5c bundle; the others — Undo, dashboard banner — are separate slices).
After deadguard executes (H-30), re-check the lineup decision against fresh data until the deadline; on a
material change, auto-apply (>15 min out) or alert-only (≤15 min lockout). Lineup-only — never a transfer.
**Depends on:** 2.5a deadguard (`evaluate`, `run_deadguard_job`, `_notify`, `gameweeks.state` machine,
`RESOLVED`), 2.5b (`decisions/bench.rank_bench`, `run_lineup(optimize_bench=True)`), 2.2 executor
(`fetch_current_picks`, `MY_TEAM_URL`), the captain ranker, the data layer (`refresh`, `xp.compute_and_store`),
2.7 emergency override (the freeze checkpoint at the top of `run_deadguard_job`).
**Source of truth:** `docs/deadguard.md` §"Late team news arrives between deadguard execution and deadline"
(>15 min → re-evaluate/re-execute; ≤15 min → freeze + log missed update) and §"User opens the dashboard after
deadguard executed" (the dashboard/undo half is 2.5c-3/2.5c-2, NOT here).

## Goal

Close the gap where a squad player is flagged out (status flips to injured/doubtful/suspended) in the final
window before the deadline, *after* deadguard already set the lineup for an absent user. A periodic re-eval
refreshes FPL availability data, recomputes the lineup decision, and — if the best captain/vice/bench now
differs — re-applies it (when there's safe time) or alerts the user (when it's too late to act safely).
Lineup-only (free, reversible); B8 holds (no second transfer/hit/chip).

## Decisions (locked — brainstorming 2026-05-23)

| Decision | Choice |
|----------|--------|
| Re-eval scope | **Lineup only** — captain/vice + bench order. Never a transfer (B8: deadguard's "one transfer per activation" stays intact; the most common late news = a benched/ruled-out captain, fixed via lineup). |
| Apply behavior, >15 min out | **Auto-apply + notify.** The change is free + reversible and the user is by-definition absent (deadguard fired). No stateful 10-min one-tap timer (deadguard.md's alternative) — simpler and more timely. |
| Behavior, ≤15 min (lockout) | **Detect but don't apply.** Recompute + compare; on a material change, log a "missed update" row and send ONE alert ("captain X looks ruled out — too close to deadline to change safely; you may want to act"). Hands the panic decision back to the user. (`reeval_lockout_minutes`, default 15.) |
| Trigger mechanism | Extend the pure `evaluate()` with a `reeval`/`lockout` directive for `state == DEADGUARD_EXECUTED` in-window; `run_deadguard_job` dispatches to `_run_reevaluate`. Rides the existing 5-min `deadguard_job` cadence. |
| Data freshness | Re-eval **force-refreshes** FPL `bootstrap-static` (`full=True`, bypassing the cache) + recomputes xP before recomputing the lineup — a cache-aware refresh would return stale data and miss the news. No Understat re-scrape (B6). |
| Idempotency | Act only when the recomputed captain/vice/bench **differs** from what's currently set; once applied, the next tick sees no diff → noop. Lockout alert is once-only via a `deadguard_reeval_alerted_at` marker. |
| Freeze (2.7) | A frozen system makes the whole `run_deadguard_job` dormant (2.7 checkpoint at the top), so re-eval doesn't run either — no new checkpoint needed. |
| Mode scope | `DEADGUARD_EXECUTED` only. Auto-mode `SYSTEM_ACTED` late-news re-eval is **deferred** (2.5c is deadguard polish). |

## Architecture

```
src/interface/deadguard.py   ← evaluate() gains reeval_enabled/lockout_min → "reeval"/"lockout";
                                run_deadguard_job dispatches them; NEW _run_reevaluate(conn, key, gw, cfg, *, apply)
src/config.py                ← deadguard_reeval_enabled / deadguard_reeval_lockout_minutes accessors
config.yaml                  ← deadguard.reeval_if_late_news: true, deadguard.reeval_lockout_minutes: 15
src/data/schema.sql          ← gameweeks.deadguard_reeval_alerted_at TIMESTAMP
src/data/db.py               ← _migrate_gameweeks adds deadguard_reeval_alerted_at (idempotent)
src/data/repository.py       ← mark_deadguard_reeval_alerted(conn, gw)
docs/decision-engine.md      ← v0.11 changelog (reeval directive + lockout + material-change rule)
```
B2 holds: re-eval orchestration lives in `deadguard` (interface/jobs); it reuses the data layer (`refresh`,
`xp.compute_and_store`), the decision layer (captain ranker, `bench.rank_bench`), and the executor
(`run_lineup`, `fetch_current_picks`) — the same dependencies `_run_trigger` already uses. No new
lower→interface inversion.

## §1 State machine — `evaluate()` (pure)

`evaluate` currently returns `"noop"` for any `state in RESOLVED` (which includes `DEADGUARD_EXECUTED`).
Add re-eval handling **before** the RESOLVED check, gated by a new `reeval_enabled` kwarg (default `False`
→ all existing callers/tests keep `DEADGUARD_EXECUTED → noop`, backward-compatible):

```python
def evaluate(now, *, deadline, state, last_system_action_at, user_acted,
             warned, triggered, warn_min, trigger_min,
             reeval_enabled=False, lockout_min=15):
    """Return: 'system_acted'|'user_acted'|'warn'|'trigger'|'reeval'|'lockout'|'noop'.
    Pure: no I/O, deterministic for frozen inputs (B11)."""
    if state == "DEADGUARD_EXECUTED":
        if not reeval_enabled:
            return "noop"
        mins = (deadline - now).total_seconds() / 60
        if mins <= 0:
            return "noop"
        return "lockout" if mins <= lockout_min else "reeval"
    if state in RESOLVED:
        return "noop"
    # ... existing system_acted / user_acted / trigger / warn / noop logic unchanged ...
```
Note: if the user manually acts after deadguard executed, the state moves to `USER_ACTED` (via
`touch_user_action`), so `evaluate` returns `noop` and re-eval stops — re-eval never fights a present user.
A `decision-engine.md` v0.11 entry records the new directive + lockout threshold (B4).

## §2 Job dispatch — `run_deadguard_job`

`run_deadguard_job` already loads `cfg`, runs the 2.7 freeze checkpoint, reads the gameweek row, and
dispatches the `evaluate` directive. Pass the two new args into `evaluate` and dispatch the two new directives:

```python
        directive = evaluate(
            now, deadline=datetime.fromisoformat(row["deadline_utc"]), state=row["state"],
            last_system_action_at=row["last_system_action_at"], user_acted=user_acted(conn, gw),
            warned=bool(row["deadguard_warned_at"]), triggered=bool(row["deadguard_triggered_at"]),
            warn_min=config.deadguard_warning_minutes(cfg),
            trigger_min=config.deadguard_trigger_minutes(cfg),
            reeval_enabled=config.deadguard_reeval_enabled(cfg),
            lockout_min=config.deadguard_reeval_lockout_minutes(cfg))
        ...
        elif directive == "trigger":
            _run_trigger(conn, key, gw, cfg)
        elif directive == "reeval":
            _run_reevaluate(conn, key, gw, cfg, apply=True)
        elif directive == "lockout":
            _run_reevaluate(conn, key, gw, cfg, apply=False)
        return directive
```

## §3 `_run_reevaluate` — refresh → recompute → compare → apply / alert

```python
def _run_reevaluate(conn, key, gw, cfg, *, apply):
    # 1. fresh availability data so the ranker sees late news. full=True bypasses the cache (a
    #    cache-aware refresh would return stale bootstrap-static and miss the news). FPL only — no
    #    Understat re-scrape (B6). Lazy import mirrors scheduler.refresh_and_recompute (cli<->scheduler cycle).
    try:
        from src.cli import refresh
        refresh(cfg=cfg, conn=conn, sources=("fpl",), full=True)
        xp.compute_and_store(conn)
    except Exception:
        log.exception("deadguard re-eval refresh failed")
        return                                          # stale data -> skip this tick, retry next

    # 2. current vs desired lineup
    try:
        session = ensure_session(conn, key)
        current = executor.fetch_current_picks(session, config.team_id(cfg))
        caps = captain.get_captain_picks(conn)
        if not caps["picks"]:
            return
        desired = (caps["picks"][0]["player_id"], caps["vice_player_id"], bench.rank_bench(conn, current))
        cur = _current_lineup(current)                  # (captain_id, vice_id, [bench element ids 13,14,15])
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)        # B7 path, same as _run_trigger
        _notify(conn, "alert", "Deadguard re-eval: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. Re-run init-fpl, then unfreeze.")
        return
    except Exception:
        log.exception("deadguard re-eval compare failed")
        return

    if desired == cur:
        return                                          # no material change -> nothing to do (idempotent)

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
            _notify(conn, "executed", f"Late news: re-set captain {name} + bench. You can change it back before the deadline.")
    else:
        # <=15 min lockout: do NOT change; alert once
        row = conn.execute("SELECT deadguard_reeval_alerted_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
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
`_current_lineup(picks)` reads the FPL picks (`is_captain`/`is_vice`/`position`): captain = the `is_captain`
pick's element, vice = the `is_vice` pick's element, bench = the elements at positions 13/14/15 in order.
The desired bench comes from `bench.rank_bench` (the 2.5b ranker). The actual write is delegated to
`run_lineup` (single source of truth for the lineup POST); `_run_reevaluate` only decides whether to call it.
`refresh` already takes a `sources` kwarg (`cli.refresh(..., sources=...)`); pass `("fpl",)`.

## §4 Config + schema

`config.py`:
```python
def deadguard_reeval_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("deadguard", {}).get("reeval_if_late_news", True))

def deadguard_reeval_lockout_minutes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("reeval_lockout_minutes", 15)
```
`config.yaml` `deadguard:` block gains `reeval_if_late_news: true` and `reeval_lockout_minutes: 15`.
`schema.sql` `gameweeks` gains `deadguard_reeval_alerted_at TIMESTAMP`; `db._migrate_gameweeks` adds it
idempotently (mirrors the existing `deadguard_warned_at` migration). `repository.mark_deadguard_reeval_alerted`
mirrors `mark_deadguard_warned`.

## Safety & B-rule compliance
- **B8:** lineup-only — captain/vice + bench reorder via the existing bounded `run_lineup`. No transfer, hit,
  chip, or multi path is reachable from re-eval.
- **B4:** new `reeval`/`lockout` directive + the lockout threshold + the material-change rule recorded in
  `docs/decision-engine.md` v0.11. The captain ranker + bench ranker are reused unchanged (no threshold edits).
- **B6:** re-eval force-refreshes only FPL `bootstrap-static` (cache-bypassed, ≤ ~3 fetches across the
  ~15-min window) + recomputes xP from existing DB data; no Understat re-scrape; well within ≤1 req/sec.
  The cache-bypass is scoped to this short pre-deadline window only — normal ops keep caching aggressively.
- **B7:** a `SessionExpired` during re-eval feeds the same `override.maybe_auto_freeze` path as `_run_trigger`;
  no token/cookie logged.
- **B9:** both outcomes notify (auto-apply summary; lockout alert). A no-change tick is silent (correct — no event).
- **B10:** every applied re-eval and every lockout missed-update logs to `activity_log` (`mode="deadguard"`).
- **R3 / dry-run:** the agent never runs the live daemon; all tests are fixtures-only (fake
  `refresh`/`ensure_session`/`fetch_current_picks`/ranker/`run_lineup`/notify, in-memory DB, frozen clock).

## Testing — fixtures only
- `evaluate` (pure): `DEADGUARD_EXECUTED` + `reeval_enabled=True` → `"reeval"` when `mins > lockout_min`,
  `"lockout"` when `0 < mins <= lockout_min`, `"noop"` when `mins <= 0`; `reeval_enabled=False` → `"noop"`
  (backward-compat — existing `test_evaluate_resolved_state_noop` still passes); other states unchanged.
- `_run_reevaluate(apply=True)`: fresh data makes the ranker pick a different captain → `run_lineup` called
  (live) + an "executed" notify + an activity row; recompute equals current → `run_lineup` NOT called, no
  notify (idempotent no-op).
- `_run_reevaluate(apply=False)` (lockout): material change → no `run_lineup`, ONE "alert" notify + a
  "missed update" activity row + `deadguard_reeval_alerted_at` set; a second tick → no second alert (guard);
  no change → silent.
- `run_deadguard_job`: dispatches `reeval`/`lockout` to `_run_reevaluate` with the right `apply`; a frozen
  system (2.7) returns `None` and never reaches re-eval; `reeval_if_late_news: false` → `evaluate` returns `noop`.
- `SessionExpired` during re-eval → alert + (at threshold) `maybe_auto_freeze`, no crash.
- config accessors + the `_migrate_gameweeks` column add. Full `pytest -q` green.

## Scope boundary
- **IN:** `evaluate` reeval/lockout directive, `_run_reevaluate` (auto-apply + lockout-alert), the
  `deadguard_reeval_alerted_at` column + migration + repo helper, config accessors, decision-engine.md v0.11.
- **OUT → 2.5c-2:** Undo (revert a deadguard transfer before the deadline).
- **OUT → 2.5c-3:** dashboard deadguard/freeze banner + keep/undo controls + multi-device (the dashboard half
  of deadguard.md's "user opens the dashboard" sections).
- **OUT (deferred):** late-news re-eval for Auto-mode `SYSTEM_ACTED`; the 10-min one-tap "apply update"
  override timer (we auto-apply instead); a second transfer during re-eval (B8).

## Definition of done (CLAUDE.md B14)
- With the daemon running and `deadguard.enabled` + `reeval_if_late_news: true`: after a deadguard execution,
  if a squad player's availability flips before the deadline, re-eval re-sets captain/vice + bench when there's
  >15 min left (one notification, reversible) and, in the final 15 min, sends a single "you may want to act"
  alert without changing anything. No transfer/hit/chip ever. A frozen system does no re-eval.
- All tests fixtures-only; suite green; no token logged; `decision-engine.md` v0.11 added; the agent never ran live.
- Manual smoke check (out of band, by the user): force a GW to `DEADGUARD_EXECUTED` with >15 min to a
  synthetic deadline, flip a starter's status in the DB, and confirm the re-eval re-sets the lineup + notifies;
  repeat inside 15 min and confirm alert-only.
