# Decision Engine

This document defines every rule the system uses to make a decision. Changes here are versioned. Code follows this document, not the other way around.

## Inputs

The decision engine consumes the following from the Analytics Layer:

| Input | Source | Refresh |
|---|---|---|
| `xP[player, gw]` | xP model | Per data refresh |
| `xP_5gw[player]` | sum of xP over next 5 GW | Per data refresh |
| `fdr_attack[team, gw]` | custom FDR | Weekly |
| `fdr_defense[team, gw]` | custom FDR | Weekly |
| `form_adjusted_delta[player]` | actual - expected, last 5 GW | Per data refresh |
| `status[player]` | injury / suspension / doubt | Hourly |
| `my_squad` | current 15 players, bank, FT | On user open or pre-deadline |
| `chips_used` | which chips already played | Weekly |

## Fixture Difficulty Rating (custom)

The official FPL FDR is based on team rank and is noisy. The system computes its own.

For each fixture `(team_a vs team_b)`:

```
fdr_attack[team_a, fixture] = f(xG_conceded_per_game[team_b, last 5 GW], home_away_factor)
fdr_defense[team_a, fixture] = f(xG_scored_per_game[team_b, last 5 GW], home_away_factor)
```

- Output is a 1–5 integer per fixture.
- Attack and defense are tracked separately. An attacking player and a defender on the same team can have different effective difficulties.
- Home / away adjustment: ~0.3 xG per game advantage at home, applied as a multiplier.
- Weighting: last 5 GW dominates; longer history used only for stability when a team has played < 5 GW.

## Expected Points (xP) model

### Version v1 (Phase 1 default)

```
xP[player, gw] =
    xMinutes
  + xGoals × goal_points_by_position
  + xAssists × 3
  + xCleanSheet × cs_points_by_position
  + appearance_points
```

Where:

- `xMinutes` = rolling 5 GW minutes average, adjusted by `status` flag.
- `xGoals` = (xG per 90 from Understat) × (xMinutes / 90) × attacking FDR multiplier.
- `xAssists` = (xA per 90 from Understat) × (xMinutes / 90) × attacking FDR multiplier.
- `xCleanSheet` = Poisson probability that opponent scores 0, given opponent's xG per 90 and home/away factor. Multiplied by P(xMinutes ≥ 60).
- `appearance_points` = 1 if `P(xMinutes ≥ 1) high`, 2 if `P(xMinutes ≥ 60) high`.

`goal_points_by_position`:

| Position | Goal pts |
|---|---|
| GK | 6 |
| DEF | 6 |
| MID | 5 |
| FWD | 4 |

`cs_points_by_position`:

| Position | CS pts |
|---|---|
| GK | 4 |
| DEF | 4 |
| MID | 1 |
| FWD | 0 |

### Deliberately deferred

- **xBonus.** Bonus points are hard to model. Phase 1 omits them. Phase 1.5 may add a BPS-history proxy.
- **Save points (GK).** Proxied through expected shots-on-target faced.
- **Defensive contributions / new scoring rules.** Update when the rule set changes.

### Versioning

Every xP value stored in the DB is tagged with model version. When introducing v2, run v1 and v2 in parallel for one full gameweek and compare actuals.

## Captain ranker

For every player in `my_squad`, rank by:

1. **Primary:** `xP[player, next_gw]` descending.
2. **Tiebreaker 1:** `1 - rotation_risk` (lower risk wins).
3. **Tiebreaker 2:** `fdr_attack[player.team, next_gw]` lower (easier fixture wins).

Output: top 5 with reasoning string. The reasoning must include the xP value, the fixture, and the second-best alternative's gap.

Vice-captain = #2 on the same ranking.

## Transfer engine (Phase 1: suggest only)

Algorithm:

1. **Identify sell candidates** in `my_squad`:
   - `xP_5gw < median(xP_5gw for position)` — underperforming relative to peers.
   - `status` flag is non-clear (any flag at all).
   - `form_adjusted_delta > +threshold` — overperforming, regression risk (default threshold = +5 points over last 5 GW).
2. **For each sell candidate, find buy candidates:**
   - Same position.
   - Price ≤ `sell_price + bank`.
   - Does not violate 3-per-club rule when substituted in.
   - Status flag is clear.
   - Rank by `xP_5gw` descending.
