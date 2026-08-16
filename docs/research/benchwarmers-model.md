# Benchwarmers FPL Model — Reverse-Engineered Analysis

Status: RESEARCH (no decision-engine rules changed). Dated 2026-08-16.
Source: FPL Benchwarmers YouTube walkthrough + workbooks shared by the creator
(`data/models-from-yt/`: `MODEL.xlsx`, `SIMPLE MODEL.xlsx`, `SOLVER.xlsx`), built for
season 2026-27 (GW1 deadline 2026-08-21 — same season as fpl-autopilot).

This document is the complete reverse-engineered spec of that model, the gap analysis
against fpl-autopilot, and a **draft** proposal for `docs/decision-engine.md` v2 changes.
Nothing here is implemented; B4/B5/B13 gate any adoption.

---

## 1. Architecture (data flow)

```
Vaastav databank (FPL-API per-GW CSVs) ──► pivot tables: MINS, xG, xA, xGC, PL, PTS, DC, FDRDC
FBref ──► PT (playing time: Mn/St, Mn/Sub, Starts, Subs, unSub)
          + CS (team goalkeeping: GA, SoTA, Saves, Save%, CS%)
FPL API bootstrap, current + previous season (API / PSAPI) ──► prices, chance_of_playing,
          season xG/xA, saves, bonus, bps, costs
Understat ──► correction factor (maps FPL/FBref xG to Understat scale)
Control Panel ──► all weights, window lengths, fallback constants, manual overrides
        │
        ▼
MODEL: 1000-player master; 11 FPL-scoring components × (Previous-Season | LF | SF) rates
        │
        ▼
PPts: player × GW projection (76,000 rows = 24-25 + 25-26 seasons = built-in backtest)
        │
        ▼
PREDICT: TRUE PTS / IF-START PTS / Start% / FIX / VALUE over a GW window
        │
        ▼
SOLVER (OpenSolver/CBC MILP): starting XI + bench + captain, budget ≤ 83
```

Sheets in MODEL.xlsx and their roles:

| Sheet | Role |
|---|---|
| CONTROL | All tunables: season, windows, weights, fallbacks, overrides, scoring rules |
| API / PSAPI | FPL bootstrap static, current + previous season |
| TEAMS / FIXTURES / FIXHELPER | FPL teams, fixtures, venue lookup |
| DATABANK / DBFORM | Flattened per-GW databank (Vaastav layout) / live form row |
| MINS, xG, xA, xGC, PL, PTS, DC, FDRDC | Pivot tables over the databank (player×GW, team×GW, cumulative) |
| TABLE / PSTABLE | Current + previous-season league tables (Understat xG/xGA) |
| PT / PSPT | FBref playing time, this + previous season |
| CS | FBref team goalkeeping table |
| MODEL | Player master + per-component rates |
| PPts | The projection engine (player × GW) |
| PREDICT | Window aggregation (TRUE PTS, value, ownership) |

The databank values are **cumulative season-to-date at each GW** (verified: `24-25-GW38`
David Raya 3420 mins; the pivots store the cumulative value at each GW column). LF/SF
windows are therefore computed as *differentials* between GW columns (e.g. SF = value at
GWn − value at GWn−6).

## 2. Control panel (all constants)

