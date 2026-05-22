# Product Spec — FPL Autopilot

## Problem statement

The user has played FPL for two seasons. Both seasons followed the same arc: engaged start, declining attention mid-season, abandoned by late winter. The user is not lacking in strategic ability — the failure mode is friction and forgetfulness, not skill.

The product exists to lower friction enough that the user stays engaged through GW38 without requiring constant attention.

## Core principles

1. **Friction reduction beats algorithmic sophistication.** A merely-decent recommendation that the user can accept with one tap will outperform a brilliant recommendation that requires opening a laptop.
2. **The system never falls more than one gameweek behind.** Even if the user goes radio-silent, the deadguard layer ensures captain, bench, and obvious moves still happen.
3. **Trust is built incrementally.** Phase 1 is read-only. Phase 2 introduces execution behind dry-run mode. Auto is only enabled after the user has watched the system make 3+ gameweeks of dry-run decisions and agreed with them.
4. **The user is always in control.** Override, freeze, and undo are always one action away.

## User stories

### Phase 1 — Insight Engine

- As the user, I can open the dashboard one hour before deadline and within five minutes know who to captain, what transfer (if any) to make, and whether a chip is in play.
- As the user, I can see my squad with each player's expected points for the next gameweek and the next five.
- As the user, I can see a fixture difficulty grid for the next 5–6 gameweeks for every player in my squad.
- As the user, I can see the top three transfer suggestions, each with the expected points delta over a horizon and the cost in hits if any.
- As the user, I can see captain recommendations with reasoning, not just a name.
- As the user, I am notified (via the dashboard, not yet Telegram) when chip conditions are met.

### Phase 2 — Decision Automation

- As the user, I can connect my FPL account once and trust that the system can act on my behalf.
- As the user, I can choose Auto, Manual, or Hybrid mode and change it at any time.
- As the user, I receive a Telegram notification 24 hours before deadline with the recommended captain and any transfer, and I can confirm or reject with one tap.
- As the user, I receive a Telegram notification 2 hours before deadline with a final summary.
- As the user, if I take no action and don't engage with the system during a gameweek, the deadguard takes over in a configurable pre-deadline window and handles captain, bench, and safe transfers.
- As the user, I can freeze all automation with a single command and resume later.
- As the user, I can put the system in dry-run mode where it generates decisions but does not execute them.
- As the user, I see a complete activity log of every decision the system made, including ones it considered and rejected.

### Phase 3 — AI Layer

- As the user, I can ask "why are you recommending I sell Saka?" and receive an explanation in natural language grounded in the underlying numbers.
- As the user, the system knows my mini-league standing and adjusts recommendations between template and differential strategies accordingly.
- As the user, the system learns from my transfer history whether I tend toward risk or safety, and biases recommendations accordingly.
- As the user, I can simulate scenarios ("what if I wildcard now vs in three weeks?") and see projected outcomes.

## UI states

### Dashboard (Phase 1)

Single-page PWA. Sections, top to bottom:

1. **Header** — current gameweek, deadline countdown, system status (auto / manual / hybrid / deadguard active / frozen).
2. **My team** — 15 players in a pitch layout, each cell showing name, position, price, xP next GW, xP next 5 GW, status flag if any.
3. **Captain pick** — top 5 players by xP this GW, each with a one-line reason.
4. **Transfer ideas** — top 3 suggested transfers (sell → buy), each with EP delta and hit cost. Empty state: "No transfers worth making this GW."
5. **Chip recommendation** — visible only when a chip condition is flagged.
6. **Fixture planner** — 5×6 grid (squad × next 5 GW), color-coded by custom FDR.
7. **Activity log** — recent decisions, filterable.

### Telegram bot (Phase 2)

Notification templates with inline buttons:

- **H-24 preview** — "GW{N} preview: Captain Haaland (xP 7.2), no transfers. [✅ Lock in] [🔄 See alternatives] [✏️ I'll do it myself]"
- **H-24 with transfer** — "GW{N} preview: Captain Salah (xP 6.8). Transfer suggested: Isak → Watkins (+3.2 EP, free). [✅ Accept all] [🟡 Captain only] [❌ Skip]"
- **H-2 reminder** — "GW{N} starts in 2h. Your team is set. Captain: Haaland. [📊 Details]"
- **Deadguard warning (H-120)** — "Deadguard will activate at H-30 if no action. [✅ I've reviewed, keep as is] [📊 Review now]"
- **Deadguard executed (post)** — "Deadguard acted: Captain Haaland, transfer Isak→Watkins (free). [📊 See log] [↩️ Undo transfer]"

## Data flow (Phase 1)

```
Scheduler ─── triggers ───┐
                          ↓
                  ┌─────────────────┐
                  │   Data Layer    │
                  │  FPL API client │
                  │  Understat/FBref│
                  │  SQLite cache   │
                  └────────┬────────┘
                           ↓
                  ┌─────────────────┐
                  │ Analytics       │
                  │  FDR computer   │
                  │  xP model       │
                  │  Form metrics   │
                  └────────┬────────┘
                           ↓
                  ┌─────────────────┐
                  │ Decision Layer  │
                  │ Captain ranker  │
                  │ Transfer engine │
                  │ Chip recommender│
                  └────────┬────────┘
                           ↓
                  ┌─────────────────┐
                  │ Interface       │
                  │ PWA dashboard   │
                  └─────────────────┘
```

## Data flow (Phase 2 adds)

The same flow, with Decision Layer feeding into an **Action Executor** that calls authenticated FPL endpoints. The Mode Router sits between Decision Layer and Action Executor and decides whether to:

- Execute directly (Auto mode + confidence above threshold)
- Send notification and wait for user (Manual / Hybrid)
- Wait for deadguard window (no action yet, no system action yet)

## Configuration model

A single `config.yaml` (or equivalent) holds user-tunable settings:

```yaml
mode:
  current: manual  # auto | manual | hybrid

thresholds:
  min_ep_delta_for_transfer: 2.0      # over 5 GW horizon
  min_ep_delta_for_hit_minus4: 4.0
  confidence_floor: 70                # below this, fall back to notify even in auto
  max_transfers_per_gw_auto: 2
  max_hit_per_gw_auto: 4

deadguard:
  enabled: true
  warning_window_minutes: 120
  trigger_window_minutes: 30
  scope:
    captain_vice: true       # always true, not user-editable
    bench_order: true        # always true, not user-editable
    auto_sub_flagged: true
    transfer_if_flagged: true
    transfer_if_underperform: false
    allow_hit: false
    min_ep_delta_for_transfer: 3.0

notifications:
  channel: telegram
  schedule:
    chip_preview_hours_before: 48
    transfer_preview_hours_before: 24
    final_reminder_hours_before: 2

xp_model:
  version: v1
```

## Out of scope (every phase)

- Multi-user, social, league chat
- Native mobile app
- In-match live tracking
- Auto-execution of chips (always confirm)
- Wildcard auto-rebuild (manual only)
