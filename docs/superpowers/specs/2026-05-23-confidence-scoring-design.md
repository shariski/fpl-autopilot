# Confidence Scoring — Design (Phase 2.3a)

**Status:** approved 2026-05-23
**Slice:** Phase 2.3a (prerequisite for the Mode Router, 2.3b)
**Depends on:** the captain ranker (`src/decisions/captain.get_captain_picks`), the transfer engine (`src/decisions/transfers.get_transfer_suggestions`), `cache_meta`, `players.status`.

## Goal

Implement the confidence formula already specified in `decision-engine.md` and surface a real
`confidence` (0–100) on captain and transfer decisions (currently `None`). This is the input the
Mode Router's auto-execution gate (`confidence < 70 → notify instead of execute`) will consume in
2.3b. This slice only **computes and surfaces** confidence — nothing routes on it yet.

## Formula (from `decision-engine.md`, made precise here)

```
confidence = 75
           - staleness_penalty            # 0 (<6h) | 10 (6–24h) | 30 (>24h or unknown)
           - status_uncertainty_penalty   # worst of the involved players
           - alternative_proximity_penalty
```

- **staleness:** hours since `cache_meta.last_fetched_utc` for `bootstrap-static` (the anchor —
  player status/price source). Missing row → treated as >24h (+30).
- **status_uncertainty (graded, locked):** per involved player — `a`→0, `d`→+15, everything else
  (`i`/`s`/`u`/`n`/unknown/empty)→+30; the penalty is the **worst** among the players involved.
- **alternative_proximity (gap between top and second-best):** `gap > 2`→0, `1 ≤ gap ≤ 2`→+5,
  `0.5 ≤ gap < 1`→+15, `gap < 0.5`→+25. `gap is None` (no second option) → 0.

Result clamped to `[0, 100]`.

## New module `src/decisions/confidence.py`

```python
_STATUS_PENALTY = {"a": 0, "d": 15}  # default 30 for i/s/u/n/unknown/empty


def _status_penalty(status):
    return _STATUS_PENALTY.get(status, 30)


def score(*, staleness_hours, statuses, gap):
    c = 75
    if staleness_hours is None or staleness_hours > 24:
        c -= 30
    elif staleness_hours > 6:
        c -= 10
    c -= max((_status_penalty(s) for s in statuses), default=0)
    if gap is None or gap > 2:
        c -= 0
    elif gap >= 1:
        c -= 5
    elif gap >= 0.5:
        c -= 15
    else:
        c -= 25
    return max(0, min(100, c))


def hours_since_refresh(conn, resource="bootstrap-static"):
    row = conn.execute("SELECT last_fetched_utc FROM cache_meta WHERE resource=?",
                       (resource,)).fetchone()
    if row is None:
        return None
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(row["last_fetched_utc"])
    return delta.total_seconds() / 3600.0
```

`score` is pure. `hours_since_refresh` reuses the same `cache_meta` shape `src/data/cache.py` uses.

## Wiring into the readers

### `get_captain_picks(conn)` — add `confidence` to the returned dict
After building `picks` and `vice_player_id`, compute:
- `staleness = confidence.hours_since_refresh(conn)`
- `statuses` = `players.status` for the captain (`picks[0]["player_id"]`) and the vice
  (`vice_player_id`), looked up via one query (e.g. `SELECT id, status FROM players WHERE id IN (?,?)`).
- `gap = picks[0]["xp"] - picks[1]["xp"]` when `len(picks) >= 2`, else `None`.
- Return `{"picks": picks, "vice_player_id": vice, "confidence": confidence.score(...)}`.
  When `picks` is empty, `confidence` is `None` (no decision to score).

### `get_transfer_suggestions(conn)` — fill each suggestion's `confidence` (was `None`)
The underlying `pairs` carry full `in`/`out` dicts (with `status`). For suggestion at index `i`:
- `statuses = [pair["in"]["status"], pair["out"]["status"]]`
- `gap = pairs[i]["ep_delta_5gw"] - pairs[i+1]["ep_delta_5gw"]` if a next pair exists, else `None`.
- shared `staleness = confidence.hours_since_refresh(conn)`.
- set the projected suggestion's `"confidence" = confidence.score(...)` (replacing `None`).

## Doc updates (B4 / B13)

- **`decision-engine.md`** — under the confidence section, add the precise status-code→penalty
  mapping (`a`=0, `d`=+15, `i`/`s`/`u`/`n`=+30, worst-of-involved) and the staleness anchor
  (`bootstrap-static`), with a changelog entry (B4: decision-logic detail pinned).
- **`docs/api-contract.md`** — note `/captain` now returns `confidence` (additive; `/transfers`
  suggestions already declare the field).

## Error / edge handling
- No `cache_meta` row → `staleness_hours=None` → +30. Single captain pick / single suggestion →
  `gap=None` → 0 proximity penalty. Unknown/empty status → +30. Confidence clamped `[0,100]`.
- No network, no auth, no execution in this slice.

## Testing — deterministic, fixtures only

1. `confidence.score` — table of pure cases: all-clear (fresh, `a`, gap 3) → 75; staleness tiers
   (12h→65, 30h→45, None→45); status tiers (`d`→60, `i`→45, worst-of `["a","d"]`→60); gap tiers
   (gap 1.5→70, gap 0.7→60, gap 0.2→50); clamp (stacked penalties floor at 0); `gap=None`→no penalty.
2. `hours_since_refresh` — fresh (~0), 12h, 30h, missing row → None (use a frozen `cache_meta` row).
3. `get_captain_picks` integration (frozen fixtures): returns an `int` confidence; a doubtful
   captain (`status='d'`) drops it ~15; a tight top-2 gap drops it; empty picks → `confidence None`.
4. `get_transfer_suggestions` integration: each suggestion has an `int` confidence reflecting
   in/out status + gap-to-next. (Existing reader tests updated to expect the field, not `None`.)

## Scope boundary
- **IN:** `confidence.py`, confidence wiring into both readers, the `decision-engine.md` +
  `api-contract.md` doc updates.
- **OUT → 2.3b:** the Mode Router that gates execution on confidence.
- **OUT → 2.3c / 2.4:** unattended scheduling; Telegram notifications.

## Definition of done (CLAUDE.md B14)
- `confidence.score` matches the documented formula; `get_captain_picks`/`get_transfer_suggestions`
  return real integer confidences.
- `decision-engine.md` records the status mapping (changelog); `api-contract.md` notes the captain
  field.
- All tests pass (including updated reader tests); suite stays green; no network/auth touched.
