# Action Executor — captain/vice write (Phase 2.2a) — Design

**Status:** approved 2026-05-23
**Slice:** Phase 2.2a (Decision Automation — execution). First slice that *writes* to the live FPL account.
**Depends on:** 2.1 token-capture auth (`src/auth/session.ensure_session`), the captain ranker (`src/decisions/captain.get_captain_picks`), the `activity_log` table.

## Goal

A dry-run-first executor that reads the current team, sets **captain & vice** from the ranker, and writes it back via the FPL API — proving the execution machinery on the lowest-stakes, reversible action. Live execution requires an explicit flag + typed confirmation; the agent only ever runs dry-run.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Action in scope | **Captain & vice only**; the existing starting XI and bench order are **preserved** unchanged. (Bench *reordering* needs a bench optimizer that doesn't exist yet — deferred.) |
| Write path (provisional) | `POST https://fantasy.premierleague.com/api/my-team/{entry}/`, body `{"chip": null, "picks": [{element, position, is_captain, is_vice_captain} ×15]}`, header `X-Api-Authorization: Bearer` (on the session from `ensure_session`). Derived from the FPL JS bundle; **provisional until the user's first live save confirms** exact header/`chip`/response. |
| Safety | Dry-run is the default (constructs + logs the request, sends nothing). `--live` shows the captain/vice change and requires a typed `yes` before POSTing. |
| Master key | Loaded per-invocation via `master.get_master_key()` (env/getpass) when the user runs the command — no long-running in-memory key yet (that's 2.3 + scheduler wiring). |
| Trigger | A user-invoked CLI command (`execute-lineup`). Auto/scheduled execution is the Mode Router's job (2.3). |

## Architecture & placement (new execution layer)

```
src/execution/__init__.py
src/execution/executor.py   ← fetch_current_picks, build_lineup_payload, apply_lineup, ExecResult (pure write mechanism)
src/execution/lineup.py     ← run_lineup(): read → rank → build → (confirm if live) → apply → log
src/cli.py                  ← execute-lineup command (dry-run default; --live)
src/data/repository.py      ← log_activity() (first activity_log writer)
```

Respects B2: the executor consumes Decision-Layer output (the captain ranker) and the authed session (auth layer), and performs the write. It computes no decisions itself.

## Module: `src/execution/executor.py`

```python
MY_TEAM_URL = "https://fantasy.premierleague.com/api/my-team/{entry}/"
TIMEOUT = 10

@dataclass
class ExecResult:
    dry_run: bool
    request: dict      # {"method","url","body"}  — the exact request (would-be for dry-run)
    status: int | None # HTTP status for live; None for dry-run
    ok: bool           # dry-run: True; live: status == 200


class ExecutorError(Exception):
    """Invalid lineup payload (e.g. captain not in squad)."""
```

- `fetch_current_picks(session, entry_id) -> list[dict]` — `GET MY_TEAM_URL`, return `resp.json()["picks"]` (each pick: `element, position, multiplier, is_captain, is_vice_captain, element_type, selling_price, purchase_price`). Non-200 → `ExecutorError`.
- `build_lineup_payload(current_picks, captain_id, vice_id) -> dict` — **pure**. Validates `captain_id != vice_id` and both are `element`s present in `current_picks` (else `ExecutorError`). Returns
  `{"chip": None, "picks": [{"element": p["element"], "position": p["position"], "is_captain": p["element"] == captain_id, "is_vice_captain": p["element"] == vice_id} for p in current_picks]}` —
  order/positions preserved, only the two flags change.
- `apply_lineup(session, entry_id, payload, *, dry_run) -> ExecResult` — builds `request = {"method": "POST", "url": MY_TEAM_URL.format(entry=entry_id), "body": payload}`. If `dry_run`: return `ExecResult(dry_run=True, request=request, status=None, ok=True)` — **no network**. Else: `resp = session.post(url, json=payload, timeout=TIMEOUT)`; return `ExecResult(dry_run=False, request=request, status=resp.status_code, ok=resp.status_code == 200)`. The token lives only on the session headers — never in `request` or logs.

## Module: `src/execution/lineup.py`

```python
def run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None) -> ExecResult
```
- `session = session or auth_session.ensure_session(conn, key)` (Bearer session; auto-refreshes; raises `SessionNotInitialized`/`SessionExpired` → caller surfaces "run init-fpl").
- `entry = config.team_id()`.
- `current = executor.fetch_current_picks(session, entry)`.
- `caps = (ranker or captain.get_captain_picks)(conn)`; if `not caps["picks"]` → `ExecutorError("no captain pick available (no data?)")`. `captain_id = caps["picks"][0]["player_id"]`, `vice_id = caps["vice_player_id"]`.
- `payload = executor.build_lineup_payload(current, captain_id, vice_id)`.
- `diff` = human-readable current (C/VC) → desired (C/VC).
- If `live`: `confirm_fn` (defaults to a CLI typed-`yes` prompt that prints `diff`) must return truthy; if not, log `executed=False, action="aborted"` and return a dry-run-style `ExecResult` (nothing sent). If confirmed: `apply_lineup(..., dry_run=False)`.
- If not `live`: `apply_lineup(..., dry_run=True)`.
- `repository.log_activity(...)` always (see below). Return the `ExecResult`.

`confirm_fn(diff:str)->bool`, `session`, and `ranker` are injectable so tests never hit the network or stdin.

## CLI: `execute-lineup`

`_execute_lineup_cli(conn=None, salt_path=None, verify_path=None, live=False, session=None, ranker=None, confirm_fn=None)`:
load key (`get_master_key`), open DB if needed, call `run_lineup(conn, key, live=live, confirm_fn=confirm_fn, session=session, ranker=ranker)`, print the outcome (the would-be request for dry-run; the status for live). Register `execute-lineup` with a `--live` flag in `main()`; dispatch `_execute_lineup_cli(live=args.live)`. Default (no `--live`) = dry-run. The real `confirm_fn` prints the diff and reads a typed `yes` via `input`.

## Logging (`activity_log`, B10)

Add `repository.log_activity(conn, *, decision_type, mode, action_taken, inputs_json, executed, exec_outcome_json, gw=None, alternatives_json=None)` — a parameterized INSERT into `activity_log` (`ts_utc` = now UTC). The executor logs one row per run:
- `decision_type="lineup"`, `mode="manual"` (2.2a is user-invoked; modes are 2.3).
- `action_taken` e.g. `"captain=<web_name>, vice=<web_name>"`, or `"dry-run"`, or `"aborted"`.
- `inputs_json` = the ranker's top picks + xP + `vice_player_id`.
- `executed` = `False` for dry-run/aborted, `True` for a live POST.
- `exec_outcome_json` = the constructed `request` (dry-run) or `{"status": <code>}` (live). **No token.**

First writer of `activity_log`.

## Error handling & safety

- `SessionExpired`/`SessionNotInitialized` → clean message ("run init-fpl"), nothing submitted, nothing logged as executed.
- `ExecutorError` (bad payload / no pick / non-200 read) → clean message, no write.
- Live POST non-200 → `ExecResult.ok=False`, logged with the status, surfaced; **no retry** (B6).
- The Bearer token never appears in `request`, the diff, stdout, or `activity_log`.
- Write shape is **provisional**: dry-run prints the exact request so the user verifies it before the first `--live`; the user's first live save confirms header/`chip`/response.
- The agent never runs `--live` (R3); the user does.

## Testing — fixtures only, never live

Inject a fake authed session (canned `/my-team` GET; records/raises on `.post`), a fake `ranker`, and a `confirm_fn` lambda. Throwaway data; no network/stdin.
1. `build_lineup_payload` — sets `is_captain`/`is_vice_captain` on the right elements, preserves the other 13 picks + order; `captain==vice` and captain-not-in-squad → `ExecutorError`.
2. `fetch_current_picks` — returns `picks` from a fake 200; non-200 → `ExecutorError`.
3. `apply_lineup` dry-run — returns the request, fake session's `.post` is **never called**.
4. `apply_lineup` live — fake session records the POST url/json; `ok` reflects status (200 vs 4xx).
5. `run_lineup` dry-run — builds payload from fake current + fake ranker, logs `executed=False`, sends nothing.
6. `run_lineup` live + `confirm_fn=lambda d: True` — POSTs, logs `executed=True`.
7. `run_lineup` live + `confirm_fn=lambda d: False` — aborts, nothing sent, logs `executed=False, action="aborted"`.
8. `log_activity` — row round-trips with the expected columns.

## Scope boundary

- **IN:** captain/vice executor (`executor.py`), `run_lineup` orchestration, `execute-lineup` CLI (dry-run default + `--live`+typed-confirm), `activity_log` writer, payload from the real `/my-team` shape.
- **OUT → 2.2b:** transfer execution (`POST entry/{id}/transfers/`).
- **OUT → 2.3:** Mode Router (auto/manual/hybrid), confidence-gated auto-execution, scheduler wiring + long-running master key.
- **OUT (future decision slice):** bench-order optimizer (so bench *reordering*).
- **OUT → 2.4:** Telegram confirmations (the CLI typed-confirm is the 2.2a stand-in).

## Definition of done (CLAUDE.md B14)

- `execute-lineup` (no flag) dry-runs: prints the exact `POST /my-team/{entry}/` request it would send and logs `executed=False`.
- `execute-lineup --live` shows the captain/vice change, requires typed `yes`, then POSTs and logs `executed=True` (the user runs this; the agent does not).
- All tests pass fixtures-only; the suite stays green; no token logged.
- Manual smoke check (out of band, by the user): run `execute-lineup` (dry-run), confirm the printed request looks right; optionally one `--live` save to confirm the provisional write shape.
