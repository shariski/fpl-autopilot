# Plan

Flat task list, grouped by phase. No day-by-day, no time estimates. Tasks within a phase can be done in any order unless a dependency is noted. Each task is a checkpoint.

Legend:
- 🔰 First-time risk: requires research or verification before implementing.
- ⚠️ Unverified assumption: documented assumption that must be validated empirically.

---

## Phase 0 — Setup

- [ ] Initialize git repo with `README.md`, `CLAUDE.md`, `docs/`.
- [ ] Create Python project scaffold (FastAPI, SQLite, pytest).
- [ ] Set up `.env.example` template with: Telegram bot token placeholder, port, healthcheck URL placeholder.
- [ ] Create initial `config.yaml` matching the schema in `product-spec.md`.
- [ ] Initialize SQLite DB and apply all schemas from `architecture.md`.
- [ ] Set up structured logging to file (`data/logs/`) with rotation.
- [ ] Set up external healthcheck endpoint (UptimeRobot or Healthchecks.io account).
- [ ] Implement `fpl-autopilot` CLI entrypoint.
- [ ] Implement `init-master-password` command (Argon2id key derivation, salt persistence, password-manager warning).
- [ ] Implement `config` command (set / get / list).
- [ ] Implement `status` command (mode, last action, next scheduled job).
- [ ] Implement `freeze` / `unfreeze` commands.
- [ ] Implement `log` command (--tail, --gw filters).
- [ ] Implement persistent dashboard banner system for incomplete setup states (Telegram missing, session expired, stale data, dry-run on).
- [ ] Write `docs/onboarding.md` (done — present file).
- [ ] Write `docs/runbook.md` (done — present file).

---

## Phase 1 — Insight Engine (read-only)

**Goal:** the user can open a dashboard one hour before deadline and within five minutes know who to captain, what transfer is worth making, and whether a chip is in play.

**Exit criteria:** the dashboard shows squad with xP per player, top 5 captain options with reasoning, top 3 transfer ideas with EP delta, 5-GW fixture planner grid, and chip flags when triggered.

### Phase 1.1 — Data Layer

- [ ] 🔰 Build FPL API client module. Endpoints: `bootstrap-static/`, `fixtures/`, `entry/{team_id}/`, `entry/{team_id}/event/{gw}/picks/`, `element-summary/{player_id}/`.
- [ ] Add schema-assertion tests for `bootstrap-static` response. Fail loudly on drift.
- [ ] Add retry / backoff / rate limit (≤ 1 req/sec) to the client.
- [ ] Persist FPL data to `players`, `teams`, `fixtures`, `gameweeks` tables.
- [ ] 🔰 Build Understat client to fetch xG / xA per player per GW. Library `understat` is a candidate.
- [ ] ⚠️ Validate that Understat data lines up with FPL player ids. There is no shared key — match on name+team. Build a name-resolution table.
- [ ] Persist supplementary stats to `player_stats` table with source tagging.
- [ ] Build initial cache logic: read from DB first, fetch from API only when stale.
- [ ] Set up Tuesday-morning data refresh job in APScheduler.
- [ ] Set up hourly status-flag refresh.
- [ ] Implement `init-fpl` CLI command (login validation, team confirmation, encrypted credential storage).
- [ ] Implement `refresh` and `refresh --full` CLI commands.

### Phase 1.2 — Analytics

- [ ] Implement custom FDR computation per `decision-engine.md`. Output to `fdr` table.
- [ ] Implement xP model v1 per `decision-engine.md`. Output to `xp` table with `model_version = "v1"`.
- [ ] Implement form-adjusted delta metric.
- [ ] Write deterministic tests for FDR with frozen team-stats inputs.
- [ ] Write deterministic tests for xP v1 with frozen player + fixture inputs.
- [ ] Compute xP for next 6 GW for every player on every data refresh.

### Phase 1.3 — Decision Layer

- [ ] Implement captain ranker per `decision-engine.md`. Output top 5 with reasoning strings.
- [ ] Implement transfer engine: sell candidates, buy candidates, EP delta, hit calculator. Top 3 output.
- [ ] Implement chip recommender (flag only, no execution).
- [ ] Write tests for transfer engine:
  - [ ] 3-per-club rule enforcement
  - [ ] Budget constraint
  - [ ] Hit threshold logic
  - [ ] Property test: every suggested transfer leaves a valid squad
