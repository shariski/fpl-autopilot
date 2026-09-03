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

> **Pre-season caveat (observed 2026-08-14, 26/27):** FPL publishes `strength_* = 0` for all
> teams before the season starts, so every team lands in the same quintile and all fixtures
> rate `fdr_attack = fdr_defense = 1`. The xP model therefore treats every fixture as neutral
> until FPL populates real strengths (expected around GW1). Not a bug in the computation —
> degenerate input, expected state, self-corrects. The AI insight feature may surface this as
> a "flat fixture ratings" finding; treat it as data-quality noise, not an anomaly.

### v2 (current, 2026-08-16) — xG-based continuous multipliers

Unblocks the original v2 spec: team per-match xG was unavailable from Understat; the
Vaastav databank (`player_stats` rows with `source='fpl_databank'`) now provides per-GW
xG/xGC for every player. Ratings are venue-agnostic team aggregates (home/away venue
split is a documented follow-up; interim venue factors ride on the xP v2 side).

For a fixture `Home H vs Away A`, for each team the opponent difficulty is a continuous
multiplier (not a 1–5 integer):

```
xgc_ratio[A] = damped( VS xGC/90[H]  ÷ LA xGC/90 )     # opponent defense, for A's attack
xg_ratio[A]  = damped( VS xG/90[H]   ÷ LA xG/90 )      # opponent attack, for A's defense
xgc_ratio[H] = damped( VS xGC/90[A]  ÷ LA xGC/90 )
xg_ratio[H]  = damped( VS xG/90[A]   ÷ LA xG/90 )

damped(x) = sign(x) × ( min(|x|, 1.55) + max(|x| − 1.55, 0) × 0.4 )
```

- `VS xG/90` = opponent's team xG per 90 (all opponents' players, databank),
  `LF/SF blend = 0.8 × last 38 GW + 0.2 × last 6 GW`; `VS xGC/90` likewise from xGC.
- `LA xG/90` = league-average team xG per 90 over the same window; `LA xGC/90` likewise.
- Stored in `fdr` as `fdr_attack_mult` (opponent xGC ratio) and `fdr_defense_mult`
  (opponent xG ratio) per `(team_id, gw)`; the v1 quintile columns are retained for v1
  consumers (xP v1, chips v1).
- Pre-season (no 26-27 databank rows yet): the window defaults to the most recent
  completed season (25-26), so ratings are live at GW1 instead of flat — fixing the v1
  degenerate state.
- Promoted teams: no databank history; use manual xG/90 / xGC/90 overrides until the
  team has ≥ 5 databank GWs (26-27: COV 1.9, IPS 1.72, HUL 1.3 attack; defense 1.55).

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

### v1 (retained as B5 comparison evidence)

v1 continues to compute in every refresh (`model_version='v1'`) but is not consumed by
any decision since v0.14 (2026-08-16). It exists so the GW1 live window can be compared
against the backtest; retire it after the GW1 review.

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

### v2 (current, 2026-08-16) — 11-component model

Consumed by every decision path since v0.14 (2026-08-16). v1 continues to compute in
every refresh as B5 comparison evidence through the GW1 review (2-season backtest
evidence: `docs/research/benchwarmers-model.md` §10). Full provenance, calibration data
and the reverse-engineered reference model: `docs/research/benchwarmers-model.md`.