| Block | Constants |
|---|---|
| Season | `2026-27`, LAST GW = 0 (pre-season) |
| Appearance | PS/TS weights 1.0/0.0; 60+ mins probabilities: mn/st 90→0, 80→0.05, 70→0.15, 60→0.5, 50→0.5 |
| 3× Saves | PS/TS 1.0/0.0 |
| Yellow cards | PS/TS 1.0/0.0 |
| Red cards | PS/TS **0.0/0.0** (component zeroed — data sparsity) |
| Bonus | Fallback BPS/Start = 14.86, Bonus/Start = 0.294; min-starts filter = 5; source = Bonus (not BPS) |
| Goals & Assists | LF = 38 GW (weight 0.8), SF = 6 GW (weight 0.2); **Fantasy Assist Boost = 0.4** |
| Mins/Start blend | PS 1.0 / TS 0.0 |
| Clean sheets | LF 38 (0.8) / SF 6 (0.2) |
| 2+ goals conceded | LF 0.8 / SF 0.2 |
| Defensive Contributions | LF 38 (1.0) / SF 10 (0.0); mins filter 40 |
| FDR | LF 38 (0.8) / SF 6 (0.2) |
| Understat correction | piecewise anchors: x=2.0 → y=1.0; x=0.9 → y=0.9 |
| PPts FDR multiplier | anchors: x=2.25 → y=1.6; x=league avg → y=1.0 |
| Dampening | safe threshold 1.55, dampening factor 0.4 |
| Promoted teams (26-27) | xG/90 overrides: COV 1.9, IPS 1.72, HUL 1.3; DC/90: 8.04 / 7.58 / 8.11 |
| Home/Away | blanket 1.05 home / 0.95 away |
| Home/Away DC rates | per-team rates 25-26 (0.86 – 1.09); CB 0.928, DM 1.006, OVERALL 0.972 |
| FPL scoring | full 2025/26 rules incl. DC: DEF ≥10 → 2, MID/FWD ≥12 → 2; 2+ GC −1 (GK/DEF); saves×3 = 1 |
| TRUE VALUE curve | 0.8 at £5 → 0.6 at £10 → 0.4 at £15 (interpolated per bracket) |

## 3. MODEL sheet — per-player rates

Per player, three rate blocks are computed:

- **PS block** (previous season): Starts, Subs, UnSub, Mn/St, Mn/Sub, Sts/SqdsMd, Saves/90,
  YC/St, RC/90, Bonus, BPS, Start BPS, BPS/Start, Bonus/Start, A/St, xA/St, DC Hits/Starts,
  G+A PSxTS Mn/Start & Mn/Sub, Chance of Playing, and the appearance probabilities
  (`PS Mn/St %`, `PS Mn/Sub %`, `PS Sts/SqdsMd`, `PS UnSub/SqdsMd`).
- **TS block** (this season): same metrics, current-season.
- **LF/SF blocks** (long form 38 GW / short form 6 GW): xA, xG, xGC/90, %CS/St, %2+GC,
  DC/St, DC% — summed from the databank pivots (differentials).

Name matching against FBref tables uses **4 name variants** (FPL_Name, web_name,
FBRef_Name, Alt Name 2) with a dedup rule: if exactly one variant matches, use it; if
several match (same player via different spellings), average.

The 11 components (MODEL cols `1`–`11`) are each a blend of PS/TS or LF/SF rates:

```
1  = (PS%1 × w_ps + %1 × w_ts) × plays-flag × Chance of Playing   # start probability
2  = (PS%2 × 1.0 + %2 × 0.0) × 60+ mins points                    # appearance
3  = (PS%3 × 1.0 + %3 × 0.0) × 3xSaves rate
4  = (PS%4 × 1.0 + %4 × 0.0) × YC rate
5  = (PS%5 × 0.0 + %5 × 0.0) × RC rate                             # zeroed
6  = (PS%6 × 1.0 + %6 × 0.0) × Bonus rate
7  = (LF%7 × 0.8 + SF%7 × 0.2) × Assist rate                       # = xA/St × 3 (incl. boost)
8  = (LF%8 × 0.8 + SF%8 × 0.2) × Goal rate                         # = xG/St × pos goal pts
9  = (LF%9 × 0.8 + SF%9 × 0.2) × CS pts
10 = (LF%10 × 0.8 + SF%10 × 0.2) × 2+GC pts
11 = (LF%11 × 1.0 + SF%11 × 0.0) × DC pts
```

## 4. PPts — the per-GW projection engine

For each player × GW (both seasons — this is the built-in backtest):

