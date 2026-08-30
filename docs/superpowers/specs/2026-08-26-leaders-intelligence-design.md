# Leaders Intelligence (Top-100 Cohort Analytics) — Design

**Date:** 2026-08-26 · **Status:** approved in brainstorming · **Scope:** data layer + analytics + interface (read-only; B4 untouched — nothing feeds the decision engine in this slice)

## 1. Problem & context

The user wants to learn from elite FPL players: what strategies they use, when they play chips, how they manage transfers/hits/bank — then decide later what products to build from the patterns. Verified data availability (2026-08-26):

- **Current season** (public API): global classic league standings (paginated, top-100 via 2 pages) + per-entry per-GW history: points, overall_rank, bank, value, event_transfers, event_transfers_cost (hits), chips played (name + event). Live example: the current #1 played TC in GW2.
- **Previous season**: only aggregate summaries per entry (`past`: season_name, total_points, rank) — no per-GW chip timing. Used to identify "sustained elite" (current top-100 with strong 25-26 ranks), never for per-GW patterns.

## 2. Decisions (approved)

1. **Cohort**: global classic league (id 314), top 100 (standings pages 1-2).
2. **Cadence**: per-GW, ASAP — the hourly refresh snapshots whenever a finished GW has no snapshot yet (settlement-style trigger); ~102 requests ≈ 2 min once per GW. On-demand `leaders --refresh`.
3. **Flow**: gather → analyze → present for the user to learn → **products decided later** (chip-recommender priors are a separate B4-documented follow-up).
4. **Presentation**: a **Leaders dashboard page** with tables + charts (dependency-free SVG — no new npm deps), desktop-first; plus `leaders --json` CLI with the same data shape.
5. **No AI** in the analysis: all patterns are deterministic, inspectable statistics.

## 3. Data model

`leader_entries` (per cohort member, upserted on sight):

| column | type | notes |
|---|---|---|
| entry_id | INTEGER PK | |
| player_name | TEXT | |
| entry_name | TEXT | |
| past_season_rank | INTEGER NULL | 25-26 final rank from `history.past[0]` (sustained-elite filter) |
| past_season_pts | INTEGER NULL | |
| first_seen_gw | INTEGER | |
| last_rank | INTEGER | current overall rank |
| last_total | INTEGER | current total points |
| updated_at | TIMESTAMP | |

`leader_gw_snapshots` (per entry per GW, PK (entry_id, gw)):

| column | type |
|---|---|
| entry_id, gw | PK |
| points, total_points, overall_rank | INTEGER |
| bank, value | INTEGER (tenths of £m, raw API scale) |
| event_transfers, hit_cost | INTEGER |
| chip_played | TEXT NULL | 'wildcard'\|'free_hit'\|'bench_boost'\|'3xc' |

(Chipped GWs are also captured in `gameweeks`-style derived data; the snapshot is the source of truth.)

## 4. Fetch flow