- [ ] Persist all decisions to `activity_log` even though Phase 1 doesn't execute.

### Phase 1.4 — Interface (Dashboard)

- [ ] Initialize SvelteKit project. PWA support via `@vite-pwa/sveltekit`.
- [ ] Build dashboard layout per `product-spec.md`:
  - [ ] Header with GW info, deadline countdown, system status.
  - [ ] My team pitch view with xP per player.
  - [ ] Captain pick section.
  - [ ] Transfer ideas section.
  - [ ] Chip recommendation (conditional).
  - [ ] Fixture planner grid.
  - [ ] Activity log view.
- [ ] PWA manifest, service worker, icons. Verify installable on iOS Safari and Android Chrome.
- [ ] Mobile-responsive layout (mobile-first; desktop is secondary).
- [ ] Connect dashboard to backend via FastAPI endpoints.

### Phase 1 Done When

- [ ] Open dashboard one hour before a real deadline.
- [ ] In under five minutes, the user can list: captain pick, transfer (if any), chip status.
- [ ] All data is fresh as of the latest scheduled refresh.

---

## Phase 2 — Decision Automation

**Goal:** the user can switch to Auto, Manual, or Hybrid mode, and can be absent for a gameweek without losing position.

**Exit criteria:** all of: session-based execution works without manual re-auth weekly, Auto mode executes captain/bench/transfer end-to-end, Telegram one-tap confirmation works, deadguard activates and executes within its scope, dry-run mode produces sensible decisions for 3 consecutive GWs.

### Phase 2.1 — Auth & Session

- [ ] 🔰 Implement FPL login flow against `users.premierleague.com/accounts/login/`.
- [ ] Capture and store session cookie + CSRF token, encrypted with key derived from master password.
- [ ] Add session-expiry detection (401/403 → mark expired).
- [ ] Add automatic re-login on session expiry.
- [ ] Add "session frozen after 2 consecutive re-login failures" safeguard.
- [ ] Test: encrypt cookie, kill process, restart, decrypt, make authenticated request successfully.

### Phase 2.2 — Action Executor

- [ ] Implement POST to `/api/transfers/`. Verify response codes.
- [ ] Implement POST to `/api/my-team/{team_id}/` for captain/vice/bench changes.
- [ ] Wrap all authenticated POSTs in a single Action Executor module with retry / backoff.
- [ ] Add idempotency: don't re-submit the same transfer twice in a window.
- [ ] Log every authenticated request (method, endpoint, response code) — redacted, no cookies.

### Phase 2.3 — Mode Router

- [ ] Implement `mode` field in `config.yaml`: auto / manual / hybrid.
- [ ] Build Mode Router: routes each decision to execute / notify / wait per `decision-engine.md`.
- [ ] Confidence scoring per `decision-engine.md`. Auto falls back to notify if confidence < 70.
- [ ] Hard caps in Auto mode: max 2 transfers/GW, max -4 hit/GW.
- [ ] Lock window: H-2 to deadline, freeze all auto changes outside deadguard.

### Phase 2.4 — Telegram Bot

- [ ] Set up bot via BotFather. Token in env var.
- [ ] Implement notification templates per `product-spec.md`:
  - [ ] H-24 preview (with/without transfer)
  - [ ] H-2 reminder
  - [ ] Chip preview
  - [ ] Deadguard warning
  - [ ] Deadguard executed summary
- [ ] Inline buttons with callback handlers: confirm, reject, modify, see alternatives.
- [ ] Persist notification state: which notif sent, which acked, when.
- [ ] Add "freeze auto mode" command.
- [ ] Add "unfreeze" command.
- [ ] Add "status" command (current mode, last action, next scheduled job).
- [ ] Implement `init-telegram` CLI command with /start polling for chat ID detection and end-to-end inline button verification.

### Phase 2.5 — Deadguard

