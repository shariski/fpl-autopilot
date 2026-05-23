# Transfer Execution — Design (Phase 2.2b)

**Status:** approved 2026-05-23
**Slice:** Phase 2.2b (Decision Automation — execution). The higher-stakes write: a single free transfer.
**Depends on:** 2.2a Action Executor (`src/execution/executor.py`, `src/execution/lineup.py` patterns, `repository.log_activity`), `src/auth/session.ensure_session`, `src/decisions/transfers.get_transfer_suggestions`.

## Goal

Extend the executor to submit **one free transfer** (the engine's chosen suggestion) via the FPL API — dry-run-first, `--live` + typed confirmation. Reuses the 2.2a foundation; the agent only ever dry-runs.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Scope | One **free** transfer. `suggest_transfers` only produces single free transfers (`hit_cost=0`), so hits / multiple transfers / chips are never submitted. `chip` hardcoded `null`. |
| Selection | Suggestion #1 by default; `--rank N` (1-based) picks another; dry-run lists all suggestions. |
| Safety | Dry-run default; `--live` shows the OUT→IN change and needs a typed `yes`. Same gate as 2.2a. |
| Write path (provisional) | `POST https://fantasy.premierleague.com/api/entry/{entry}/transfers/`, body `{"chip": null, "entry": <id>, "event": <gw>, "transfers": [{element_in, element_out, purchase_price, selling_price}]}`, header `X-Api-Authorization: Bearer`. Bundle-derived; confirmed at first live transfer. |
| Prices | `selling_price` from the live `/my-team` picks (exact); `purchase_price` = DB `price × 10` (= `now_cost`). Stale local price → API rejects (`transfer_element_in_price_mismatch`) safely. |

## Architecture (extends the 2.2a execution layer)

```
src/execution/executor.py   ← ADD TRANSFERS_URL, build_transfer_payload, apply_transfers;
                              refactor apply_lineup + apply_transfers to share a _post_json helper (behavior identical)
src/execution/transfer.py   ← NEW run_transfer orchestration
src/cli.py                  ← ADD execute-transfer command (--live, --rank)
```

Reused unchanged: `fetch_current_picks`, `ExecResult`, `ExecutorError`, `repository.log_activity`,
`auth.session.ensure_session` + `SessionError`, `config.team_id`,
`decisions.transfers.get_transfer_suggestions` (returns `{"suggestions": [{"out":{player_id,web_name,price},
"in":{player_id,web_name,price}, "ep_delta_5gw", "hit_cost":0, "confidence":None}], "empty_reason": str|None}`)
and `decisions.transfers._next_gw` for the `event`.

## `executor.py` additions

```python
TRANSFERS_URL = "https://fantasy.premierleague.com/api/entry/{entry}/transfers/"


def _post_json(session, url, payload, *, dry_run):
    request = {"method": "POST", "url": url, "body": payload}
    if dry_run:
        return ExecResult(dry_run=True, request=request, status=None, ok=True)
    resp = session.post(url, json=payload, timeout=TIMEOUT)
    return ExecResult(dry_run=False, request=request, status=resp.status_code, ok=resp.status_code == 200)


def apply_lineup(session, entry_id, payload, *, dry_run):       # refactored to delegate (same behavior)
    return _post_json(session, MY_TEAM_URL.format(entry=entry_id), payload, dry_run=dry_run)


def apply_transfers(session, entry_id, payload, *, dry_run):
    return _post_json(session, TRANSFERS_URL.format(entry=entry_id), payload, dry_run=dry_run)


def build_transfer_payload(*, entry, event, element_out, element_in, selling_price, purchase_price):
    return {"chip": None, "entry": entry, "event": event,
            "transfers": [{"element_in": element_in, "element_out": element_out,
                           "purchase_price": purchase_price, "selling_price": selling_price}]}
```

`apply_lineup`'s existing tests stay green (behavior identical). `build_transfer_payload` is pure.

## Orchestration (`transfer.py`)

```python
def run_transfer(conn, key, *, rank=1, live=False, confirm_fn=None, session=None, suggester=None):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    suggestions = sugg["suggestions"]
    if not suggestions:
        raise executor.ExecutorError(sugg.get("empty_reason") or "no transfer suggestion available")
    if not (1 <= rank <= len(suggestions)):
        raise executor.ExecutorError(f"rank {rank} out of range (1..{len(suggestions)})")
    chosen = suggestions[rank - 1]
    element_out = chosen["out"]["player_id"]
    element_in = chosen["in"]["player_id"]
    purchase_price = round(chosen["in"]["price"] * 10)
    current = executor.fetch_current_picks(session, entry)
    selling_price = next((p["selling_price"] for p in current if p["element"] == element_out), None)
    if selling_price is None:
        raise executor.ExecutorError(f"player {element_out} not in current squad")
    event = transfers._next_gw(conn)
    payload = executor.build_transfer_payload(entry=entry, event=event, element_out=element_out,
                                              element_in=element_in, selling_price=selling_price,
                                              purchase_price=purchase_price)
    diff = (f"OUT {chosen['out']['web_name']} -> IN {chosen['in']['web_name']} "
            f"(EP +{chosen['ep_delta_5gw']})")
    inputs = {"chosen": chosen,
              "alternatives": [s for i, s in enumerate(suggestions) if i != rank - 1]}
    url = executor.TRANSFERS_URL.format(entry=entry)

    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual",
                                action_taken="aborted", inputs=inputs, executed=False,
                                exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)

    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    action = f"OUT {element_out} IN {element_in}" if live else "dry-run"
    repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
```

## CLI: `execute-transfer`

`_execute_transfer_cli(conn=None, salt_path=None, verify_path=None, live=False, rank=1, session=None, suggester=None, confirm_fn=None)` — mirrors `_execute_lineup_cli`: master gate → `get_master_key` → default `confirm_fn` (prints the diff + the alternatives, reads typed `yes`) → `run_transfer(...)` → print dry-run request / "Submitted" / "Aborted" / failure. Catches `(ExecutorError, SessionError)`. Register `execute-transfer` with `--live` (store_true) and `--rank` (int, default 1); dispatch `_execute_transfer_cli(live=args.live, rank=args.rank)`.

## Prices & the mismatch caveat

`selling_price` is the API's own value from `/my-team` — never mismatches. `purchase_price` is computed from the DB `price` (`price × 10` = `now_cost`); if the local price is stale, the live POST is rejected with `transfer_element_in_price_mismatch` — a **safe** failure: FPL transfers are atomic, so nothing changes; it is logged (`executed=False`) and surfaced. Mitigation: run `refresh` first; the dry-run prints `purchase_price` to sanity-check. Fetching live `now_cost` at execute time is a future refinement (out of scope).

## Safety

Same gate as 2.2a: the only path to a live POST is `--live` AND a truthy `confirm_fn`. Structurally bounded — exactly one transfer, `chip: None`, `hit_cost` always 0 — so no hit, no chip, no multi-transfer can be submitted. The dry-run prints the exact request + alternatives before any `--live`. The agent never runs `--live` (R3); the user does.

## Logging (`activity_log`, B10)

`decision_type="transfer"`, `mode="manual"`, `action_taken` (`OUT <id> IN <id>` / `dry-run` / `aborted`), `inputs` = chosen suggestion + alternatives (+ ep_delta), `executed` true only on a successful live POST, `exec_outcome` = `{status, request}` (no token).

## Testing — fixtures only, never live

Inject a fake session (canned `/my-team` GET carrying `selling_price`; records `.post`), an injected `suggester`, a `confirm_fn` lambda. Throwaway data; no network/stdin.
1. `build_transfer_payload` — correct shape: `chip None`, one transfer with the 4 fields, right entry/event.
2. `apply_transfers` — dry-run sends nothing; live POSTs to `entry/{entry}/transfers/`; non-200 → `ok=False`. (And `apply_lineup` still passes after the `_post_json` refactor.)
3. `run_transfer` dry-run — payload from suggestion #1; `selling_price` taken from the live picks (not the suggestion's price); logs `executed=False`; sends nothing.
4. `run_transfer` `rank=2` — picks the 2nd suggestion.
5. `run_transfer` live confirmed → POSTs, `executed=True`; aborted (`confirm_fn` False) → nothing sent, logs `aborted`.
6. empty suggestions → `ExecutorError`; rank out of range → `ExecutorError`; `element_out` not in squad → `ExecutorError`.
7. CLI `execute-transfer` — dry-run (prints request, nothing sent); live confirmed (submitted, `executed=1`); no master password → message, nothing logged.

## Scope boundary

- **IN:** single free-transfer execution (`build_transfer_payload`, `apply_transfers`, `run_transfer`), `execute-transfer` CLI (`--live`, `--rank`), the `_post_json` refactor.
- **OUT (future decision-layer):** hits / multiple transfers / wildcard / free-hit (the suggester would need to produce them).
- **OUT → 2.3:** Mode Router (auto/manual/hybrid), confidence gating, scheduler + long-running master key.
- **OUT (future refinement):** live `now_cost` fetch at execute time to avoid stale-price rejection.
- **OUT → 2.4:** Telegram confirmation.

## Definition of done (CLAUDE.md B14)

- `execute-transfer` (no flag) dry-runs: prints the exact `POST entry/{entry}/transfers/` request (and the alternatives) and logs `executed=False`.
- `execute-transfer --live` (optionally `--rank N`) shows the OUT→IN change, requires typed `yes`, then POSTs and logs `executed=True` (user-run; the agent does not).
- `apply_lineup` still green after the shared-helper refactor; all new tests fixtures-only; suite stays green; no token logged.
- Manual smoke check (out of band, by the user): `execute-transfer` dry-run, confirm the printed request + prices look right.