```
VS xG/90  = 0.8 × opp LF xG/90 + 0.2 × opp SF xG/90
VS xGC/90 = 0.8 × opp LF xGC/90 + 0.2 × opp SF xGC/90
LA xG/90  = PS/TS-weighted league average xG/90
LA xGC/90 = PS/TS-weighted league average xGC/90

xG mult  = VS xG/90 ÷ LA xG/90        (used for GK/DEF & CS)
xGC mult = VS xGC/90 ÷ LA xGC/90      (used for attackers & bonus MID/FWD)

Component expected values:
  saves  = saves/90 × xG mult
  assist = xA/St × xGC mult × (1 + 0.4 FA boost) × 3
  goal   = xG/St × xGC mult × pos goal pts
  bonus  = xBonus/Start × (xG mult if GK/DEF else xGC mult)
  CS λ   = team dampened xGC × xG mult      # xGC FIX = Understat-corrected, dampened
  CS     = e^(−λ) × CS pts                  # Poisson P(0 conceded)
  2+GC   = (1 − e^(−λ)(1+λ)) × −1           # Poisson P(≥2 conceded)
  DC     = DC/St × (VS DC/90 ÷ LA DC/90 per position class) × 2

Start % = Chance of Playing × Sts/SqdsMd   (manual override via PREDICT M/S)
Mn/Sub ÷ Mn/St ratio scales the sub-appearance total

IF START TOTAL   = Σ all 11 components × H/A                # H/A = 1.05 home / 0.95 away
IF NOT START TOTAL = (YC + RC + Bonus + Assist + Goal + 2+GC) × (Mn/Sub ÷ Mn/St) × H/A
TRUE TOTAL = [ start% + Σ(2..11) × start% + IF NOT START TOTAL × (1 − start%) ] × H/A
```

Observations:

- The 1-pt appearance value is folded in as `start%` itself (the expected value of the
  "plays" point), and component 2 adds the 60+ pt, both weighted by P(start).
- Subs get no saves/CS/DC/appearance, and their rates scale by the Mn/Sub÷Mn/St ratio.
- Everything is opponent-adjusted; the dampening (cap 1.55, 40% excess) prevents extreme
  fixtures from over-swinging projections.
- The whole grid runs across 24-25 + 25-26, so every GW of last two seasons can be
  compared prediction-vs-actual — backtesting is intrinsic, not bolted on.

## 5. PREDICT + SOLVER

PREDICT aggregates PPts over the configured window (`BB:BC`):

- `IF START PTS` — best case (everyone starts), `TRUE PTS` — probability-weighted
- `Start %`, `FIX` (= number of fixtures in window → DGW/BGW aware)
- `VALUE = TRUE PTS ÷ £ ÷ FIX`, compared against the TRUE VALUE price-bracket curve
- ownership (`selected_by_percent`) surfaced per player

SOLVER (MILP via OpenSolver/CBC), ~785 players:

```
maximize  Σ (Pick-LU×PTS + Pick-Bench×PTS×U2 + Pick-Cap×PTS)
s.t.      Pick-LU + Pick-Bench = Squad (= 15)
          Σ (Squad × price) ≤ budget (83)
          Σ Pick-Cap = 1
          position structure per FPL rules (2-5-5-3)
```

The shipped Sensitivity report (2026-08-13) is the LP relaxation: Haaland picks at 0.2,
Igor Thiago 0.8, Bruno Fernandes captained at 34.2 TRUE PTS/window. Reduced costs /
allowable-increase columns give a principled "how close was this player to the squad" —
free alternatives-considered data.

## 6. Gap analysis vs fpl-autopilot

