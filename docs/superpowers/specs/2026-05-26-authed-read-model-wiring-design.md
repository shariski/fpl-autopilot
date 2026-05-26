# Authed read-model wiring — design

**Date:** 2026-05-26
**Slice:** Phase 2 leftover (live e2e backlog #1 + #4)
**Status:** approved

## Problem

Two findings from the 2026-05-24 live e2e against the real account (HANDOFF.md):

1. **Dashboard read-model is public + one-GW-behind.** `interface.queries.get_squad` reads
   the latest `my_team` row, which `cli.refresh` populates from the **public**
   `/entry/{id}/event/{gw}/picks/` endpoint (last finished GW). After making a transfer
   for the upcoming GW, the dashboard still shows the old squad and re-suggests the
   transfer that was already made.
2. **`free_transfers` is unknown to the executor.** `repository.snapshot_my_team` writes
   `None` to the `my_team.free_transfers` column (`# free_transfers: auth-only`) because
   the public picks endpoint doesn't expose it. As a result `run_transfer` cannot detect
   that a live transfer would cost a `-4` hit — it just submits.

Both root-cause back to the same gap: the system never calls the authed
`/api/my-team/{entry_id}/` endpoint as part of the refresh cycle, even though
`executor.fetch_current_picks` already uses it for lineup writes.

## Goal

After this slice:

- The dashboard shows the **current upcoming-GW squad** (including any transfer just
  made), not the last finished GW.
- The transfer executor knows the real `free_transfers` count and refuses to take a
  silent `-4` hit.
- Deadguard's `_pick_flagged_transfer` becomes strictly safer (refuses when
  `free_transfers=0`, per B8).
- 2.5c-3 invariant preserved: **the web layer holds no master key and makes no FPL
  call.** All authed fetches happen in the scheduler or in user-invoked CLI commands.

## Non-goals

- **Hit-aware transfer suggester.** `suggest_transfers` will keep computing
  `hit_cost: 0` for all suggestions in this slice. Real hit-aware ranking is a
  decision-engine change that requires a B4 / `decision-engine.md` update first.
