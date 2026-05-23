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

### v1 (current) — FPL-strength quintile

FDR v1 derives difficulty from FPL's own team strength ratings (`strength_attack_home/away`, `strength_defence_home/away`), which are finer-grained than FPL's rank-based FDR. Team xG (the v2 basis) is not obtainable — Understat exposes only per-player season aggregates as of 2026-05-22.

For a fixture `Home H vs Away A`, each team is rated from the opponent's venue-specific strength:

- `fdr_attack[H]  = quintile(A.strength_defence_away)`   `fdr_defense[H] = quintile(A.strength_attack_away)`
- `fdr_attack[A]  = quintile(H.strength_defence_home)`   `fdr_defense[A] = quintile(H.strength_attack_home)`

`quintile(value)` ranks the value against the 20-team distribution for that venue/column and returns 1–5 (5 = strongest opponent = hardest): `min(below*5 // n + 1, 5)` where `below` = count strictly less than `value`. `fdr_attack` keys off the opponent's defense; `fdr_defense` off the opponent's attack. Venue advantage is intrinsic to FPL's separate home/away columns (no extra ±0.3 factor). A single current rating, not rolling form.

### v2 (target) — xG-based

Deferred: blocked on team per-match xG ingestion (xG conceded/scored), unavailable from Understat as of 2026-05-22. The original xG-based definition:

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

### v1 implementation (current, 2026-05-22)

Two corrections vs. the structural sketch above: (1) the leading `xMinutes` term was a typo — raw minutes must not be added to a points total; the minutes contribution to points is `appearance_points`. `xMinutes` only scales xGoals/xAssists and gates the clean sheet. (2) `xCleanSheet` uses `cs_prob(fdr_defense)` instead of Poisson(opponent xG/90), because team xG is unavailable (see FDR v2 note). FDR here is FDR v1 (FPL-strength).

```
GOAL_PTS = {GKP:6, DEF:6, MID:5, FWD:4}     CS_PTS = {GKP:4, DEF:4, MID:1, FWD:0}
STATUS_MULT = {a:1.0, d:0.5, i:0.0, s:0.0, u:0.0}   # unknown -> 1.0
FDR_ATTACK_MULT = {1:1.20, 2:1.10, 3:1.00, 4:0.90, 5:0.80}
CS_PROB         = {1:0.55, 2:0.45, 3:0.35, 4:0.22, 5:0.12}

xMin       = min(minutes/games, 90) * STATUS_MULT[status]      # 0 if games==0
p_appear   = clamp(xMin/20, 0, 1);  p60 = clamp((xMin-30)/30, 0, 1)
appearance = p_appear + p60
xGoals     = xg_per_90 * (xMin/90) * FDR_ATTACK_MULT[fdr_attack]
xAssists   = xa_per_90 * (xMin/90) * FDR_ATTACK_MULT[fdr_attack]
xCleanSheet= CS_PROB[fdr_defense] * p60
xP         = appearance + xGoals*GOAL_PTS[pos] + xAssists*3 + xCleanSheet*CS_PTS[pos]
```

Inputs come from `understat_players` (per-90 rates, season minutes/games — a v1 proxy for rolling) and `fdr`. Computed only for players with a matched Understat row whose team has an FDR row that GW. Stored in `xp` with `model_version='v1'` and all components, for the next 6 GW.

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

**v1 implementation (2026-05-22):** no explicit `rotation_risk` metric exists yet, so Tiebreaker 1 uses `xminutes` (expected minutes, from the xP row) as the rotation-risk proxy — higher expected minutes = lower rotation risk.

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

**v1 implementation (2026-05-22) — three data-forced substitutions:** (1) the `form_adjusted_delta` sell criterion is dropped (needs per-GW actual points, not yet ingested); (2) `sell_price` = the player's current `price` (true selling price is auth-only, Phase 2); (3) free transfers are assumed = 1, so a single suggested transfer is free (hit 0); the −4/−8 hit path is deferred to multi-transfer planning when the FT count is known.

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

### v1 implementation (2026-05-22)

- **Wildcard v1** uses only the fixture-swing criterion (≥3 squad players whose `fdr_attack` worsens by ≥2 over the next 3 GW — implemented as the `fdr_attack` at the next GW `N` vs 3 GWs later `N+3`). The "≥4 sell candidates" criterion is deferred until the transfer engine is integrated; "squad value below team average" is dropped (cross-manager data unavailable).
- **DGW-aware xP** for Bench Boost / Triple Captain = `fixture_count × single-fixture xP` (reusing `analytics.xp.compute_player_xp` with the team's stored FDR for that GW). The `fdr` table holds one value per `(team, gw)`, so both DGW fixtures share it (approximation).
- **Single recommendation priority:** Triple Captain → Bench Boost → Free Hit → Wildcard. Already-used chips (from `my_team.chips_used_json`, best-effort) are skipped.
- Flag-only; chips never auto-execute (B3/B8).

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

**Implementation detail (v0.7, 2026-05-23):** status-uncertainty maps the FPL `status` code —
`a`→0, `d`→+15, and `i`/`s`/`u`/`n`/unknown→+30 — taking the worst among the players involved in
the decision (captain + vice for captaincy; in + out for a transfer). Staleness is measured from
the `bootstrap-static` cache timestamp. Alternative-proximity uses the gap between the top two
options (captain: top-2 xP; transfer: a suggestion's EP delta vs the next suggestion's).

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

**Universal confidence gate (v0.8, 2026-05-23):** the confidence floor applies to *every*
auto-route, not just Auto mode. In Hybrid, a captain/bench or qualifying-transfer decision whose
`confidence < floor` falls back to notify-and-wait (rather than auto-executing). Manual mode always
notifies regardless of confidence.

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
| v0.2 | 2026-05-22 | FDR versioned: v1 = FPL-strength quintile (implemented); v2 = xG-based (deferred, team xG unavailable). |
| v0.3 | 2026-05-22 | xP v1 made concrete: appearance_points (not raw xMinutes), FDR-strength attack multiplier, cs_prob(fdr_defense) for clean sheet; constants pinned. |
| v0.4 | 2026-05-22 | Captain ranker v1: xminutes used as rotation-risk tiebreaker proxy. |
| v0.5 | 2026-05-22 | Transfer engine v1: dropped form_adjusted_delta (no per-GW actuals), selling price = current price, FT assumed 1; hit -4/-8 path deferred to multi-transfer. |
| v0.6 | 2026-05-22 | Chip recommender v1: DGW/BGW detection; Wildcard fixture-swing only (others deferred/dropped); DGW-xP via per-fixture sum; priority TC>BB>FH>WC. |
| v0.7 | 2026-05-23 | Confidence score implemented: status map (`a`→0, `d`→15, else→30); staleness from `bootstrap-static` cache timestamp; proximity gap between top-2 options. |
| v0.8 | 2026-05-23 | Universal confidence gate: floor applies in all modes (including Hybrid); low-conf decisions always fall back to notify-and-wait. |
| v0.9 | 2026-05-23 | Deadguard (Phase 2.5a) consumes the captain ranker for its captain/vice safety action when a Manual/Hybrid user goes silent (H-30 trigger). No threshold change — reuses existing captain selection. Transfer/bench scope deferred to 2.5b. |