| Dimension | Benchwarmers | fpl-autopilot (current) | Impact |
|---|---|---|---|
| Per-GW history | 2-season databank (xG/xA/xGC/DC/starts/saves/bonus/bps/was_home per GW) | Season aggregates only (Understat); `element-summary` history modeled but not persisted | **Biggest gap**; blocks rolling rates, form_adjusted_delta, backtesting, residual feed |
| Clean sheet | Poisson e^(−λ) from team xGC | Fixed lookup by FDR quintile (0.55/0.45/0.35/0.22/0.12) | Our docs' v2 target; was "blocked on team xG" |
| GK saves | saves/90 × opp xG ratio | Deferred | Cheap to add via databank |
| Bonus | BPS/Start proxy × opponent | Deferred | Databank has bps/bonus per GW |
| DC (new scoring) | Full DC model (DEF ≥10, MID/FWD ≥12) | Explicitly deferred in docs | Databank has defensive_contribution |
| 2+ GC | Poisson P(≥2) × −1 | Not modeled | GK/DEF value underestimated |
| Minutes | Mn/St% + Mn/Sub% split; Starts/Squads-Made | minutes/games from season totals + status mult | Bench order / rotation risk inputs |
| Start probability | chance_of_playing × Sts/SqdsMd + manual override | Status multiplier only | We already have chance_of_playing |
| Form weighting | LF(38)/SF(6) 0.8/0.2, PS/TS blends | Season-to-date only | Form recency |
| Home/away | 5% blanket + per-team H/A DC rates | Implicit in FPL strengths; **degenerate pre-season** (flat quintiles until GW1) | Theirs survives pre-season, ours goes flat |
| FDR | Continuous xG multipliers, dampened | Quintile buckets, dead pre-season | = our FDR v2 spec, now unblocked |
| Value | TRUE PTS/£/fixture vs price curve | Raw xP sums | Transfer + squad ranking |
| Squad optimization | Joint MILP + sensitivity | Greedy xP-6GW + AI spike/drop bonuses | Sensitivity = alternatives (B10) |
| Promoted teams | −33.5% xG / +46.5% xGA study + per-team overrides | Nothing | Matters most in GW1 |
| Backtesting | Built-in (2 seasons of projections vs actuals) | Residuals only after execution (B10), no pre-deployment bench | B5 parallel-run is manual/one-GW |

## 7. Recommendations (ranked)

1. **Ingest per-GW history (Vaastav databank)** — 1 raw CSV per GW vs ~500
   element-summary requests; every column above verified present. Unblocks 8 of the 14
   gaps incl. `form_adjusted_delta` (restores dropped transfer-engine criterion) and real
   residual settlement. Fits existing `player_stats` table (add columns: xgc, dc, saves,
   starts, bps, was_home, value). B6: schema assertions, cache, UA, ≤1 req/s — as designed.
2. **xP v2 as the 11-component model** — versioned per B5; parallel-run vs v1; the
   residual machinery in `src/analytics/residuals.py` already compares frozen-xP vs
   actuals per decision.
3. **Poisson CS + 2+GC from team xGC** — this is the FDR v2 / xP v2 direction already
   spec'd in decision-engine.md; blocked only on team xG, which the databank provides.
4. **P(start) = chance_of_playing × starts/squads-made + manual override** — feeds
   appearance EV, bench order, deadguard. We have chance_of_playing today.
5. **Promoted-team multipliers** (−33.5% / +46.5%, per-team xG/90 overrides) — GW1 is
   Aug 21; COV/IPS/HUL.
6. **Backtest harness** — run v1 vs v2 across 24-25 + 25-26 databank GWs before switching
   (B5 parallel-run made cheap and continuous).
7. **MILP (PuLP) squad+captain v2** — sensitivity output improves "alternatives
   considered" (B10) and formalizes bench EV (deadguard currently ranks bench by raw
   next-GW xP).

## 8. What NOT to copy

- Blanket 1.05/0.95 home factor — crude; their own CONTROL has per-team H/A rates; use
  `was_home` split rates instead.
- Fixed 1.4 fantasy-assist boost — assert, then calibrate from databank (assists vs xA).
- RC component zeroed — data-sparse hack.
- 4-name FBref matching + dedup — we have `name_resolver.py`; FPL `element-summary`
  `starts` column makes FBref playing-time scraping largely redundant.
- 68MB / 76k-row Excel grid — a DB is strictly better; we already have one.

---

## 9. Implementation status

**v0.12 (FDR v2) and v0.13 (xP v2) are APPLIED** — changelog + normative spec landed in
`docs/decision-engine.md` (2026-08-16) and the code is implemented and tested
(`src/analytics/ratings.py`, FDR v2 in `src/analytics/fdr.py`, xP v2 in
`src/analytics/xp.py`, databank ingestion in `src/data/databank_client.py` +
`repository.upsert_databank_stats`, wired into `refresh` and the scheduler). B5
parallel-run is live: every refresh computes v1 + v2; nothing consumes v2 yet.

**v0.14 (value metric + MILP squad builder) remains DRAFT** — not implemented.

### 9.1 Proposed changelog entries