- [ ] Implement gameweek state machine per `deadguard.md`.
- [ ] Define and persist "user has acted" event triggers.
- [ ] Add "I've reviewed, keep as is" button to dashboard + Telegram.
- [ ] Implement deadguard trigger window (H-30, configurable).
- [ ] Implement warning window (H-120, configurable).
- [ ] Deadguard scope enforcement per `deadguard.md` (forbidden actions hard-coded as forbidden).
- [ ] Deadguard logging with explicit reason for activation.
- [ ] Post-execution notification, always sent (including DEADGUARD_SKIPPED case).
- [ ] Late-news re-evaluation logic (between execution and deadline).

### Phase 2.6 — Dry-Run Mode

- [ ] Add `dry_run: true` flag to config.
- [ ] When set, Action Executor short-circuits all writes but logs what would have been written.
- [ ] Dashboard shows "DRY RUN" banner when active.
- [ ] Activity log marks dry-run entries clearly.
- [ ] Implement `dry-run report --gw <N>` CLI command with side-by-side comparison format per `onboarding.md`.
- [ ] First-live-execution one-time confirmation flow (extra Telegram confirm on first action after mode change to auto).
- [ ] ⚠️ Validate: run dry-run for 3 consecutive GWs before switching to live Auto. Compare against user's manual choices.

### Phase 2.7 — Emergency Override

- [ ] Single command to freeze all automation. Persists across restarts.
- [ ] Single command to undo a transfer (before deadline only).
- [ ] Implement `fpl-autopilot undo --gw <N> --action transfer` CLI command (reverses last transfer if before deadline).
- [ ] Add `init-fpl --reauth` flag to refresh session without re-entering email.
- [ ] All overrides logged to activity log.

### Phase 2 Done When

- [ ] User connects FPL account once, system maintains session for weeks without manual intervention.
- [ ] In Auto mode, captain/vice/bench/transfer execute end-to-end without user input.
- [ ] In Manual mode, every action requires one-tap Telegram confirmation.
- [ ] Deadguard ran in 3 dry-run GWs and made reasonable decisions.
- [ ] Activity log is complete and auditable.
- [ ] Emergency freeze works and blocks all subsequent auto actions until unfrozen.

---

## Phase 3 — AI Layer

**Goal:** decisions are accompanied by natural-language reasoning, the system understands the user's mini-league context, and personalization is based on observed user behavior.

**Exit criteria:** TBD — Phase 3 spec to be expanded in a future session.

### Placeholder tasks

- [ ] Integrate LLM (Claude API or local) into reasoning layer.
- [ ] Replace template-string reasoning with LLM-generated explanations grounded in actual numbers.
- [ ] Add conversational query interface: "why are you recommending X?"
- [ ] Pull mini-league standing data and incorporate into recommendations (template vs differential strategy).
- [ ] Build user-history analyzer: identify risk preference, captain preference patterns.
- [ ] Add scenario simulator: "what if I wildcard now vs in 3 weeks?"
- [ ] Cache LLM reasoning per GW to control cost.

This phase is intentionally under-detailed. Detail it after Phase 2 is operational and the system has accumulated real activity-log data.

---

## Always-on tasks (cross-phase)

- [ ] Keep `docs/` in sync with code. When code disagrees with docs, the doc is the source of truth until explicitly updated.
- [ ] Maintain a `CHANGELOG.md` at the repo root summarizing each substantive change.
- [ ] Review the `activity_log` weekly. Mark decisions that turned out wrong and note why.
- [ ] Run schema-assertion tests against the live FPL API at least weekly.
- [ ] Back up the SQLite DB before any schema migration.

---

## Risk flags to revisit

These are documented in `docs/risks.md`. The plan above does not yet account for them being wrong — if any is wrong, the plan changes.

- ⚠️ R1: FPL API schema stability across the season.
- ⚠️ R2: Understat / FBref scraping reliability.
- ⚠️ R3: Auto-execution legality / risk of account flag.
- ⚠️ R4: xP model accuracy (the v1 model is a starting point, not validated).
- ⚠️ R5: Session cookie longevity (assumed weeks, actual unknown).
- ⚠️ R6: Deadguard correctness on edge cases (late news, partial failure mid-execution).
