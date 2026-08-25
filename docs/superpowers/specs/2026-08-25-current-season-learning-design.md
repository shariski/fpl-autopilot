# Current-Season Learning (Live GW Stats → Ratings Blend) — Design

**Date:** 2026-08-25 · **Status:** approved in brainstorming · **Scope:** data layer + analytics (B2: no decision-layer or interface changes)

## 1. Problem

The xP v2 / FDR v2 ratings read exclusively from Vaastav databank rows (`player_stats`, `source='fpl_databank:<season>'`). Vaastav does **not** maintain the databank in-season for 26/27 (last commits ~6 months ago; current-season GW CSVs 404). The model therefore projects from 25-26 data only — the GW1 blind spot (Dubravka benched → 0 captain points; upside events under-predicted 2.5-3.6 → 11-17).

FPL's own `event/{id}/live` endpoint (already fetched hourly by settlement) provides **every field** the ratings need — verified live 2026-08-25: `starts`, `expected_goals`, `expected_assists`, `expected_goals_conceded`, `defensive_contribution`, `saves`, `bps`, `yellow_cards`, `red_cards`, `bonus`, `total_points`, plus per-fixture `explain` for DGWs. This makes an in-season, Vaastav-free learning path possible.

## 2. Decisions (approved with the user)

1. **Blend strategy: natural window.** Current-season live rows enter the existing LF(38)/SF(6) rating windows, ordered by season then GW. Zero new blend constants; doc-consistent ("windows span seasons"). Pre-season behavior (no live rows yet) is byte-identical to today.
2. **GW1 backfill: automatic.** Settlement re-fetches `event/{id}/live` for any settled GW whose rows are missing the new columns and fills them (idempotent UPDATE; GW1 heals on first refresh after deploy; self-heals future column gaps).
3. **Validation: simulated-season backtest.** Treat 24-25 as prior, 25-26 as the "live" season; step GW-by-GW through the natural-window blend and compare projections vs actuals, vs the pure-prior baseline.

## 3. Architecture

```
FPL event/{id}/live  ──►  settlement_run  ──►  player_gw_stats (extended, auto-backfill)
                                                        │
                       ratings._rating_rows(conn) ◄─────┘  (union: databank rows + live rows)
                                  │
              compute_team_ratings / compute_player_rates / promoted gate / league averages
                                  │
              FDR v2 multipliers · xP v2 rates · captain/transfer/bench/chip consumers
```

Analytics keeps reading from the DB only (B2). The Decision Layer is untouched — it consumes the same xP/FDR outputs, now fed by current-season evidence.

## 4. Data capture

### 4.1 Schema (db.py migration, idempotent — follows `_migrate_player_stats` pattern)

`player_gw_stats` gains 9 columns (existing 7 unchanged: player_id, gw, fixture_id, minutes, goals_scored, assists, clean_sheets, bonus, total_points, was_substituted_in, settled_at):

| column | live payload key | note |
|---|---|---|
| starts | starts | 0/1 per fixture |
| saves | saves | |
| bps | bps | |
| expected_goals | expected_goals | = databank `xg` |
| expected_assists | expected_assists | = databank `xa` |
| expected_goals_conceded | expected_goals_conceded | = databank `xgc` |
| defensive_contribution | defensive_contribution | = databank `dc` |
| yellow_cards | yellow_cards | |
| red_cards | red_cards | |

### 4.2 Upsert (repository.upsert_player_gw_stats)

Writes the 9 new fields per fixture row. Schema assertion stays (B6). `was_substituted_in` column untouched (it is written but never consumed; its current value semantics are out of scope).

### 4.3 Auto-backfill (settlement.py)

After the normal `INSERT OR IGNORE` pass, settlement_run also:

1. Finds finished GWs where any `player_gw_stats` row has `starts IS NULL`:
   `SELECT DISTINCT gw FROM player_gw_stats WHERE starts IS NULL`.
2. Re-fetches `event/{id}/live` for those GWs and `UPDATE`s **only the 9 new columns** keyed on the existing PK `(player_id, gw, fixture_id)`.
3. Never touches existing columns on re-fetch (residuals/audit stability: settled `total_points` etc. stay frozen).
4. Logs the backfilled row count (structured log line, e.g. `settlement.backfill gw=1 rows=610`).

Runs inside the existing per-GW try/except — a failed backfill never blocks the refresh. The first refresh after deploy backfills GW1; the rate windows then include GW1 before the next recompute.

## 5. Row union (ratings.py)

### 5.1 `_rating_rows(conn)`

Returns rows in the same dict shape the current queries use (`source, gw, minutes, xg, xgc, dc, starts, xa, saves, yellow_cards, red_cards, player_id, team_id, position`), from **two** sources:

- databank: `player_stats` rows, excluding `source == f"fpl_databank:{current_season}"` (R9: live is authoritative in-season; prevents double-counting the same GWs from a hypothetical unstable databank).
- live: `player_gw_stats` rows aggregated **per (player_id, gw)** with a synthetic `source = f"fpl_live:{current_season}"`:
  - rows with `starts IS NULL` are excluded until backfilled (deterministic pre/post-backfill behavior; backfill normally runs before the next recompute anyway)
  - `starts = MAX(starts)` (started ≥1 fixture in the GW — matches FPL element-summary semantics)
  - all other stats `= SUM(...)` across the GW's fixtures (DGW-correct)
  - team_id/position joined from `players` (same join as today)

`current_season` derived from config `season` (start year → `"2026-27"` form, matching the databank convention).

### 5.2 Window ordering