```
xP_v2[player, gw] =
    [ start% × (1 + p60 + saves + yc + rc + bonus + assist + goal + cs + twogc + dc)
      + (1 − start%) × sub_total ] × venue_mult

start%   = min(1, cop × blend(starts ÷ squads_made, recent_starts ÷ recent_squads))  # v0.24
           where blend(a, b) = (1 − w) × a + w × b,  w = min(1, recent_squads ÷ 3)
sub_total = (yc + rc + bonus + assist + goal + twogc) × 0.30         # Mn/Sub ÷ Mn/St league const

venue_mult = attack 1.15 home / 0.87 away, defense 0.88 / 1.12,
             saves 0.86 / 1.14, starts 1.00 / 1.00                   # interim component mults
p60      = P(minutes ≥ 60 | minutes > 0), per player, LF window      # the 60+ appearance point
saves    = saves_per_90 × xg_ratio                       (GK only)
yc, rc   = per-90 rates (LF/SF blend); rc capped at 0 pre-season-ish (sparse)
bonus    = 0.29 × opponent mult                          (per start; bps proxy is a refinement)
assist   = xa_per_start × xgc_ratio × 1.38 × 3           (FA boost calibrated 1.38)
goal     = xg_per_start × xgc_ratio × goal_pts[pos]
cs       = min(1, e^(−λ) + 0.04) × cs_pts[pos],          λ = team_xgc_damped × xg_ratio
twogc    = min(1, (1 − e^(−λ)(1+λ)) + 0.045) × −1        (GK/DEF)
dc       = dc_per_start × dc_ratio × 2                    (DEF ≥10, MID/FWD ≥12 per FPL rules)
xg_ratio = fdr columns: damped(opponent xG/90 ÷ LA xG/90)             (FDR v2)
xgc_ratio= damped(opponent xGC/90 ÷ LA xGC/90)
dc_ratio = damped(opponent DC/90 ÷ LA DC/90, team-level)
```

- **Rates** (xG/St, xA/St, DC/St, saves/90, YC/90, RC/90, starts, p60) come from
  `player_stats` databank rows **and** current-season live rows (`player_gw_stats`,
  synthetic source `fpl_live:<season>`, aggregated per (player, gw): DGW stats summed,
  starts = started ≥1 fixture), `LF = last 38 GW`, `SF = last 6 GW`, blend 0.8/0.2,
  windows ordered by season then gw (v0.23). Live rows are authoritative in-season:
  databank rows of a season with live rows present are excluded (R9). Rows not yet
  backfilled (`starts IS NULL`) are skipped. Pre-season (no live rows) the windows
  span the last complete season (25-26). Per-start rates are `Σ stat ÷ Σ starts`
  (per-90 for saves/YC/RC). Players with no prior databank rows shrink toward pooled
  position averages until `MIN_LIVE_RATE_GWS` = 3 live GWs.
- **squads_made** = the player's team's databank GW count in the window (one match per
  team per GW).
- **Recent-window start term (v0.24):** next-GW start probability is not a season-long
  rate — a GW1 benching is far more predictive of GW2 than a GW38-25 benching was.
  Once live rows exist, start% blends toward the player's starts in the live GWs of
  the LF window (`recent_starts ÷ recent_squads`, same team-match semantics), weight
  `w = min(1, recent_squads ÷ RECENT_PSTART_GWS)` with `RECENT_PSTART_GWS = 3`. At 1
  live GW the recent term has 1/3 weight (a GW1 benching moves 1.00 → ~0.65, not
  0.97); at ≥3 live GWs start% is the current-season rate alone. `chance_of_playing`
  and status still multiply the blended rate. Pre-season (no live rows) unchanged.
  Rationale (GW2 26/27 live): the natural window (v0.23) left p_start ~97% prior —
  Garner, benched GW1, was priced as a near-certain GW2 starter. Backtest verdict:
  see v0.24 changelog entry.
- **λ** uses the player's own team's LF/SF-blended xGC/90, damped. The +0.04/+0.045
  corrections compensate Poisson over-dispersion vs real goals (calibrated 24-25/25-26).
- **Status** multiplies start% (a=1.0, d=0.5, i/s/u=0.0) — unchanged from v1.
- Stored in `xp` with `model_version='v2'` and component columns (`p_start`, `xbonus`,
  `xdc`, `xcs_lambda`).

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

## Squad builder (S-B) — AI-assisted starting squad

