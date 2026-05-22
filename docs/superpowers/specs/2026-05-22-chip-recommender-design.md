# Chip Recommender + DGW/BGW — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** The last Decision-Layer engine (flag-only, Phase 1) + the DGW/BGW detection it needs. Independent of the in-flight captain/transfer engines.
- **Slice goal:** `recommend_chip(conn)` returns a single `/api/chips` recommendation (or `null`) by evaluating the four chip triggers; DGW/BGW detection is the reusable foundation.

Consumes Analytics output (`fdr`, `understat_players` rates) + `fixtures` + squad.

---

## 1. Context

Per `decision-engine.md` ("Chip recommender"), each chip has a trigger; Phase 1 flags only (no execution — and chips ALWAYS require user confirmation, `CLAUDE.md` B3/B8). Three rules need v1 adaptations driven by data/dependencies:

- **DGW-aware xP** (Bench Boost, Triple Captain): the stored `xp` table is one row per (player, GW) for a single fixture; a double gameweek needs the sum over *both* fixtures. The recommender computes this locally by reusing `analytics.xp.compute_player_xp` per fixture (no change to the stored xp model — that general DGW-xP work stays deferred).
- **Wildcard** depends on the transfer engine (in-flight) and on cross-manager data we don't have. v1 implements only the fixture-swing criterion; the other two are deferred/dropped (see §4).

End-of-season caveat (2026-05-22): no live DGW/BGW exists, so a live run returns `null`. Correctness is proven by deterministic tests on crafted fixtures.

## 2. Decisions locked

| Decision | Choice |
|---|---|
| DGW-aware xP | reuse `compute_player_xp` per fixture, sum over the team's GW fixtures (no stored-xp change) |
| Wildcard v1 | fixture-swing criterion only; defer "≥4 sell candidates" (transfer engine, in-flight); drop "value vs team average" (cross-manager data unavailable) |
| Priority (single recommendation) | Triple Captain → Bench Boost → Free Hit → Wildcard |
| chips_used suppression | best-effort from `my_team.chips_used_json` (the full history needs the auth entry-history endpoint — Phase 2) |
| Output | flag only; `/api/chips` shape; chips never auto-execute (B3/B8) |

## 3. Components

### `src/analytics/dgw.py` (detection foundation)
- `team_gw_fixtures(conn, team_id, gw) -> list[dict]` — the team's fixtures that GW, each `{opponent_id, home, fdr_attack, fdr_defense}` (join `fixtures` + `fdr`). Length 0 (blank), 1 (single), or 2 (double).
- `squad_coverage(conn, team_ids_by_player, gw) -> int` — count of squad players whose team has ≥1 fixture that GW.

### `src/decisions/chips.py`
- `_player_gw_xp(player_row, gw_fixtures) -> float` — sum of `compute_player_xp(...)["xp"]` over each fixture's FDR (player_row carries position/status/xg_per_90/xa_per_90/minutes/games). 0 if no fixtures.
- Pure trigger helpers (each takes prepared squad/fixture data, returns a reason string or `None`):
  - `free_hit_trigger`: a GW in the horizon where `squad_coverage < 8` → "Blank GW{n}: only X of 15 have fixtures."
  - `bench_boost_trigger`: a GW where all 15 squad players have ≥1 fixture AND the 4 bench players' (pick positions 12–15) combined `_player_gw_xp > 4` → reason with the bench xP.
  - `triple_captain_trigger`: a squad player with `price ≥ 9.5`, a DGW (2 fixtures that GW), both `fdr_attack ≤ 2`, `_player_gw_xp ≥ 12` → reason naming the player + DGW xP.
  - `wildcard_trigger`: ≥3 squad players whose `fdr_attack` worsens by ≥2 from the next GW to GW+3 (from `fdr`) → reason listing the count.
- `recommend_chip(conn, horizon=6) -> dict` — loads squad (latest `my_team` picks) + `chips_used_json`, loads squad players' rates (`players`⋈`understat_players`), evaluates the four triggers across the horizon in priority order, returns `{"recommendation": {"chip": <name>, "reason": <str>}}` for the first triggered chip not already used, else `{"recommendation": None}`. Chip names: `triple_captain | bench_boost | free_hit | wildcard`.

### `/api/chips` wiring
Replace the `src/interface/api.py` chips stub with `return chips.recommend_chip(conn)` (this slice owns `api.py`; the captain/transfers stubs stay until their engines merge).

## 4. decision-engine.md update (B4)
In the "Chip recommender" section, add a v1 note: Wildcard v1 = fixture-swing criterion only (≥4-sell-candidates deferred to the transfer engine; value-vs-team-average dropped — cross-manager data unavailable); DGW-aware xP computed by summing `compute_player_xp` over a team's two GW fixtures; single-recommendation priority TC→BB→FH→WC; chips_used suppression best-effort. Changelog: `v0.6 | 2026-05-22 | Chip recommender v1: DGW/BGW detection; Wildcard fixture-swing only (others deferred/dropped); DGW-xP via per-fixture sum; priority TC>BB>FH>WC.`

## 5. Scope

### In scope
- `src/analytics/dgw.py`, `src/decisions/chips.py`, `/api/chips` wiring, `decision-engine.md` v1 note + changelog, tests.

### Out of scope (deferred)
- Wildcard sell-candidate criterion (until the transfer engine merges) and value-vs-average (cross-manager data).
- Full chips_used history (auth entry-history endpoint — Phase 2).
- Chip *execution* (always user-confirmed, never automated — B3/B8).
- Extending the stored `xp` model for DGW (the recommender does its own local DGW-xP).

## 6. Testing (B11)
- `test_team_gw_fixtures_double/single/blank`: crafted fixtures → 2/1/0 entries with FDR.
- `test_free_hit_triggers_on_blank`: a GW where <8 squad have fixtures → free_hit reason; else None.
- `test_bench_boost_triggers_on_dgw`: all 15 with fixtures + bench combined DGW-xP >4 → bench_boost; below threshold → None.
- `test_triple_captain_triggers`: a ≥9.5 player with a DGW, both fdr_attack≤2, DGW-xP≥12 → triple_captain; fails any condition → None.
- `test_wildcard_fixture_swing`: ≥3 squad players' fdr_attack worsens ≥2 over 3 GW → wildcard; <3 → None.
- `test_priority_and_chips_used`: when multiple trigger, TC wins; an already-used chip (in chips_used_json) is skipped to the next.
- `test_recommend_chip_none_when_nothing_triggers`: a normal single-GW slate → `{"recommendation": None}`.
- `test_api_chips_wired`: the `/api/chips` endpoint returns `recommend_chip`'s output (TestClient + seeded DB).

## 7. Definition of done
1. `pytest` green incl. chip tests.
2. `recommend_chip(conn)` returns a valid `/api/chips` payload against the live DB (likely `{"recommendation": null}` at season end — correct per §1).
3. `/api/chips` endpoint wired to the recommender.
4. `decision-engine.md` v1 note + changelog (B4).

## 8. Notes
- Flag-only; chips never auto-execute (B3/B8) — even Phase 2/deadguard forbid it.
- Re-add the Wildcard sell-candidate criterion once `feat/transfer-engine` merges (a small follow-up).
