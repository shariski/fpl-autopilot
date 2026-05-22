# Understat Season-Aggregate Ingestion — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** The deferred half of Phase 1.1 — Understat supplementary data (season-aggregate xG/xA), FPL↔Understat name resolution, persistence.
- **Slice goal:** `fpl-autopilot refresh` fetches Understat season xG/xA via its JSON endpoint, conservatively resolves each Understat player to an FPL player id, and persists into a new `understat_players` table — failing *gracefully* (R2) and reporting the match rate.

Builds directly on the merged Data Layer foundation (`docs/superpowers/specs/2026-05-22-data-layer-foundation-design.md`).

---

## 1. Context

The xP model (`docs/decision-engine.md`) is built on `xG per 90` and `xA per 90` — predictive chance-quality signals the **official FPL API does not publish**. Understat is the source. This slice ingests that data into the Data Layer so the (later) Analytics slice can compute xP.

Constraints inherited from the docs:
- **`risks.md` R2** — Understat is scraped/unofficial and can break without notice. Fail gracefully: skip the refresh, use last known data, log staleness.
- **`architecture.md` failure handling** — "Understat/FBref scraping fails: skip the refresh, use last known data, log staleness."
- **`CLAUDE.md` B6** — single client module with retry/backoff/schema-assertions, ≤1 req/s, realistic User-Agent.
- **`CLAUDE.md` B13** — a new table is documented in `architecture.md` in the same change.

### Discovery (verified during brainstorming, 2026-05-22)