**Status:** added 2026-08-14 (B4 entry; spec `docs/superpowers/specs/2026-08-14-squad-builder-design.md`).
**Revised 2026-08-14:** the AI no longer picks the squad (it proved unable to satisfy the
constraints — invented slots, budget overshoot). It is now a **speculative input signal**;
the deterministic optimizer owns the decision (spec
`2026-08-14-squad-speculation-design.md`).

**Division of labour (deterministic law, inspectable output):**

1. **Deterministic candidate pool** (`src/decisions/squad_builder.py`): legal prefilter
   (status a/d, price ≥ 4.0, xP present), per-position top-15 by xP-6-GW ∪ top-10 by value,
   ~90-100 players. Logged with the result.
2. **AI speculative signal** (`src/ai/squad/spikes.py`): the LLM labels up to 10 spike and
   5 drop candidates with `high`/`medium` levels and one grounded reason each, plus a
   `market_read`. No slots, no prices, no sums — the AI never touches legality.
   **Edge gate:** a reason that only restates the projection (xP/xG/xA/price) is rejected —
   the signal must cite market/trend evidence the optimizer does not see
   (`transfers_in`/`transfers_out`/`net_momentum`, ownership, form, recent GW points,
   fixture shape). This keeps the signal additive rather than redundant with xP.
3. **Deterministic optimizer decides** (`src/decisions/squad_validator.py`): picks the 15 by
   `xp_6gw + bonus`, budget-aware with minimum-remaining-cost reservation. Bonus constants
   (B4 — do not change without a log entry): `SPIKE_BONUS = {high: +1.5, medium: +0.75}`,
   `DROP_BONUS = {high: −1.5, medium: −0.75}`.
4. **Validator is the law**: 2-5-5-3, budget ≤ 100, ≤ 3/club, unique ids from the digest.
   Guaranteed legal — the optimizer is budget-aware by construction.
5. **Apply** (`src/execution/squad.py`): dry-run default; `--live` requires master key + typed
   confirm; any API refusal aborts with a report. Applies only when the API allows
   (pre-season unlimited window; wildcard).
6. **Logging (B10):** every squad decision logs `decision_type="squad"` with the pool, picks,
   the applied speculation map (player → level), source, budget, and per-transfer outcomes.
   Speculation failure is logged (`spikes_failed`) and degrades to pure xP — never blocks.