| Version | Date | Change |
|---|---|---|
| v0.12 | 2026-08-16 | FDR v2 implemented: continuous xG-based opponent multipliers (opp xGC/90 ÷ league avg, attack and defense separate), dampened (cap 1.55, 40% excess). Replaces quintile FDR v1. Unblocked by databank ingestion. Fixes pre-season degenerate state. |
| v0.13 | 2026-08-16 | xP v2: 11-component model (appearance, 60+ min, 3×saves, YC, RC, bonus, assist, goal, CS, 2+GC, DC) with LF(38)/SF(6) 0.8/0.2 blends, PS/TS blends (1.0/0.0 pre-season), P(start) = chance_of_playing × starts/squads-made with manual override, Poisson CS + 2+GC from dampened team xGC, home 1.05 / away 0.95 (interim; per-team split rates target). Stored as `model_version='v2'` alongside v1 (B5). |
| v0.14 | 2026-08-16 | Value metric: `value = xP_5gw ÷ price ÷ fixtures` with price-bracket TRUE VALUE anchors; transfer engine ranks by EP delta as today but surfaces value; squad builder v2 = MILP (PuLP) over candidate pool with bench discount U2 and sensitivity-derived alternatives. |

### 9.2 xP v2 spec (APPLIED — see decision-engine.md for the normative version)

```
xP[player, gw] =
    [ start% × (1 + 2pts_60plus + saves + yc + rc + bonus + assist + goal + cs + twogc + dc)
      + (1 − start%) × sub_total ] × venue_factor

start%        = min(1, chance_of_playing × starts/squads_made)      # manual override allowed
sub_total     = (yc + rc + bonus + assist + goal + twogc) × 0.30    # Mn/Sub ÷ Mn/St league const
venue_factor  = venue-split opponent ratings (interim: component mults —
                attack 1.15/0.87, defense 0.88/1.12, saves 0.86/1.14, starts 1.00/1.00)
saves (GK)    = saves_per_90 × xg_ratio
yc            = yc_per_90; rc = rc_per_90                            # pre-season: PS/TS 1.0/0.0
bonus         = 0.29 per start (fallback; refine to bps_per_start later) × opponent mult
assist        = xa_per_start × xgc_ratio × 1.38 × 3                 # FA boost calibrated 1.38
goal          = xg_per_start × xgc_ratio × goal_pts[pos]
cs            = min(1, e^(−λ) + 0.04) × cs_pts[pos],  λ = team_xgc_damped × xg_ratio
twogc         = min(1, (1 − e^(−λ)(1+λ)) + 0.045) × −1   (GK/DEF)
dc            = dc_per_start × dc_ratio × 2    (DEF ≥10, MID/FWD ≥12)
xg_ratio      = VS xG/90 ÷ LA xG/90             # venue-split ratings
xgc_ratio     = VS xGC/90 ÷ LA xGC/90
LF(38)/SF(6)  blends 0.8/0.2 for xA, xG, xGC, CS, 2+GC, DC (PS = previous season, 1.0/0.0)
dampening     = sign(x) × (min(|x|, 1.55) + max(|x| − 1.55, 0) × 0.4)
promoted      = xG/90 overrides per team (COV 1.9, IPS 1.72, HUL 1.3) until 5 GWs played
```

All rates sourced from the databank (FPL per-GW values: xG, xA, xGC, saves, bonus, bps,
starts, DC) with LF/SF windows summed per player; team ratings venue-split via `was_home`.

### 9.3 Schema deltas (APPLIED)

- `player_stats`: add columns `xgc REAL, dc INTEGER, saves INTEGER, starts INTEGER,
  bps INTEGER, was_home BOOLEAN, value REAL`; databank rows keyed
  `source='fpl_databank'` (**per-GW values** — Vaastav gws CSVs are per-GW, not cumulative).
- `fdr`: FDR v2 stores `fdr_attack_mult REAL, fdr_defense_mult REAL` alongside or instead
  of quintiles (new columns, versioned). Team venue-split ratings table
  `team_ratings (team_id, venue, gw_window, xg90, xgc90)`.
- `xp`: unchanged shape (per-player per-GW per-model_version) — v2 adds
  `xcs_lambda REAL, xbonus REAL, xdc REAL, p_start REAL` component columns.

### 9.4 Design decisions — RESOLVED 2026-08-16 (empirically calibrated)