Understat **no longer embeds** `playersData = JSON.parse(...)` in page HTML (the league page is an 18KB shell; the `understat` pip library's scraping approach is likely broken). Instead, data is available via a JSON endpoint:

- `POST https://understat.com/main/getPlayersStats/` with form body `league=EPL&season=2025` (season uses the start year; `2025` = the 2025/26 season).
- Returns gzip-compressed `{"success": true, "players": [ ... ]}` — **533 players**, **season aggregate**.
- Per-player fields: `id, player_name, team_title, games, time, goals, assists, xG, xA, npg, npxG, key_passes, shots, xGChain, xGBuildup, position, yellow_cards, red_cards`. Numbers are JSON **strings** (e.g. `"28.79"`), coerced like the FPL fields.

This is cleaner than HTML scraping (a real JSON API). The endpoint is **season-aggregate only**; per-GW xG/xA would require ~533 per-player requests and is out of scope (see §3).

## 2. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Granularity | **Season-aggregate** (1 request) | Fully feeds xP v1 (xG/90, xA/90); robust vs R2. Per-GW deferred (533 requests, ~9 min/refresh). |
| Data source | Understat `POST /main/getPlayersStats/` JSON endpoint | Verified working; embedded-JSON scraping is dead; cleaner than HTML. |
| Team matching | **Explicit overrides + normalization**, not pure fuzzy | Understat `"Tottenham"` ≠ FPL `"Spurs"`, etc. — fuzzy is unsafe. |
| Player matching | Conservative: **unambiguous single match within resolved team only** | A *wrong* match silently corrupts xP; an *unmatched* player degrades safely to FPL-only. |
| Storage | New table `understat_players` | Season aggregate doesn't fit the per-GW `player_stats` schema. |
| Failure mode | **Graceful degradation** in the refresh orchestration | R2 / architecture.md: Understat failure must NOT break the FPL refresh. |

## 3. Scope

### In scope
- `src/data/understat_client.py` — hardened client for the getPlayersStats endpoint.
- `src/data/models.py` additions — `UnderstatPlayer`, `UnderstatPlayersResponse`.
- `src/data/name_resolver.py` — FPL↔Understat team + player resolution (conservative).
- New `understat_players` table in `schema.sql`; documented in `architecture.md` (B13).
- `src/data/repository.py` addition — `upsert_understat_players(...)` incl. derived per-90 + resolved `fpl_player_id`.
- `data/name_resolution.yaml` support — manual `understat_id → fpl_id` overrides (per `onboarding.md`).
- Cache: add `understat` resource (6h TTL).
- `src/cli.py`: integrate Understat into `refresh`/`refresh --full`; add `--source {fpl,understat}` filter (referenced in `runbook.md` §6); **graceful degradation** wrapper; print `matched X/N`.
- Tests: frozen Understat fixture, model+drift tests, name-resolver tests, client retry tests, repository test, CLI graceful-degradation test.

### Out of scope (deferred)
- **Per-GW xG/xA** (the ~533-request per-player path) — until rolling-form metrics need it.
- **R2 freshness-confidence downgrade** (per-player staleness → confidence penalty) — belongs with Analytics confidence scoring; `updated_at` is stored now to enable it later.
- **FBref backup source** — until Understat proves unreliable in practice.
- Analytics/xP computation (a later slice consumes this data).

## 4. File layout

```
src/data/
  understat_client.py     # NEW: UnderstatClient.players_stats(season)
  name_resolver.py        # NEW: resolve_players(fpl_players, fpl_teams, understat_players, overrides)
  models.py               # +UnderstatPlayer, +UnderstatPlayersResponse
  schema.sql              # +understat_players table
  repository.py           # +upsert_understat_players(...)
  cache.py                # +"understat" TTL
src/cli.py                # refresh integrates understat + --source filter + graceful degradation
data/name_resolution.yaml # NEW (gitignored? NO — it's user config, commit a template/empty)
docs/architecture.md      # +understat_players table (B13)
tests/
  fixtures/understat-players.json   # NEW frozen endpoint response
  test_understat_models.py  test_name_resolver.py
  test_understat_client.py  test_understat_repository.py
  (test_cli_refresh.py extended for understat + degradation)
```

`data/name_resolution.yaml`: committed as an empty/commented template (it is user-editable config, not secret). The DB and logs stay gitignored as before.

## 5. Component specs

### 5.1 `understat_client.py`
`UnderstatClient` mirroring the `FPLClient` hardening (retry/backoff `1,5,30`s, ≤1 req/s, realistic UA, timeout; `requests` handles gzip via `Accept-Encoding`). Injected `session`/`sleep`/`monotonic` for tests.
- `players_stats(season="2025") -> UnderstatPlayersResponse`: `POST {BASE}/main/getPlayersStats/`, form `{"league": "EPL", "season": season}`, header `X-Requested-With: XMLHttpRequest`. Parse JSON through `UnderstatPlayersResponse`.
- `BASE_URL = "https://understat.com"`.

### 5.2 `models.py` additions
```
class UnderstatPlayer(_Base):
    id: str
    player_name: str
    team_title: str
    games: int
    time: int            # minutes
    goals: int
    assists: int
    xG: float
    xA: float
    npg: int
    npxG: float

class UnderstatPlayersResponse(_Base):
    success: bool
    players: list[UnderstatPlayer]
```
(Other endpoint fields — key_passes, shots, xGChain, etc. — ignored via `extra="ignore"`; not consumed.)

### 5.3 `name_resolver.py`
Pure functions (no I/O), so they're deterministically testable.

**Normalization** `_norm(s)`: lowercase, strip accents (`unicodedata` NFKD), remove punctuation, collapse whitespace.

**Team resolution** `resolve_teams(fpl_teams, understat_team_titles) -> {understat_title: fpl_team_id}`:
- For each Understat `team_title`: try `_norm` match against FPL `teams.name` and `teams.short_name`; then apply `UNDERSTAT_TEAM_OVERRIDES` (a module dict for divergent names — seed with the known-divergent ones, e.g. `"Tottenham"→"Spurs"`, `"Wolverhampton Wanderers"→"Wolves"`, `"Nottingham Forest"→"Nott'm Forest"`, `"Manchester City"→"Man City"`, `"Manchester United"→"Man Utd"`, `"Newcastle United"→"Newcastle"`; the implementer completes/verifies it by running against the real FPL teams + Understat fixture).
- **Any Understat team that maps to no FPL team is returned as an `unmapped_teams` list** — surfaced loudly (an unmapped team means ALL its players go unmatched, a large silent gap).

**Player resolution** `resolve_players(fpl_players, team_map, understat_players, overrides) -> ResolutionResult`:
- Apply manual `overrides` (`understat_id → fpl_id`) first — authoritative.
- Else: scope to the FPL players on the resolved team; `_norm`-match the Understat `player_name` against each FPL player's full name (`name`) and `web_name`. **Accept only if exactly one FPL player matches** (ambiguous or zero → unmatched).
- Return `ResolutionResult(matched: dict[understat_id, fpl_id], unmatched: list[understat_player], unmapped_teams: list[str])`.

### 5.4 `understat_players` table (schema.sql + architecture.md)
```sql
CREATE TABLE IF NOT EXISTS understat_players (
  understat_id TEXT PRIMARY KEY,
  fpl_player_id INTEGER,        -- resolved FPL id; NULL if unmatched
  season TEXT,
  player_name TEXT,
  team_title TEXT,
  games INTEGER,
  minutes INTEGER,
  goals INTEGER,
  assists INTEGER,
  xg REAL,
  xa REAL,
  npg INTEGER,
  npxg REAL,
  xg_per_90 REAL,              -- xg / (minutes/90), 0 if minutes==0
  xa_per_90 REAL,
  updated_at TIMESTAMP
);
```
A one-paragraph + table entry added to `docs/architecture.md` per B13.

### 5.5 `repository.py` addition
`upsert_understat_players(conn, understat_players, resolution, season)`: upsert each player, setting `fpl_player_id` from `resolution.matched.get(up.id)`, computing `xg_per_90`/`xa_per_90` (guard minutes==0 → 0.0), `updated_at = now`.

### 5.6 cache + CLI
- `cache.DEFAULT_TTL["understat"] = timedelta(hours=6)`.
- `cli.refresh`: add a `sources` parameter (default both). When understat is in scope and (`full` or stale): fetch via `UnderstatClient`, load overrides from `data/name_resolution.yaml` (if present), resolve against the `players`+`teams` already in the DB, upsert, `mark_fetched("understat")`, print `understat OK (matched X/N players, U unmatched, T unmapped teams)`.
- **Graceful degradation:** wrap the entire Understat step in `try/except Exception`; on failure, print a `WARNING: understat refresh failed (...); keeping last data` and continue — the FPL refresh and overall command still succeed. (FPL parsing remains loud; only the supplementary Understat step degrades.)
- `main`: add `--source {fpl,understat}` to `refresh` — a single optional value; omitted = both sources.

## 6. Testing strategy (B11)
- `test_understat_models.py`: parse frozen `understat-players.json` (success True, 500+ players, Haaland present with xG>0); drift test (mutate `xG` to non-numeric → `ValidationError`).
- `test_name_resolver.py` (deterministic, uses frozen FPL bootstrap + Understat fixtures): a known player resolves (Haaland understat→FPL Haaland); a divergent team maps (`Manchester City`→`Man City`); an injected ambiguous duplicate is left unmatched; a manual override forces a mapping; `unmapped_teams` empty for the real data; reported match count is sane (e.g. ≥ 90% of Understat players matched).
- `test_understat_client.py`: fake-session POST returns the fixture → parsed model; retry on 5xx/connection error (reuse the FPL client's fake-session pattern); correct endpoint + form body + UA.
- `test_understat_repository.py`: upsert maps `fpl_player_id` from resolution, computes per-90 correctly, minutes==0 → 0.0, idempotent.
- Extend `test_cli_refresh.py`: refresh populates `understat_players` with a fake Understat client; **graceful-degradation test** — a fake client that raises makes refresh still complete with FPL data intact and understat data untouched.

## 7. Definition of done
1. `pytest` green incl. the Understat drift loud-failure test.
2. `fpl-autopilot refresh --full` populates `understat_players` (500+ rows) with a high match rate (target ≥ 90% of Understat players resolved to FPL ids), printing the match summary, against the live endpoint.
3. A simulated Understat failure leaves the FPL refresh succeeding (graceful degradation), verified by test.
4. `architecture.md` updated for `understat_players`; `name_resolution.yaml` template committed.

## 8. Risks specific to this slice
- **R2 (scraping fragility):** mitigated by graceful degradation + the fact this is now a JSON endpoint, not HTML scraping. If the endpoint shape changes, the Pydantic model fails loud *inside the client*, the orchestration catches it, and FPL refresh survives.
- **Wrong-match corruption:** mitigated by conservative unambiguous-only matching + explicit team overrides + manual yaml. Unmatched is safe (degrades to FPL-only); wrong-match is the thing we refuse to risk.
- **Season rollover (D5):** team overrides and `season` are season-specific; revisit at rollover.