No thresholds changed. The squad is a **suggestion** until the user executes it — execution
rules (chips, hits, caps) are unchanged.

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
| v0.10 | 2026-05-23 | Deadguard 2.5b: bench-order optimization (rank positions 13/14/15 by next-GW xP, xMinutes tiebreaker; FPL native auto-sub does the swap); targeted transfer-if-flagged (OUT status not in a/d, free only, ep_delta_5gw >= 3.0, confidence >= 75, max 1). Captain + transfer engines reused unchanged. |
| v0.11 | 2026-05-23 | Deadguard 2.5c-1 late-news re-evaluation: after DEADGUARD_EXECUTED, `evaluate` returns a `reeval` directive (>lockout) or `lockout` directive (<= `reeval_lockout_minutes`, default 15) until the deadline. Re-eval force-refreshes FPL availability + recomputes the lineup; a material change (captain/vice/bench differs from what is set) auto-applies via the existing captain/bench rankers when >15 min out, else alert-only. Lineup-only - no transfer (B8). Rankers reused unchanged (no threshold edits). |
| v0.12 | 2026-08-16 | FDR v2 implemented: continuous xG-based opponent multipliers (opp xGC/90 ÷ league avg for attack; opp xG/90 ÷ league avg for defense), dampened (safe threshold 1.55, 40% beyond). Replaces quintile FDR v1 for xP v2 (v1 columns retained for v1 consumers). Fixes the pre-season degenerate state (v1 = flat when FPL publishes strength=0). Unblocked by databank ingestion (`source='fpl_databank'` in player_stats). Provenance: `docs/research/benchwarmers-model.md`. |
| v0.13 | 2026-08-16 | xP v2: 11-component model (appearance, 60+ mins, 3×saves, YC, RC, bonus, assist, goal, CS, 2+GC, DC) with LF(38)/SF(6) 0.8/0.2 blends over the databank, P(start) = chance_of_playing × starts/squads-made, Poisson CS + 2+GC with calibrated bias corrections (+0.04 / +0.045), FA boost 1.38, DC thresholds DEF ≥10 / MID+FWD ≥12, bonus 0.29/start, component venue multipliers (attack 1.15/0.87, defense 0.88/1.12, saves 0.86/1.14, starts 1.00/1.00). Stored as `model_version='v2'` alongside v1; both run in every refresh (B5 parallel-run). All constants empirically calibrated on 24-25 + 25-26 databank data — see `docs/research/benchwarmers-model.md` §9.4. |
| v0.14 | 2026-08-16 | xP v2 becomes the consumed model: squad builder pool, captain ranker, bench order, transfer engine, chip DGW-xP, interface queries and AI insight all read `model_version='v2'`. FDR v1 quintiles remain for chip-trigger FDR semantics (unchanged rule). v1 keeps computing in every refresh purely as B5 comparison evidence through the GW1 review. Decision: 2-season no-leakage backtest (MAE 1.22/1.36 vs 2.16/2.14, bias +0.1 vs +1.0, corr 0.40 vs 0.36 — `docs/research/benchwarmers-model.md` §10). Captain ranker unchanged (max xP); a ceiling-aware captaincy term is a documented follow-up, not part of this change. |
| v0.15 | 2026-08-20 | Data-integrity + squad optimizer fixes (no thresholds touched). (1) Databank remap now matches by NAME, never by element id (FPL reuses ids across seasons — 25-26 rows were mis-attributed to whoever holds that id today, zeroing ~194 players incl. most premiums; see `docs/risks.md` R8). (2) `optimize_squad` reserve now sums the N cheapest unused players per position instead of cheapest×N, which under-reserved when cheap options were spread thin and could strand a final slot (95m spent, FWD3 unfillable → `ValueError`). (3) Team/league xG·90 and DC·90 are now normalized per MATCH, not per player-minute — player xG sums to the team match total once, so dividing by all players' minutes deflated xg90/la.xg90 ~13× (la.xg90 0.132 vs true ~1.35) and made promoted-override multipliers explode (damp(1.3/0.132) = 4.86 → a GKP vs a promoted side projected 14.43 xP). Ratios for non-promoted teams self-cancelled, so the backtest verdict is unchanged: re-run 2026-08-20 gives v2 MAE 1.217/1.357 vs v1 2.163/2.144, bias +0.15/+0.12 vs +1.20/+0.99, corr 0.396 vs 0.358. All three fixes are legality/data-integrity repairs; the greedy xP objective, SPIKE/DROP bonuses and all thresholds are unchanged. Re-ingest tool: `docs/research/calibration/reingest_databank.py`. |
| v0.16 | 2026-08-20 | Chip recommender fix (no thresholds touched): the authed my-team snapshot stores chips as FPL dicts `[{"name": "wildcard", ...}]`, not strings — `_squad` hashed the raw entries and died with `TypeError: unhashable type: 'dict'`, crashing the hourly AI reasoning job (`ai.generate_job_failed`) and the chips pane/CLI. `_chips_used_set` now normalizes both shapes. |
| v0.17 | 2026-08-20 | `execute-lineup` now also applies the ranker's bench order (positions 13-15, next-GW xP desc — `rank_bench`) alongside captain/vice. FPL's native auto-sub follows bench order, so leaving it untouched meant a sub-optimal auto-sub when a starter misses. Bench reordering was already the deadguard behaviour; the ranker and its thresholds are unchanged. The applied bench order is logged in the lineup activity inputs (B10). |
| v0.18 | 2026-08-20 | New: squad status-change watcher (`src/interface/status_watch.py`). Each scheduler refresh captures the squad's (status, chance_of_playing) before the FPL fetch and diffs after; a Telegram alert + activity-log entry fires only when a squad player's availability WORSENS: status rank increase (a < d < i < u/s) OR chance_of_playing drop ≥ 25 percentage points. No change → no alert (idempotent). Squad = latest my_team snapshot; players outside the squad are ignored. A watcher failure never breaks the refresh (logged). |
| v0.19 | 2026-08-20 | Operating mode becomes a runtime switch: `fpl-autopilot mode --set <manual|hybrid|auto>` and the dashboard selector (`POST /api/mode`) write system_state; `config.mode()` prefers the override over the image-baked config.yaml, so no config edit + redeploy is needed to switch modes. The auto-execute job's gate changed from `unattended.enabled` to `mode == 'auto'` — auto mode now implies unattended execution near the deadline (timing still from `unattended.hours_before_deadline`). Chips still always require confirmation (B3); freeze still halts everything regardless of mode. B11 still binds: Auto should only be enabled after 3 GWs of dry-run comparison. |
| v0.20 | 2026-08-20 | Two model refinements (both recalibrations of v2, same formula structure — v2 stays the consumed model, constants re-pinned here). **(a) Bonus recalibrated per position** from the 25-26 databank: the flat 0.29/start under-credited forwards (25-26 actuals: FWD 0.580, MID 0.306, GK 0.212, DEF 0.211) — now `BONUS_PER_START = {GKP: 0.21, DEF: 0.21, MID: 0.31, FWD: 0.58}`. **(b) Captaincy ceiling term**: the ranker now scores `xp + 0.15 × (xgoals + xassists)` (`CEILING_WEIGHT = 0.15`) — GKP/DEF have ~zero goal involvement, so the term prefers premium attackers when the xP gap is small; rank-1 reason says "Ceiling-adjusted top pick" when the term reorders. Backtest re-run 2026-08-21 (no leakage): MAE unchanged in substance (v2 1.215/1.357 vs v1 2.163/2.144); captain-proxy improves — 24-25 plain v2 top-pick 4.89 (was 4.51 with the old bonus) and ceiling pick 5.35; 25-26 ceiling pick 3.11 vs plain 2.89. Follow-up: FPL's 26/27 bonus rebalance is directional; re-calibrate from 26-27 databank once ~6 GWs are in. |
| v0.21 | 2026-08-21 | Pre-season defensive-captain penalty (live-GW evidence): while the current season has no databank rows (all projections lean on last season + lineup risk is invisible), GKP/DEF captain scores get `PRE_SEASON_DEF_PENALTY = 1.5`. GW1 26/27 live: the top-xP captain (Dubravka, 8.86) was benched → 0 pts; the whole-GW review showed v2 under-predicting (MAE 2.38, bias −0.44 on 245 players — v1: 2.88, +0.60), with the worst misses on upside events (proj 2.5-3.6 → actual 11-17). Pre-season detection: `player_stats` has no `fpl_databank:<current season>` rows. The penalty flips only close calls; a huge keeper cushion still wins. Backtest proxy updated (penalty applied to each season's GW1); no regression (ceiling-proxy 5.35/3.11 unchanged). |
| v0.22 | 2026-08-25 | Fix: `chance_of_playing` scale bug — FPL stores it 0-100 but the p_start formula assumed 0-1. `min(1.0, cop × starts/squads)` with cop=100 capped every available player's start probability at 1.0, nullifying BOTH rotation risk (starts/squads) and the doubt haircut. Observed live: Brooks (13/37 starts, 34%) got a guaranteed 90-minute 7.1 xP projection, inflating his transfer-EP delta against a doubtful Anderson. The backtest passed cop=1.0 (0-1 scale) so it priced rotation correctly — production had diverged. Fix: values >1.0 are normalized /100 at the formula boundary (0-1 callers unchanged). Recompute + transfer re-check follow. |
| v0.23 | 2026-08-25 | In-season data source (Vaastav-free learning): ratings now blend current-season per-GW stats captured from FPL's own `event/{id}/live` (already fetched hourly by settlement) with the databank. `player_gw_stats` gains starts/saves/bps/xG/xA/xGC/DC/YC/RC (verified present in the live payload 2026-08-25); GWs settled pre-change are auto-backfilled (GW1 heals on the first refresh). The LF(38)/SF(6) windows span both sources, ordered by season then gw — natural window, no new blend constants; live rows authoritative in-season (R9). New-signing guard `MIN_LIVE_RATE_GWS = 3` (live-only rates shrink toward pooled position averages). The v0.21 defensive-captain penalty now auto-off once the SF window has ≥ 3 live pairs (`SF_LIVE_MIN = 3`; was: databank-rows detection). Backtest: blend simulation (prior 24-25, live 25-26, strict no-leakage) — verdict: blend beats pure prior at every stage (MAE 1.302 vs 1.714 on live-GWs 6+, 1.713 vs 1.825 on 3-5, 1.835 vs 1.864 on 1-2); bias blend +0.130 vs prior +0.092 (mild over-prediction, same sign as v2's known bias). |
| v0.24 | 2026-08-26 | Early-season start-probability fix (live-GW evidence, GW2 26/27): the v0.23 natural window left p_start ~97% prior-season — Garner (benched GW1, 11 min) was priced as a near-certain GW2 starter with 0.97 start probability, inflating his transfer EP delta (13.75) on a case that GW1 contradicted. **Recent-window term:** start% now blends toward the player's starts in the live GWs of the LF window (`recent_starts ÷ recent_squads`), weight `w = min(1, recent_squads ÷ RECENT_PSTART_GWS = 3)`; `chance_of_playing` and status multiply the blended rate (v0.22 normalization preserved). Pre-season (no live rows) unchanged. Also (a) `PlayerRates` exposes `recent_starts`/`recent_squads`; (b) transfer suggestions gain an early-season `caveat` (data-basis note + "started 0 of N live GWs" when the buy has not started) and an `EARLY_SEASON_CONF_PENALTY = 15` on confidence while live pairs < 3 (deadguard's confidence floor now correctly blocks early-season transfers). Backtest: blend simulation re-run (prior 24-25, live 25-26) — verdict: MAE blend 1.688 vs prior 1.864 on live-GWs 1-2 (v0.23: 1.835), 1.179 vs 1.825 on 3-5 (v0.23: 1.713), 1.074 vs 1.714 on 6-38 (v0.23: 1.302); bias +0.023/+0.006/+0.077 (v0.23: +0.021/+0.098/+0.130). The recent-window term improves the blend at every stage and nearly eliminates the early bias. |
| v0.25 | 2026-09-03 | `execute-lineup` adds **cohort-formation rebalance** (slot swap, no transfers): after the captain/vice/bench-order pass, look up the modal formation among the tracked top-100 cohort (`leader_gw_picks.formation` for the upcoming GW). If `cohort >= COHORT_FORMATION_MIN = 20` for that GW AND the modal formation differs from the user's current XI shape AND the squad can fill that shape, swap the lowest-xP starter of the surplus position with the highest-xP bench player of the deficit position (slot 12 is the bench GK anchor — never swapped; only 13/14/15 are swapped). Captain/vice stay with their player id. If the squad cannot fill the modal shape (wrong position counts), or the modal is a tie within 1 vote of the runner-up, no swap. Activity-log entry captures both the current and modal formation plus the swap diff (B10). Fallback (cohort < 20, no picks stored for upcoming GW, modal tie within 1 vote, illegal shape): behave exactly as v0.17. Captain/vice/bench-order logic, the ranker, and all thresholds are unchanged — this is a pure slot-assignment layer that reads existing analytics output.