All five open questions were answered against the actual Vaastav databank (24-25 + 25-26,
76 GW CSVs, per-GW values) and Understat player stats for both seasons. Raw data cached in
`data/databank/` (gitignored); analysis script archived at
`docs/research/calibration/` (see below).

| # | Decision | Evidence | Resolution |
|---|---|---|---|
| 1 | Databank ingestion | 24-25 + 25-26 complete (~12MB, 1 req/GW); 26-27 populates as season runs | **Ingest now.** Pre-GW1 it gives PS/LF rates for GW1 (which is what the Benchwarmers model does at LAST GW=0) and the full backtest harness. GW1 deadline 2026-08-21. |
| 2 | xG source | FPL (databank) xG correlates 0.985/0.989 with Understat (24-25/25-26); FPL xG ≈ 0.87–0.90× Understat (mean of ratios 0.888/0.927); FPL xA ≈ 0.74–0.83× | **Databank (FPL) xG/xA/xGC becomes the primary source** — per-GW, venue-aware, uniform, already ingested. Drop the Understat correction anchors (their x=2→y=1, x=0.9→y=0.9 regression exists only to reconcile sources). Keep Understat ingestion for cross-checks. |
| 3 | Starts source | Databank `starts` column is per-GW (verified). Mean minutes when played = 64.7/65.1; P(60+ \| played) = 0.68; starts/team-match = 10.9–11.0 | **Databank `starts`**, no FBref scrape. Sub-appearance scaling uses league constants (Mn/Sub ÷ Mn/St ≈ 0.30, matching their 0.2–0.66 fallbacks). FBref only if the sub model ever needs per-player Mn/Sub. |
| 4 | FA boost | League-wide weighted assists/xA = **1.375 (24-25), 1.382 (25-26)** (≥600-min players); per-player median 1.16–1.24 | **Pin 1.38** (calibrated, both seasons agree); their 1.4 is validated within noise. B4 constant, log entry required. |
| 5 | Home/away | Real per-component H/A multipliers (both seasons): xG 1.12/1.25, xGC 0.90/0.83, bonus 1.17/1.23, bps 1.06/1.15, **saves 0.85/0.88 (wrong sign for GK under blanket 1.05)**, assists 1.11/1.34, goals 1.05/1.27, starts 1.00/1.00. Per-team DC H/A 0.86–1.07 (validates their CONTROL sheet) | **Reject blanket 1.05/0.95.** v2 uses venue-split opponent ratings (home/away tables per team, like their DC home/away rates but for xG/xGC), which naturally produces ~1.1–1.25 attack uplift and the saves reversal. Interim fallback if venue-split tables aren't ready: component-specific multipliers (attack 1.15, defense 0.88, saves 0.86, starts 1.00). |

Additional calibration results baked into the v2 spec (§9.2):

- **Poisson CS bias**: e^(−λ) with λ = xGC/90 under-predicts both ends (actual CS% 0.351/0.375
  vs predicted 0.309/0.316; actual 2+GC% 0.437/0.415 vs predicted 0.394/0.385 — real goals
  are over-dispersed vs Poisson). Correction: `P(CS) = min(1, e^(−λ) + 0.04)`,
  `P(2+GC) = min(1, 1 − e^(−λ)(1+λ) + 0.045)`; team-level CS MAE 0.06–0.07 stays.
- **DC (25-26, only season with the stat)**: dc/start DEF 7.63, MID 8.51, FWD 4.85, GK 0;
  P(hit)/start DEF≥10 = 0.258, MID≥12 = 0.165, FWD≥12 = 0.011 → expected DC pts/start
  DEF 0.52, MID 0.33, FWD 0.02, GK 0.
- **Bonus/BPS fallbacks**: bonus/start 0.286–0.288, bps/start 15.4–16.1 (their 0.294/14.86
  constants validated); bonus ≈ 3 whenever bps ≥ 60.
- **League anchors**: LA xGC/90 (team) 1.40–1.45; goals/team/match 1.38–1.47; P(any
  minutes | listed) 0.39–0.42.

Raw databank files: `data/databank/` (gitignored). Calibration script:
`docs/research/calibration/calibrate.py`.