- **`recompute` CLI command.** Separate slice (live e2e finding #2).
- **SPA static mount in `serve`.** Separate slice (live e2e finding #3).
- **Schema migration.** The `my_team` table already has all needed columns
  (`free_transfers`, `bank`, `team_value`). No DB migration.

## Approach (chosen)

**Authed snapshot via the existing scheduler refresh + an opt-in CLI command.**

The scheduler's `refresh_and_recompute` already loads the master key in unattended
mode (`_maybe_load_key`) and holds a healthy session via `auth_session.ensure_session`.
After it does the public refresh, it makes one extra call to authed `/my-team` and
writes a richer row into `my_team`. A new CLI `refresh-my-team` does the same thing on
demand for non-unattended use.

Both the dashboard read-model (`queries.get_squad`) and the decision layer
(`transfers._latest_squad`, `captain._get_squad_ids`, `chips` reader) already select
`my_team` with `ORDER BY gw DESC LIMIT 1`. Writing the authed snapshot under
`gw=next_gw` (vs. public's `gw=last_finished`) means **every existing reader picks up
the authed row automatically**, with the public row as a graceful fallback if the
authed call fails.

### Rejected alternatives

- **Lazy fetch from the web layer.** Would mean the FastAPI app holds the master key
  in process memory and hits `/my-team` on dashboard requests. Violates the explicit
  2.5c-3 invariant ("DB-state only, no master key, no FPL call from the web layer").
- **Replace public picks entirely.** Removes the Phase-1 read-only path — a user
  without an FPL session would no longer see anything in the dashboard. Loses the
  fallback when authed fails.

## Architecture

```
scheduler.refresh_and_recompute(key)          cli refresh-my-team (one-shot)
        |                                              |
        +------> auth_session.ensure_session(conn, key) <------+
                              |
                              v
              executor.fetch_my_team_authed(session, entry)
                              |
                              v
        repository.snapshot_my_team_authed(conn, gw=next_gw, payload)
                              |
                              v
                         my_team table
                              |
              ORDER BY gw DESC LIMIT 1 picks the new row
                              |
        +---------+------------+------------+--------------+
        v         v            v            v              v
   get_squad   _latest_squad  captain    chips reader   run_transfer
   (queries)   (transfers)    reader                    (preflight check)
```

## Components

### `src/execution/executor.py`

Add `fetch_my_team_authed(session, entry_id) -> dict`.

- Hits `GET https://fantasy.premierleague.com/api/my-team/{entry}/` (the URL already
  defined as `MY_TEAM_URL`).
- Returns the full JSON payload (caller decides what to extract). The existing
  `fetch_current_picks` keeps returning just `picks` — internal lineup callers don't
  need the rest.
- Raises `ExecutorError` on non-200 with status code (same shape as
  `fetch_current_picks`).

### `src/data/repository.py`

Add `snapshot_my_team_authed(conn, gw: int, payload: dict) -> None`.

- Extracts:
  - `picks` -> `picks_json` (JSON-serialised, same shape as the public path uses).
  - `transfers.bank` -> `bank` (as float, /10 to match the public path's units).
  - `transfers.value` -> `team_value` (same /10 convention).
  - `transfers.limit` -> `free_transfers` (int). FPL convention: `limit` is the FT
    count for the upcoming GW. Verified against the 2026-05-24 live capture.
  - `chips` -> `chips_used_json` (list of used chips; same shape the public path
    pulls from `entry_history.chips_played` — keep nullable on missing).
- INSERT OR REPLACE on `gw` PK (the existing snapshot does the same).
- Schema-asserts: if `transfers`, `picks`, or `transfers.limit` are missing, raise
  `KeyError` (per B6 — fail loudly on FPL drift).

### `src/scheduler.py`

Update `refresh_and_recompute(cfg=None, conn=None, client=None,
understat_client=None, key=None)`:

- After the existing public refresh + recompute, **if `key is not None`**:
  - Call `auth_session.ensure_session(conn, key)`.
  - On `TokenRefreshError`: log, return (refresh succeeded as far as public data).
    Existing `maybe_auto_freeze` already increments `relogin_failures`.
  - Otherwise call `executor.fetch_my_team_authed(session, entry_id)`.
  - On `ExecutorError` (non-200): log + Telegram alert, return.
  - Otherwise `snapshot_my_team_authed(conn, gw=next_gw, payload=...)`.
- Authed refresh failure **does not roll back the public refresh**. The DB simply
  retains the older authed row (or only the public row) until the next cycle.

Update `build_scheduler` to pass `key=...` to the job's partial.

### `src/cli.py`

New command `refresh-my-team`:

- Prompts for master password (via the existing `master.unlock` helper).
- Runs the authed snapshot step once.
- Prints `my_team OK (authed, GW{next_gw}, FT={free_transfers})` on success.
- Falls back with a clear error if no `init-fpl` has been run yet.

### `src/decisions/transfers.py`

Change `_latest_squad(conn)`:

- Return tuple `(ids, bank, free_transfers)` instead of `(ids, bank)`.
- `free_transfers` is `int | None` — `None` when only the public row exists.

Change `get_transfer_suggestions(conn)`:

- Propagate `free_transfers` into the returned dict at the top level:
  `{"suggestions": [...], "empty_reason": ..., "free_transfers": int | None}`.
- Per-suggestion `hit_cost` stays `0` for this slice (B4 — non-goal).

### `src/execution/transfer.py`

Update `run_transfer(conn, key, *, rank=1, live=False, confirm_fn=None,
session=None, suggester=None, allow_hit=False)`:

- Before the live-execution branch, read `free_transfers` from the latest `my_team`
  row.
- If `free_transfers is not None` and `free_transfers == 0`:
  - If `live` and not `allow_hit`: log activity (`action_taken="refused: would cost -4 hit"`),
    return an `ExecResult(dry_run=True, ..., ok=False)` with a clear `request.note`.
  - If `live` and `allow_hit`: proceed (user opt-in).
  - If not `live`: proceed (dry-run is observational by design).
- If `free_transfers is None`: log a warning ("free_transfers unknown — run
  refresh-my-team for accurate hit math") but proceed. This is the
  authed-snapshot-missing case.

### `src/interface/deadguard.py`

Update `_pick_flagged_transfer`:

- Add a free-transfers preflight: read `free_transfers` from `my_team`. If `0`,
  return `None` (no candidate) regardless of EP/confidence. B8: deadguard never takes
  a hit.
- If `None` (no authed snapshot yet), conservatively return `None` (safer than
  guessing). Logged once per trigger.

### `src/interface/queries.py`

No code change — `get_squad` already selects `free_transfers` and returns it. After
this slice, the value is non-null when an authed snapshot exists.

### `src/cli.py refresh`

No behaviour change. The user-facing `refresh` command stays public-only. The two
ways to get the authed row are unattended mode (automatic) or the new
`refresh-my-team` CLI.

### Docs

- `docs/api-contract.md` — `GET /api/status` and `GET /api/squad` example responses
  show non-null `free_transfers` and a note about the authed source.
- `docs/runbook.md` — add `refresh-my-team` CLI to the operator commands table.
- `docs/decision-engine.md` — **no change** (no decision logic touched).
- `docs/deadguard.md` — short note in `_pick_flagged_transfer` rules: "refuses when
  `free_transfers == 0` or unknown".

## Data flow

### Happy path (unattended mode)

1. Hourly scheduler tick -> `refresh_and_recompute(key=...)`.
2. Public refresh writes `my_team` row at `gw=last_finished` (existing behaviour).
3. `ensure_session(conn, key)` returns a healthy session.
4. `fetch_my_team_authed(session, entry)` -> 200 with `{picks, transfers:{bank, value, limit}, chips}`.
5. `snapshot_my_team_authed(conn, gw=next_gw, payload)` writes a second `my_team` row.
6. Next dashboard load: `get_squad` returns the `gw=next_gw` row -> live squad +
   real `free_transfers`.
7. Next `run_transfer`: preflight reads `free_transfers` -> safe.

### Authed fetch fails (session expired, 5xx, network blip)

1. Public refresh succeeds.
2. Authed step logs the failure (and Telegram alert via existing notifier).
3. DB keeps the older authed row (if any) or only the public row.
4. Readers see the most recent row available. `get_squad` returns it; behaviour
   identical to today's state. No crash.

### Schema drift (FPL response shape changes)

1. `snapshot_my_team_authed` raises `KeyError` per B6.
2. Wrapper in scheduler catches, logs full exception (no secrets), Telegram alert.
3. Public refresh row stays as fallback.

### `run_transfer` with `free_transfers=0`

1. CLI: `execute-transfer --live`.
2. Preflight reads `0`, no `--allow-hit` -> `ExecutorError`-shaped result with
   `ok=False`, request body unset, activity log entry `"refused: would cost -4 hit"`.
3. CLI exits non-zero with a clear message.

### Deadguard trigger with `free_transfers=0`

1. H-30 trigger fires.
2. `_run_trigger` runs lineup write (captain/vice + bench), succeeds.
3. `_pick_flagged_transfer` sees `free_transfers=0` -> returns `None`.
4. Deadguard log entry: `"transfer step skipped: no free transfer available"`.

## Error handling summary

| Failure mode | Handling |
|---|---|
| Authed 401/403 | `ensure_session` already raises `SessionExpired`; scheduler catches, increments `relogin_failures` via `maybe_auto_freeze`; freezes at 2 consecutive. |
| Authed 5xx / network timeout | `ExecutorError` caught at scheduler; logged + Telegram alerted; DB unchanged. |
| Missing fields in response (B6 drift) | `KeyError` raised in `snapshot_my_team_authed`; caught at scheduler; full stack logged (no secrets); Telegram alerted. |
| `free_transfers` column NULL in DB at preflight | Treated as unknown; `run_transfer` logs warning and proceeds; deadguard refuses (safer default). |

## Testing

All tests are fixtures-only per R3.

### Unit

- **`tests/test_repository.py`**
  - `snapshot_my_team_authed` extracts `picks_json`, `bank`, `team_value`,
    `free_transfers`, `chips_used_json` from a representative payload.
  - Idempotent on rerun (INSERT OR REPLACE).
  - Raises `KeyError` when `transfers` or `transfers.limit` is missing.
  - Stores chip list when present, NULL when absent.

- **`tests/test_executor.py`**
  - `fetch_my_team_authed` calls `MY_TEAM_URL`, returns parsed JSON on 200.
  - Raises `ExecutorError` with status code on non-200.

- **`tests/test_scheduler.py`**
  - `refresh_and_recompute(key=fake_key)` invokes authed path when session healthy.
  - Skips authed path when `key=None`.
  - Does NOT crash public refresh when authed raises (Executor / Session /
    KeyError).
  - Passes `next_gw` (not `last_finished`) into `snapshot_my_team_authed`.

- **`tests/test_transfer.py`**
  - `run_transfer` with `free_transfers=0`, `live=True`, `allow_hit=False`
    -> refuses; activity log entry recorded; `ok=False`.
  - Same setup with `allow_hit=True` -> proceeds normally.
  - With `free_transfers=1` (or higher) -> proceeds without checking allow_hit.
  - With `free_transfers=None` -> warning logged, proceeds.
  - Dry-run is never blocked by the preflight.

- **`tests/test_decisions_transfers.py`** (or its existing equivalent)
  - `_latest_squad` returns 3-tuple including `free_transfers`.
  - `get_transfer_suggestions` result dict includes top-level `free_transfers` key.

- **`tests/test_deadguard.py`**
  - `_pick_flagged_transfer` returns `None` when `free_transfers=0`.
  - Returns `None` when `free_transfers IS NULL` (unknown).
  - Returns a candidate (existing behaviour) when `free_transfers>=1` and the rest
    of the gates pass.

- **`tests/test_queries.py`**
  - `get_squad` returns the authed row (newer `gw`) when both authed and public
    rows exist.
  - Returns the public row when only the public row exists. `free_transfers` is
    `None`.

### Integration / wiring

- **`tests/test_cli.py`** — `refresh-my-team` command prompts for master pw, runs
  the snapshot, prints summary. Failure path (no `init-fpl` yet) prints an
  actionable error and exits non-zero.

### Manual smoke

- After merge, in a local dev shell:
  - `fpl-autopilot refresh` (public only) -> confirm DB has `gw=last_finished`,
    `free_transfers IS NULL`.
  - `fpl-autopilot refresh-my-team` -> confirm DB now has `gw=next_gw` row with
    real `free_transfers`.
  - Open dashboard -> confirm squad reflects the upcoming GW. Make a (dry-run)
    transfer -> confirm preflight respects `free_transfers`.

## Rollout / risk

- **Backwards compatible.** Public refresh path untouched. New columns / new rows
  are additive. `get_squad` and decision-layer readers default to NULL-safe paths.
- **No schema migration.** `my_team.free_transfers` already exists; the cache TTL
  is unchanged.
- **B-rules:** B2 boundaries (web no key, decision unchanged), B4 sacred (no
  decision-engine.md change), B6 FPL discipline (assertions + rate-limit reuse),
  B7 secrets (key only in scheduler/CLI), B8 deadguard (becomes strictly safer),
  B11 dry-run (preflight runs before dry-run branch).
- **R3 (auto-execution legality):** the agent never runs `--live` or unattended
  mode. The user invokes `refresh-my-team` or starts the daemon themselves.

## Open questions

None — design is closed.

## Definition of done

- All unit tests above pass.
- `pytest -q` reports the new total (current 404 + new tests, all green).
- `frontend && npm test` still 50 passed (no frontend change).
- Manual smoke checks above pass against a real-account session.
- `docs/api-contract.md`, `docs/runbook.md`, `docs/deadguard.md` updated.
- Activity log entries for refusal and for snapshot success are visible via
  `fpl-autopilot log --tail`.
- A `HANDOFF.md` update marks findings #1 and #4 as resolved.
