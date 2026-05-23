# Deadguard Bench-Order + Transfer-if-Flagged — Design (Phase 2.5b)

**Status:** approved 2026-05-23
**Slice:** Phase 2.5b (deadguard, slice 2 of 3). Extends the 2.5a deadguard trigger from captain/vice-only
to also optimize bench order and make a single guarded transfer when a squad player is flagged out.
**Depends on:** 2.5a deadguard (`src/interface/deadguard.py`: `_run_trigger`, `run_deadguard_job`,
state machine), 2.2 executors (`executor.build_lineup_payload`, `lineup.run_lineup`, `transfer.run_transfer`),
the captain/transfer decision engines (`decisions/captain.py`, `decisions/transfers.py`), the `xp` table.
**Source of truth:** `docs/deadguard.md` (§"Scope of deadguard actions"). 2.5b implements the
"always-allowed" bench-order optimization (auto-sub via FPL's native engine) + the "allowed if
configured" single transfer-if-flagged.

## Goal

When deadguard fires at H-30 for an absent Manual/Hybrid user, in addition to setting captain/vice
(2.5a) it (a) reorders the outfield bench by xP so FPL's **native** auto-substitution picks the best
replacement for any 0-minute starter, and (b) if configured, makes a single **free** transfer to
replace a flagged-out squad player when an obvious upgrade exists. Conservative for an absent user
(B8): no formation solver, no hits, no chips, no multi-transfer.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Auto-sub interpretation | **Bench-order optimization only** — rank the 3 outfield bench (positions 13/14/15) by next-GW xP so FPL's native auto-sub does the swap. No explicit pre-deadline starter→bench formation solver. |
| Bench plumbing | Extend `run_lineup` with `optimize_bench=False` (default off → all existing callers unchanged); captain/vice + bench order go in ONE atomic FPL write (Approach A). |
| Transfer trigger | **Targeted at the flagged player, fully guarded:** OUT = a flagged squad player (`status not in ('a','d')`), free only (`hit_cost >= 0`), `ep_delta_5gw >= 3.0`, `confidence >= 75`, max one; skip if none qualifies. |
| transfer_if_underperform | **Deferred** (default-off in config; not in 2.5b's named scope). |
| Trigger order | Lineup (captain/vice + bench) first → mark triggered → transfer-if-flagged best-effort last (doc order). A transfer failure never undoes/re-submits the lineup. |

## Architecture

```
src/decisions/bench.py          ← NEW: rank_bench(conn, current_picks) -> ordered bench element ids
src/execution/executor.py       ← build_lineup_payload gains optional bench_order
src/execution/lineup.py         ← run_lineup gains optimize_bench=False (default off)
src/interface/deadguard.py      ← _run_trigger: optimize_bench=True + transfer-if-flagged; _pick_flagged_transfer
src/config.py                   ← deadguard_transfer_if_flagged / deadguard_min_ep_delta / deadguard_confidence_floor
docs/decision-engine.md         ← v0.10 changelog (bench-rank rule + deadguard transfer guards)
```
B2 holds: `run_lineup` (execution) already calls the decision layer (`captain`); calling `bench.rank_bench`
is consistent. `deadguard` (interface/jobs) orchestrates. No new interface→lower violations; nothing
lower imports interface. **Import note (disambiguate, per the router's convention):** in `deadguard.py`,
`from src.decisions import transfers` (the suggester) and `from src.execution import transfer as
transfer_exec` (the executor); `run_deadguard_job` now passes `cfg` into `_run_trigger`.

## §1 Bench ranking — `src/decisions/bench.py`

```python
def rank_bench(conn, current_picks):
    """Return the element ids currently at bench positions 13/14/15, ordered by next-GW xP
    (desc), with xMinutes as the rotation-risk tiebreaker. Missing xP -> 0 (sorts last).
    The sub-GK (position 12) is fixed and not reordered."""
```
Implementation: `bench = [p for p in current_picks if p["position"] in (13, 14, 15)]`; find the next GW
(`transfers._next_gw(conn)` — already used by the transfer executor); query the `xp` table for those
element ids at that GW (model version = the configured `xp_model.version`, mirroring how `captain`
selects xp); sort by `(xp, xminutes)` desc; return the ordered element ids. Pure-ish (reads DB only),
unit-testable with a seeded `xp` table + a fixture `current_picks`.

## §2 Executor payload + run_lineup — `src/execution/{executor,lineup}.py`

`build_lineup_payload(current_picks, captain_id, vice_id, bench_order=None)` — additive optional arg:
```python
def build_lineup_payload(current_picks, captain_id, vice_id, bench_order=None):
    # ... existing captain/vice validation ...
    pos_override = {}
    if bench_order is not None:
        current_bench = {p["element"] for p in current_picks if p["position"] in (13, 14, 15)}
        if set(bench_order) != current_bench:
            raise ExecutorError("bench_order must be exactly the current bench (positions 13-15)")
        pos_override = {element: 13 + i for i, element in enumerate(bench_order)}
    picks = [
        {"element": p["element"], "position": pos_override.get(p["element"], p["position"]),
         "is_captain": p["element"] == captain_id, "is_vice_captain": p["element"] == vice_id}
        for p in current_picks
    ]
    return {"chip": None, "picks": picks}
```
When `bench_order is None`, behavior is identical to today (no override). Positions 1–12 and the
captain/vice flags are untouched; only 13/14/15 are reassigned.

`lineup.run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None, optimize_bench=False)`:
after fetching `current` picks and computing `captain_id`/`vice_id`, when `optimize_bench` is True compute
`bench_order = bench.rank_bench(conn, current)` and pass it to `build_lineup_payload`; include a short
"bench: A>B>C" note in the `diff` string. Default `optimize_bench=False` → existing callers (2.4b confirm,
CLI execute-lineup, auto_execute, 2.5a captain) unchanged.

## §3 Transfer-if-flagged — `src/interface/deadguard.py`

```python
def _pick_flagged_transfer(conn, cfg):
    """Return the 1-based rank of the first transfer suggestion that replaces a FLAGGED squad player
    with a free, high-EP upgrade, or None. Guards (all required): OUT player status not in ('a','d');
    hit_cost >= 0 (free); ep_delta_5gw >= deadguard_min_ep_delta (3.0); confidence >= deadguard_confidence_floor (75)."""
    if not config.deadguard_transfer_if_flagged(cfg):
        return None
    min_ep = config.deadguard_min_ep_delta(cfg)
    floor = config.deadguard_confidence_floor(cfg)
    sugg = transfers.get_transfer_suggestions(conn)
    for i, s in enumerate(sugg["suggestions"], start=1):
        out_status = _player_status(conn, s["out"]["player_id"])
        if (out_status not in ("a", "d") and s["hit_cost"] >= 0
                and s["ep_delta_5gw"] >= min_ep and s["confidence"] >= floor):
            return i
    return None
```
`_player_status(conn, player_id)` queries `players.status` (robust — does not assume the suggestion dict
carries status). Targeted: the OUT must be the flagged player.

## §4 `_run_trigger` new flow — `src/interface/deadguard.py`

Replaces 2.5a's captain-only trigger body (doc order: lineup → mark → transfer):
```python
def _run_trigger(conn, key, gw, cfg):
    repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")
    caps = captain.get_captain_picks(conn)
    if not caps["picks"]:
        repository.set_gameweek_state(conn, gw, "DEADGUARD_SKIPPED")
        repository.mark_deadguard_triggered(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="skipped: no captain pick available", executed=False)
        _notify(conn, "info", "Deadguard ran — no safe action (no data). Team unchanged.")
        return
    # 1. lineup: captain/vice + bench order, one atomic write
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True, optimize_bench=True)
    except SessionExpired:
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        return
    except Exception as e:
        _notify(conn, "alert", f"Deadguard failed: {type(e).__name__}")
        return
    if not getattr(result, "ok", False):
        _notify(conn, "alert", "Deadguard: lineup submission did not complete — will retry.")
        return                                          # not marked -> retryable
    # 2. lineup done -> lock once-per-GW
    name = caps["picks"][0]["web_name"]
    try:
        repository.mark_deadguard_triggered(conn, gw)
        repository.set_gameweek_state(conn, gw, "DEADGUARD_EXECUTED")
    except Exception:
        log.exception("deadguard post-execution bookkeeping failed (lineup was already set)")
    # 3. transfer-if-flagged (best-effort; never undoes the lineup, never retried)
    transfer_note = "no transfer"
    rank = _pick_flagged_transfer(conn, cfg)
    if rank is not None:
        try:
            tr = transfer_exec.run_transfer(conn, key, rank=rank, live=True, confirm_fn=lambda d: True)
            transfer_note = "transfer applied" if getattr(tr, "ok", False) else "transfer failed"
            if not getattr(tr, "ok", False):
                _notify(conn, "alert", "Deadguard: flagged-player transfer did not complete.")
        except Exception as e:
            transfer_note = f"transfer failed ({type(e).__name__})"
            _notify(conn, "alert", f"Deadguard transfer failed: {type(e).__name__}")
    repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                            action_taken=f"captain {name}; bench optimized; {transfer_note}",
                            inputs={"pick": caps["picks"][0]}, executed=True)
    _notify(conn, "executed", f"Deadguard: captain {name}, bench optimized, {transfer_note}.")
```
`run_deadguard_job` passes `cfg` into `_run_trigger` (it already loads `cfg`). The SessionExpired/exception/
not-ok lineup paths stay retryable exactly as 2.5a; once the lineup succeeds the GW is locked, so a
flaky transfer never causes a captain/bench re-submit (B8: no repeated autonomous transfers).

## §5 Config — `src/config.py`

```python
def _scope(cfg):
    return (cfg if cfg is not None else load_config()).get("deadguard", {}).get("scope", {})

def deadguard_transfer_if_flagged(cfg=None):
    return bool(_scope(cfg).get("transfer_if_flagged", True))

def deadguard_min_ep_delta(cfg=None):
    return _scope(cfg).get("min_ep_delta_for_transfer", 3.0)

def deadguard_confidence_floor(cfg=None):
    return _scope(cfg).get("confidence_floor", 75)
```
Uses the `cfg is not None` pattern (the 2.5a correctness fix — an explicit `{}` must not fall through to
`config.yaml`, which has these enabled). `config.yaml` already ships the `deadguard.scope` block — no
config change. Bench-order has no gate (always-on per the doc).

## Safety & B-rule compliance
- **B8:** ≤1 transfer, **free only** (`hit_cost >= 0`), targeted at the flagged player, via the existing
  bounded `run_transfer`; bench-order only reassigns the 3 outfield bench slots (never the XI/formation,
  never a chip). No multi/hit/wildcard path exists.
- **B4:** new bench-rank rule + the deadguard transfer guards (3.0 / free / conf 75) recorded in
  `docs/decision-engine.md` (v0.10). Captain + transfer engines reused unchanged (no threshold edits to them).
- **B7:** token/chat/URL never logged; deadguard reaches the network only via `telegram.*` + the executors.
- **B9:** every outcome notifies (executed summary incl. bench/transfer; skip; failure alerts).
- **B10:** state in `gameweeks`; the deadguard action logs to `activity_log` (`mode="deadguard"`).
- **R3 / dry-run:** the agent never runs the live daemon; all tests inject fakes (fake current_picks,
  fake `run_lineup`/`run_transfer`/`get_transfer_suggestions`/notify, seeded `xp`, in-memory `db`).

## Testing — fixtures only, never live
- `bench.rank_bench` — bench trio at 13/14/15 + seeded `xp` → ordered by xP desc (xMinutes tiebreaker);
  a bench player with no xp row → sorts last; only positions 13/14/15 considered (12 ignored).
- `executor.build_lineup_payload(bench_order=…)` — reassigns 13/14/15 in order, preserves positions
  1–12 and captain/vice flags; `bench_order` not equal to the current bench set → `ExecutorError`;
  `bench_order=None` → identical to today.
- `lineup.run_lineup(optimize_bench=True)` — fake session + ranker + seeded xp → posted payload has the
  reordered bench; `optimize_bench=False` (default) → payload unchanged (existing run_lineup tests stay green).
- `_pick_flagged_transfer` — a suggestion whose OUT is flagged + free + ep≥3.0 + conf≥75 → its rank;
  each single guard violated (OUT status 'a' or 'd' / `hit_cost<0` / ep<3.0 / conf<75) → None;
  `transfer_if_flagged` off → None; no suggestions → None.
- `_run_trigger` (2.5b) — `run_lineup` called with `optimize_bench=True`; a qualifying flagged transfer
  → `run_transfer(rank=…)` called, state DEADGUARD_EXECUTED, notify mentions the transfer; no qualifying
  transfer → "no transfer", still EXECUTED; transfer raises/`not ok` → still EXECUTED (lineup locked) +
  alert; lineup `not ok` → not marked (retryable), no transfer attempted. Existing 2.5a trigger tests
  still pass (they assert `k.get("live")`, unaffected by the new `optimize_bench` kwarg).
- config accessors — scope values + defaults via the `cfg is not None` pattern.
- Full `pytest -q` green.

## Scope boundary
- **IN:** bench-order optimization (rank + payload + `run_lineup` flag + deadguard wiring), targeted
  free transfer-if-flagged (guarded), config accessors, `decision-engine.md` v0.10.
- **OUT → later:** `transfer_if_underperform` (sell a healthy underperformer — default-off, riskier);
  explicit pre-deadline formation-valid starter→bench swaps (relying on FPL native auto-sub instead);
  hits (forbidden, B8).
- **OUT → 2.5c:** late-news re-evaluation, undo, dashboard banner, multi-device.

## Definition of done (CLAUDE.md B14)
- With the daemon running and `deadguard.enabled: true`: at H-30 for an untouched GW, deadguard sets
  captain/vice **and** an xP-ordered bench in one write, and — if `transfer_if_flagged` and a flagged
  squad player has a free ≥3.0-EP, ≥75-confidence upgrade — makes that single transfer; the user gets
  one summary notification (captain, bench, transfer/none). No hit, chip, or multi ever. A lineup
  failure is retryable; a transfer failure leaves the lineup intact and is alerted, not retried.
- All tests fixtures-only; suite stays green; no token/chat logged; `decision-engine.md` v0.10 added;
  the agent never ran the live daemon.
- Manual smoke check (out of band, by the user): with the daemon up, force a GW into the trigger window
  with a flagged squad player and confirm the bench order + transfer + summary notification.
