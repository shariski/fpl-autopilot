# FDR v1 (FPL-strength) — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** First half of the Analytics layer — a custom 1–5 Fixture Difficulty Rating per team per upcoming GW, attack/defense split, computed from FPL team strength ratings. (xP v1 is the next slice.)
- **Slice goal:** `fdr` table is populated with sensible 1–5 attack/defense difficulty for upcoming fixtures, derived deterministically from data already in the DB.

Builds on the merged Data Layer (FPL + Understat). First code in the Analytics layer.

---

## 1. Context

`decision-engine.md` specifies a *custom* FDR (better than FPL's noisy rank-based one) defined as a function of **team xG conceded/scored per game (last 5 GW)**. During brainstorming we confirmed that data is **not cleanly obtainable**: Understat exposes only per-player season aggregates now (`getTeamsStats` 404s; team pages are empty shells; embedded JSON is dead site-wide). Per-match team xG (especially xG *conceded*) is unavailable without a new, fragile scraping source.

**Decision:** FDR v1 uses **FPL's own team strength ratings** — `strength_attack_home/away`, `strength_defence_home/away`, already in the `teams` table (model-based ~1000–1400 numbers). This needs no new network source (no R2 risk), unblocks the Analytics→Decision pipeline now, and is versioned: xG-based FDR is the future v2. Per `CLAUDE.md` B4 (the decision engine is sacred), `decision-engine.md` is updated to define FDR v1 **before** implementing.

The "custom FDR beats FPL's" rationale still holds: FPL's *FDR* is a rank-based 1–5; FPL's *strength* ratings are finer-grained model outputs. FDR v1 derives a fresh attack/defense-split 1–5 from those strength numbers.

## 2. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Analytics decomposition | FDR v1 now; **xP v1 next** (separate slice) | xP consumes FDR; splitting keeps each spec/plan and decision-engine.md change reviewable. |
| FDR data source | FPL `teams` strength ratings | Available, reliable, no scraping (vs. unavailable team xG). |
| 1–5 mapping | **Quintile buckets** (4 teams each) | Balanced, planner-friendly grid; avoids clustering at 3. |
| Venue handling | Use FPL's home/away strength columns directly | Home/away advantage is already baked in; no extra ±0.3 factor (that was for the xG formula). |
| Governance | Update `decision-engine.md` with an FDR v1 section first (B4) | Decision logic is versioned and documented before code. |

## 3. Scope

### In scope
- `src/analytics/` package (new — the layer above Data). `src/analytics/fdr.py`.
- Pure FDR computation: quintile bucketer + per-fixture attack/defense rating.
- `repository.upsert_fdr(...)` to persist into the existing `fdr` table.
- An orchestrator that reads `teams` + upcoming `fixtures` from the DB and writes `fdr`.
- `decision-engine.md` update: FDR v1 (FPL-strength) section + changelog row (B4).
- Deterministic tests (pure computation against frozen inputs + an integration test that lands rows).

### Out of scope (deferred)
- **xP v1** — the next Analytics slice (consumes this FDR).
- **xG-based FDR (v2)** — needs team per-match xG ingestion, which isn't available.
- **Rolling "last 5 GW" weighting** — FPL strength is a single current rating, not per-GW; intrinsic to v1.
- CLI wiring of an analytics step / scheduler — FDR is invoked by the (future) refresh/analytics job; this slice exposes a callable `compute_and_store(conn)` and tests it. (Hooking it into the `refresh` CLI can come with xP, when there's a full analytics pass to run.)

## 4. FDR v1 formula (precise)

For each upcoming fixture `(gw, home_team H, away_team A)`, rate each team from the **opponent's** venue-specific strength:

- `fdr_attack[H, gw]` = `quintile_bucket(A.strength_defence_away, all_teams.strength_defence_away)`
- `fdr_defense[H, gw]` = `quintile_bucket(A.strength_attack_away, all_teams.strength_attack_away)`
- `fdr_attack[A, gw]` = `quintile_bucket(H.strength_defence_home, all_teams.strength_defence_home)`
- `fdr_defense[A, gw]` = `quintile_bucket(H.strength_attack_home, all_teams.strength_attack_home)`

Higher opponent strength → higher FDR (1 = easiest, 5 = hardest). `fdr_attack` keys off the opponent's **defense** (how hard to score); `fdr_defense` keys off the opponent's **attack** (how hard to keep a clean sheet).

**`quintile_bucket(value, distribution)`** — deterministic, ties to the lower bucket:
```
below = count of distribution values strictly < value
bucket = min(below * 5 // len(distribution) + 1, 5)
```
For the 20-team PL this yields buckets of 4 (below 0–3→1, 4–7→2, 8–11→3, 12–15→4, 16–19→5).

**Horizon:** the next 6 GWs starting from the first unfinished gameweek (`MIN(id) FROM gameweeks WHERE finished=0`). If none are unfinished (season over), no rows are written — expected at 2026-05-22.

## 5. decision-engine.md update (B4 — do first)

In the `## Fixture Difficulty Rating (custom)` section, introduce versioning. Keep the existing xG-based description but relabel it as the target v2, and add the v1 definition:

- Add subsection **`### v1 (current) — FPL-strength quintile`** with the §4 formula (opponent venue-specific strength → quintile 1–5; attack keys off opponent defense, defense off opponent attack; venue intrinsic via FPL's home/away columns).
- Relabel the existing xG-based text **`### v2 (target) — xG-based`**, noting it is blocked on team per-match xG ingestion (unavailable from Understat as of 2026-05-22).
- Add a changelog row: `v0.2 | 2026-05-22 | FDR versioned: v1 = FPL-strength quintile (implemented); v2 = xG-based (deferred, data unavailable).`

## 6. File layout

```
src/analytics/
  __init__.py            # NEW package
  fdr.py                 # NEW: quintile_bucket, compute_fdr, compute_and_store
src/data/repository.py   # +upsert_fdr
docs/decision-engine.md  # FDR v1 section + changelog (B4)
tests/
  test_fdr.py            # pure computation + integration
```

Architecture boundary (B2): Analytics reads only from the DB (the Data Layer's store) and never makes network calls. `fdr.py` reads `teams`/`fixtures`/`gameweeks` rows and writes via `repository.upsert_fdr`.

## 7. Components

### `src/analytics/fdr.py`
- `quintile_bucket(value, distribution) -> int` — per §4.
- `compute_fdr(teams, fixtures) -> list[dict]` — **pure**. `teams` = list of team dicts (id + four strength columns), `fixtures` = list of fixture dicts (gw, home_team_id, away_team_id). Returns fdr rows `{team_id, gw, fdr_attack, fdr_defense}` (two per fixture). No I/O — fully testable.
- `compute_and_store(conn, horizon=6) -> int` — reads all teams, finds the first unfinished GW, selects fixtures in `[next_gw, next_gw+horizon-1]`, calls `compute_fdr`, persists via `repository.upsert_fdr` with `computed_at`, returns the row count.

### `repository.upsert_fdr(conn, rows)`
Upsert into `fdr (team_id, gw, fdr_attack, fdr_defense, computed_at)` with `ON CONFLICT(team_id, gw)`.

## 8. Testing (B11)
- `test_quintile_bucket`: known distribution → correct 1–5 boundaries (min→1, max→5, ties→lower bucket).
- `test_compute_fdr_strong_defense_is_hard`: a frozen 4–6 team set + one fixture → the team facing the strongest-defense opponent gets `fdr_attack == 5`; facing the weakest gets `1`.
- `test_compute_fdr_uses_correct_venue_columns`: home team rated off opponent's *away* strength columns, away team off opponent's *home* columns (assert via asymmetric strengths).
- `test_compute_fdr_two_rows_per_fixture`: each fixture yields exactly one row per team.
- `test_compute_and_store_persists_for_horizon` (integration, in-memory DB): seed `teams` (from frozen bootstrap), `fixtures` + `gameweeks` with an unfinished GW, run `compute_and_store`, assert `fdr` rows exist for the horizon GWs and values are in 1–5.

## 9. Definition of done
1. `pytest` green incl. the FDR tests.
2. `compute_and_store` populates the `fdr` table for the upcoming horizon when run against the live DB (or correctly writes 0 rows at season end), with all values in 1–5 and a balanced spread.
3. `decision-engine.md` updated with the FDR v1 section + changelog (B4), committed before/with the implementation.

## 10. Notes / caveats
- **End-of-season (2026-05-22):** likely 0 upcoming fixtures, so a live run may write 0 rows — correctness is proven by the deterministic tests; the table populates next season.
- **v1 is a single current rating, not rolling form** — acceptable for v1; xG-based rolling FDR is v2 when team xG is available.
