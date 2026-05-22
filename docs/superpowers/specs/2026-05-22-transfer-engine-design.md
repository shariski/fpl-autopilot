# Transfer Engine — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** Second Decision-Layer engine. Suggest-only (Phase 1, read/derive). Proposes the top transfers.
- **Slice goal:** return the top-3 sell→buy suggestions with EP delta and hit cost, in the `/api/transfers` shape, with property-tested squad-validity invariants.

Consumes Analytics output (`xp`) + squad/prices. Built in parallel with the captain ranker; both are independent.

---

## 1. Context

Per `decision-engine.md` ("Transfer engine"): identify sell candidates, find buy candidates per sell, compute EP delta over a horizon, apply a hit calculator, return the top 3. The horizon metric is `xP_5gw` = a player's summed xP over the next 5 GWs (from the `xp` table).

Data available (Decision Layer reads Analytics output + squad, never raw FPL — B2): `xp` (per player/GW, `model_version='v1'`), `players` (position, team_id, price, status, web_name), `my_team` (latest picks + `bank`), `teams`.

**Three v1 adaptations forced by available data (B4 — documented in `decision-engine.md`):**
1. **Drop `form_adjusted_delta` from sell criteria.** It needs per-GW *actual* points (regression detection); we have no per-GW actuals (deferred with xP v1). v1 sell candidate = `xP_5gw` below the position median **OR** non-clear status.
2. **Selling price = current `price`.** True selling price is auth-only (Phase 2, the documented `my_team` limitation). v1 budget uses current price as the sell value.
3. **Free transfers assumed = 1.** `free_transfers` is auth-only/NULL. The hit calculator assumes 1 free transfer; a single suggested transfer is therefore free (hit 0). Multi-transfer/known-FT hit logic (−4/−8) is implemented as a tested helper but the v1 suggestion path uses the single-free-transfer assumption.

## 2. Decisions locked

| Decision | Choice |
|---|---|
| Horizon metric | `xP_5gw` = sum of next-5-GW `xp` per player (model v1) |
| Sell candidates | `xP_5gw < median(xP_5gw of same position)` OR `status != 'a'` |
| Buy candidates | same position; `status == 'a'`; `price ≤ sell.price + bank`; 3-per-club valid after swap; ranked by `xP_5gw` desc |
| EP delta | `buy.xP_5gw − sell.xP_5gw`; only positive |
| Hit calc | helper `hit_cost(num_transfers, free_transfers=1)`; v1 single suggestion → 0. Worthiness thresholds: free→delta>0, −4→delta>4, −8→delta>8 |
| Output | top 3 sell→buy pairs by EP delta (regardless of hit), else `empty_reason` |
| Persistence | none — read/derive feeding `/api/transfers` |

## 3. Scope

### In scope
- `src/decisions/transfers.py` **only**. The `src/decisions/` package marker, the `pyproject.toml` `src.decisions` entry, and the `decision-engine.md` v1 substitutions note + changelog row are **already on `main`** — do NOT modify `pyproject.toml`, `src/decisions/__init__.py`, or `docs/decision-engine.md`.
- Pure core: `xp_5gw_by_player(...)`, `sell_candidates(...)`, `buy_candidates(...)`, `hit_cost(...)`, `suggest_transfers(...)` returning ranked pairs.
- Thin `get_transfer_suggestions(conn) -> dict` reader returning the `/api/transfers` shape.
- `tests/test_transfers.py` — property tests (B11): 3-per-club rule, budget constraint, valid resulting squad, hit-threshold logic.

### Out of scope
- `form_adjusted_delta` sell criterion (needs per-GW actuals — deferred).
- Real selling price / free-transfer count (auth-only — Phase 2).
- Multi-transfer planning (the −4/−8 path beyond a single free transfer).
- The FastAPI endpoint serving this (separate workstream — calls `get_transfer_suggestions`).
- Chip recommender (separate, after DGW/BGW detection).