`_window_keys` currently sorts by the `(source, gw)` string. Replace with a parsed key `(season_year, gw)` extracted from the source suffix (`fpl_databank:2025-26` → 2025, `fpl_live:2026-27` → 2026). LF = last 38 distinct pairs, SF = last 6. With 1 live GW: LF = 25-26 gw2..38 + 26-27 gw1; SF = 25-26 gw33..38 + 26-27 gw1. With ≥38 live GWs: windows are pure current-season. Pre-season: unchanged (25-26 only).

### 5.3 Consumers — no other changes

`compute_team_ratings`, `compute_player_rates`, league averages, and the promoted-override gate (`gw_count >= MIN_GWS_FOR_RATING` = 5) all read `_rating_rows`, so the promoted teams (COV/IPS/HUL) get real ratings once 5 live GWs exist, and everything adapts together.

## 6. New-signing guard (one new constant)

Players with **no prior databank rows** (new signings, rookies) have pure-live rates; a single GW can be extreme (1.5 xG debut → ~9 xP goal term). For those players only:

```
rate = w × rate_live + (1 − w) × position_avg       w = min(1, live_gws / MIN_LIVE_RATE_GWS)
MIN_LIVE_RATE_GWS = 3
```

- Applies to per-start and per-90 rates (xg, xa, dc hit rate, saves/90, yc/90, rc/90, p60).
- `position_avg` = pooled league rate for the position (Σ stat ÷ Σ denominator) over the same LF window rows.
- `p_start` is NOT shrunk (a GW1 starter gets 1.0 — acceptable; the defensive-captain penalty covers confidence in the early window).
- Players with prior databank rows are untouched — the natural window keeps them smooth.

## 7. v0.21 pre-season defensive-captain penalty — auto-off rule

Current detection ("no `fpl_databank:<current>` rows") is replaced: the penalty applies while the **SF window has fewer than 3 live (season, gw) pairs** (`SF_LIVE_MIN = 3`), i.e. until SF is majority-live. After GW1 backfill the penalty stays on (SF = 1/6 live); it flips off at 3 live GWs. Constants: `PRE_SEASON_DEF_PENALTY = 1.5` unchanged, new `SF_LIVE_MIN = 3`. Changelog entry per B4.

## 8. Simulated-season backtest

`docs/research/calibration/backtest.py` gains a blend-simulation mode (prior season and live season as arguments; default prior=24-25, live=25-26):

- For each live-season GW g: rates built from union(prior rows + live rows with gw < g) — **no leakage** (live GW g's own data never predicts GW g).
- Project xP v2 for GW g; compare vs actual `total_points` from the live-season databank (the "live" ground truth).
- Baseline: pure-prior (current production behavior) over the same GWs.
- Metrics per live-gw-count bucket (0, 1-2, 3-5, 6+): MAE, bias, corr, captain top-pick proxy (same shapes as the existing backtest output).
- The v0.21 penalty applies in production order (on while SF live pairs < 3).
- A fast frozen slice (first ~8 live GWs) runs in CI; the full 38-GW run is a manual invocation.

Expected behavior: blending should beat pure-prior as live evidence accumulates (buckets 3+), with a small early penalty from noisy 1-2 GW windows. The verdict is recorded in the backtest output and the changelog entry.

## 9. Docs

- `docs/decision-engine.md`: v2 section gets a data-sources paragraph (live rows enter the windows; current-season databank rows excluded; new-signing guard; penalty auto-off rule). Changelog entry **v0.23** (data-source change; no formula-structure change — stored `model_version` stays `v2`, matching the v0.20-22 pattern).
- `docs/risks.md`: R8 residual-risk note updated — new signings gain live rates (still noisy until ≥3 GWs).

## 10. Tests

- **settlement:** full-stats write; auto-backfill fills a `starts IS NULL` GW (UPDATE, no dupes, idempotent second run); backfill never overwrites existing columns; per-GW backfill failure doesn't block the refresh.
- **ratings union:** mixed-source window ordering (LF/SF boundaries with 25-26 databank + 26-27 live); current-season databank rows excluded; DGW aggregation (starts OR, sums); benched-starter p_start sanity (e.g. 37/38, not 0); new signing appears with live-only rates; new-signing shrink toward position avg at w=1/3; no NaN with a single live GW; team ratings + promoted gate count live GWs.
- **penalty:** on with SF live < 3 pairs, off at ≥3.
- **backtest slice:** frozen ~8-GW simulation runs and reports metrics.
- Full suite stays green (820 pytest + 77 vitest; frontend untouched).

## 11. Edge cases

- **DGWs:** per-fixture storage stays; aggregation at read time. `squads_made` counts (source, gw) pairs (one per GW per team) — consistent with existing databank behavior (DGW matches slightly undercounted, unchanged semantics).
- **New signings with 0 minutes GW1:** live row exists with all zeros → present but near-zero rates; p_start 0. Correct.
- **A player missing from a re-fetch payload:** UPDATE matches 0 rows; counted in the backfill log; retried next refresh (starts still NULL).
- **Re-fetch shows corrected stats:** existing columns frozen; only the 9 new ones written (audit stability).
- **Config `season` missing:** fall back to `databank.seasons[-1]`; failing that, treat live rows as unavailable (current behavior).

## 12. Out of scope

Bonus recalibration from 26-27 actuals (open item 6, ~6 GWs in), manager-regime modeling (item 7), `was_substituted_in` semantics cleanup, decision-layer or interface changes.

## 13. Definition of done (B14)

- Code implements this doc; `decision-engine.md` v0.23 entry written first.
- Tests above pass; full suite green.
- Backtest simulation run over the frozen slice (CI) and the full 38-GW 24-25→25-26 run locally, verdict recorded.
- Manual smoke: local refresh against the live DB → GW1 backfilled (610 rows), rates/xP recompute with live rows, `player_gw_stats` shows starts/xg/... populated.
- Backfill events appear in logs (B10-compatible structured lines).