3. **Compute EP delta:** `buy.xP_5gw - sell.xP_5gw`.
4. **Hit calculator:**
   - 0 hit (free transfer available) → suggest if `EP_delta > 0`.
   - -4 hit → suggest if `EP_delta > 4`.
   - -8 hit → suggest only if `EP_delta > 8`. Mark as "rare."
5. **Return top 3** transfer pairs by EP delta, regardless of hit cost.

The user always sees the hit cost and the EP delta. The system does not hide tradeoffs.

## Chip recommender (Phase 1: flag only)

Each chip has a trigger condition. When met, surface a recommendation. Phase 1 does not execute.

### Wildcard

Trigger if **any** of:

- ≥ 4 players in `my_squad` are sell candidates (per the transfer engine).
- A major fixture swing is detected: ≥ 3 squad players have FDR worsening by ≥ 2 over the next 3 GW.
- Squad value has dropped significantly (≥ 1.0 below team average), suggesting poor asset management.

### Free Hit

Trigger if a blank gameweek is upcoming and `count(my_squad with fixture in BGW) < 8`.

### Bench Boost

Trigger if a double gameweek is upcoming **and**:

- All 15 squad players have at least one fixture in the DGW.
- The 4 bench players have `xP[DGW] > threshold` (default = 4 combined).

### Triple Captain

Trigger if a premium player (price ≥ 9.5) in the squad has:

- A double gameweek, AND
- Both fixtures have `fdr_attack ≤ 2`, AND
- `xP[DGW] ≥ 12`.

## Confidence score

Every decision the engine emits carries a confidence score (0–100). It is used in Phase 2 to gate auto-execution.

```
confidence = base_score
           - data_staleness_penalty
           - status_uncertainty_penalty
           - alternative_proximity_penalty
```

Components:

- **base_score** = 75 (anchor).
- **data_staleness_penalty:** +0 if data refreshed in last 6h; +10 if 6–24h; +30 if > 24h.
- **status_uncertainty_penalty:** +0 if all involved players have clear status; +15 if any has a doubt flag; +30 if any has a recent injury news flag without resolution.
- **alternative_proximity_penalty:** based on gap between top recommendation and second-best.
  - Gap > 2 EP → 0 penalty.
  - Gap 1–2 EP → +5.
  - Gap 0.5–1 EP → +15.
  - Gap < 0.5 EP → +25.

If `confidence < 70`, Phase 2 auto mode falls back to notifying the user instead of executing.

## Phase 2: mode routing

The Mode Router sits between the Decision Layer and the Action Executor. Per current mode:

### Auto mode

For each decision:

- If `confidence ≥ 70`: execute, log, notify.
- If `confidence < 70`: skip execution, send notification with recommendation, wait for user.

### Manual mode

For each decision: send notification with recommendation and inline buttons. Never execute without user confirmation.

### Hybrid mode

Decisions are partitioned:

| Decision | Behavior in Hybrid |
|---|---|
| Captain & vice | Auto-execute |
| Bench order | Auto-execute |
| Substitute flagged player | Auto-execute |
| Transfer (free, EP delta < 4) | Notify, wait |
| Transfer involving any hit | Notify, wait |
| Chip activation | Notify, wait (always) |

## Phase 2: deadguard rules

See `docs/deadguard.md` for the full state machine. Decision-engine rules that apply in deadguard:

- All thresholds tighten. `min_ep_delta_for_transfer` defaults to 3.0 instead of 2.0.
- Hits are forbidden by default (can be opted in to `allow_hit: true` with a hard cap of -4).
- Chips are always forbidden.
- Confidence floor rises to 75 from 70.

## Activity log schema

Every decision writes one row:

```
{
  "ts_utc": "2026-02-15T19:30:00Z",
  "gw": 26,
  "mode": "auto",
  "decision_type": "transfer",
  "action_taken": "Isak -> Watkins",
  "inputs": {
    "xp_v1_sell": 2.1,
    "xp_v1_buy": 5.8,
    "ep_delta_5gw": 3.7,
    "hit_cost": 0,
    "confidence": 78
  },
  "alternatives_considered": [
    {"buy": "Wood", "ep_delta_5gw": 2.9, "confidence": 72},
    {"buy": "Solanke", "ep_delta_5gw": 2.4, "confidence": 70}
  ],
  "executed": true,
  "exec_outcome": null    // filled in after GW settles
}
```

## Changelog (this document)

| Version | Date | Change |
|---|---|---|
| v0.1 | (initial) | First version. Phase 1 + Phase 2 decision rules captured. |