## 4. Behavior (precise)

`xP_5gw` for a player = sum of their `xp` rows for the next 5 GWs (`MIN unfinished gw` .. +4), model v1; players with no rows → 0.

`sell_candidates(squad_players)`: per position, compute the median `xP_5gw` across **all players** in that position (the market, not just squad). A squad player is a sell candidate if `xP_5gw < that median` OR `status != 'a'`.

`buy_candidates(sell, all_players, squad, bank)`: players not already in the squad, same position as `sell`, `status == 'a'`, `price ≤ sell.price + bank`, and adding them (removing `sell`) keeps **≤ 3 players per club**; ranked by `xP_5gw` desc; take the best.

`suggest_transfers(...)`: for each sell candidate, pair with its best buy; `ep_delta = buy.xP_5gw − sell.xP_5gw`; keep positive deltas; sort all pairs by `ep_delta` desc; take top 3. Each pair: `hit_cost = 0` (v1 single-free-transfer assumption). If no positive-delta pair, `empty_reason = "No transfers worth making this GW."`.

`hit_cost(num_transfers, free_transfers=1)` (tested helper): `max(0, num_transfers - free_transfers) * 4`, returned as a negative points cost (e.g., 2 transfers, 1 FT → −4). `is_worth_hit(ep_delta, hit_cost)`: `ep_delta > abs(hit_cost)` (and `ep_delta > 0` when hit 0).

Output matches `docs/api-contract.md` `/api/transfers`: `{suggestions: [{out:{player_id,web_name,price}, in:{player_id,web_name,price}, ep_delta_5gw, hit_cost, confidence}], empty_reason}`. `confidence` is out of scope for this slice → return `null` (the confidence-score model is a later concern); the field stays in the shape.

## 5. decision-engine.md update (B4) — ALREADY APPLIED ON main
The "Transfer engine" section already documents the three v1 substitutions and changelog row `v0.5`. **Do not modify `decision-engine.md`.**

## 6. Components
- `src/decisions/transfers.py`: the pure functions above + `get_transfer_suggestions(conn)`.
- Reuse `xp` reads; do not duplicate xP computation (consume the `xp` table).

## 7. Testing (B11 — property tests required)
- `test_xp_5gw_sums_five_gws`: a player's `xP_5gw` equals the sum of their next-5 `xp` rows.
- `test_sell_candidate_below_median_or_flagged`: a below-median player and a flagged (`status='i'`) player are sell candidates; an above-median available player is not.
- `test_buy_respects_budget`: a buy whose `price > sell.price + bank` is excluded. **(property)**
- `test_buy_respects_3_per_club`: a buy that would create a 4th player from one club is excluded. **(property)**
- `test_suggestion_leaves_valid_squad`: applying any returned suggestion yields a 15-man squad with ≤3 per club and within budget. **(property)**
- `test_hit_cost_thresholds`: `hit_cost(1,1)==0`, `hit_cost(2,1)==-4`, `hit_cost(3,1)==-8`; `is_worth_hit` true only above the threshold.
- `test_empty_reason_when_no_positive_delta`: all squad players already optimal → `suggestions==[]`, `empty_reason` set.
- `test_get_transfer_suggestions_integration` (in-memory DB): seed players/my_team/xp; assert ≤3 suggestions, each a valid same-position swap within budget, shape matches the contract.

## 8. Definition of done
1. `pytest` green incl. the property tests.
2. `get_transfer_suggestions(conn)` returns a valid `/api/transfers` payload against the live DB; every suggestion is a legal swap (same position, budget, 3-per-club).
3. `decision-engine.md` documents the three v1 substitutions + changelog (B4).

## 9. Notes
- Suggest-only; no execution (Phase 2).
- Single-transfer suggestions only in v1 (FT=1 assumption); the hit calculator exists and is tested for when FT count + multi-transfer planning arrive.
- `confidence` returned as `null` until the confidence-score model is built.