- `FPLClient.leagues_classic(league_id, page)` → standings (schema-asserted: standings.results[].entry/player_name/entry_name/total/rank; B6).
- `FPLClient.entry_history(entry_id)` → `{current: [...], past: [...]}` (schema-asserted).
- Cache keys: `leaders:standings:{league}` (per-day TTL via `last_updated_data`) and `leaders:entry:{id}` (per-GW TTL). Client sleep/backoff (1 req/s) applies.
- **Scheduler trigger** (inside the hourly refresh, after settlement): `SELECT id FROM gameweeks WHERE finished=1 AND id NOT IN (SELECT DISTINCT gw FROM leader_gw_snapshots)` → fetch standings (2 pages) → for each entry fetch history → upsert entries + snapshots (extract the settled GW's row + chip). Failures swallowed + logged (never break the refresh). Skip when the DB has no team/season yet (pre-bootstrap).
- `fpl-autopilot leaders --refresh` runs the same path on demand.

## 5. Analysis (pure, deterministic — src/analytics/leaders.py)

All functions take `conn` and return plain dicts (no AI, B10-inspectable):

- `chip_timing(conn)` → per (chip, gw): count of leaders who played it; ordered rows for the heatmap; plus first-chip distribution.
- `transfer_discipline(conn)` → mean/median transfers per GW, hit frequency (share of leader-GWs with hit_cost > 0), mean hit cost, transfers histogram (count of leader-GWs per transfer count).
- `bank_value(conn)` → per-GW mean/median bank and value (trajectories for the line chart).
- `rank_momentum(conn)` → per-entry rank series (top movers: biggest rank gain between consecutive snapshots); sustained-elite = entries with `past_season_rank` ≤ 250,000 (top ~5% of 25-26, ~12M entries).
- `cohort_stats(conn)` → the cohort table rows (joined, latest snapshot).

Guard: functions return empty structures when no snapshots exist (pre-season page renders an empty state).

## 6. API

`GET /api/leaders` → single payload:

```json
{"cohort": [{"entry_id", "player_name", "entry_name", "rank", "total", "last_gw_points",
             "transfers", "hit_cost", "bank", "value", "chips_used": ["3xc"], "past_rank"}],
 "patterns": {
   "chip_timing": {"rows": [{"gw": 2, "chip": "3xc", "count": 23}], "first_chip": {"3xc": {"gw": 2, "count": 23}}},
   "transfers": {"mean_per_gw": 1.1, "median_per_gw": 1.0, "hit_freq": 0.08, "mean_hit_cost": 2.1,
                 "histogram": [{"transfers": 0, "count": 410}]},
   "bank_value": {"bank": [{"gw": 1, "mean": 3.2, "median": 2.5}], "value": [{"gw": 1, "mean": 1005.0, "median": 1004.0}]},
   "momentum": {"top_movers": [{"entry_id", "player_name", "from_gw", "to_gw", "rank_gain"}],
                "sustained_elite": [entry_ids]}
 }}
```

## 7. CLI

`fpl-autopilot leaders [--refresh] [--json]` — `--refresh` re-fetches (on-demand snapshot); without it, reads the stored data. Pretty mode prints the cohort table + pattern summaries; `--json` returns the API payload shape. Agent-safe (read-only; refresh writes the local DB only, never FPL).

## 8. Dashboard page — `/leaders` (desktop-first)

Dependency-free SVG charts (a small `Chart.svelte` toolkit: bars, lines, heatmap grid), matching the dark design system (--surface cards, --accent, --radius):

1. **Cohort table** — rank, player, team name, total, last GW pts, transfers, bank, value, chips used, past-rank badge ("elite" marker).
2. **Chip timing heatmap** — chips × GWs grid, cell intensity = count of leaders (tooltip on hover).
3. **Transfer discipline** — histogram bars (leader-GWs by transfer count) + mean/median/hit-frequency stat chips.
4. **Bank & value lines** — mean/median over GWs.
5. **Rank momentum** — top movers table + sustained-elite summary.

Empty state when no snapshots yet ("first snapshot lands after the next GW settles"). Page + component tests with fixture data (vitest).

## 9. Tests

- FPLClient: leagues_classic + entry_history schema assertions (drift fails loudly, B6).
- Repository: entries/snapshots upsert round-trip, chip extraction, idempotency (re-run same GW → no dupes).
- Scheduler trigger: fires once per settled GW; failures isolated.
- Analysis: pure-function tests with frozen fixture snapshots (chip cluster detection, hit frequency math, sustained-elite filter, empty-DB guards).
- API: GET /api/leaders shape with seeded snapshots.
- CLI: `leaders --json` envelope; `--refresh` calls the fetch path (monkeypatched).
- Frontend: charts render with fixture payloads (heatmap cells, bars, lines); empty state.

## 10. Docs

- `docs/agent-contract.md`: `leaders` command (agent-safe list + shape).
- `docs/runbook.md`: one-liner.
- `docs/architecture.md`: no structural change (new analytics module + read endpoint).

## 11. Out of scope (this slice)

Chip-recommender priors from leader patterns (B4-documented follow-up after the user studies the insights); mobile layout optimization (desktop-first, enhance later); previous-season per-GW data (unavailable in the API).

## 12. Definition of done (B14)

- Code implements this doc; tests pass; full suite green (pytest + vitest).
- Manual smoke: `leaders --refresh` on the local DB (live API), then `leaders --json` shows cohort + patterns; dashboard `/leaders` renders tables + charts.
- Snapshot lands on jumbo within one hourly cycle after a GW settles; second run is a no-op (idempotent).
