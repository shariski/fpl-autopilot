# Architecture

System architecture, stack reasoning, data model, and scheduling. Read this before deciding where new code belongs.

## High-level layers

```
┌─────────────────────────────────────────────┐
│  Interface Layer                            │
│   PWA dashboard (Phase 1+)                  │
│   Telegram bot (Phase 2+)                   │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Decision Layer                             │
│   Captain ranker                            │
│   Transfer engine                           │
│   Chip recommender                          │
│   Mode router (Phase 2+)                    │
│   Deadguard (Phase 2+)                      │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Analytics Layer                            │
│   FDR computer                              │
│   xP model                                  │
│   Form metrics                              │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Data Layer                                 │
│   FPL API client                            │
│   Understat / FBref client                  │
│   Action executor (Phase 2+)                │
│   Cache (SQLite)                            │
└─────────────────────────────────────────────┘
```

Rules:

- Each layer can only call the layer immediately below it.
- The Interface layer must never compute. It displays and accepts input.
- The Analytics layer must never make external network calls.
- The Decision layer must never query the cache directly.

These are not just style preferences. They make it possible to add Phase 3's LLM agent without untangling cross-cutting concerns.

## Stack rationale

**Working defaults. Confirm with the user before locking in.**

### Backend: Python with FastAPI

- Python has the strongest FPL / football data ecosystem (Understat, fpl-mcp, fpl libraries).
- FastAPI is async, lightweight, easy to deploy.
- The user has stated they are comfortable building "fully with AI" — Python's verbosity helps LLM code generation produce maintainable output more often than denser languages.

### Storage: SQLite

- Single-user, low write volume.
- Avoid the operational overhead of Postgres for a personal tool.
- Backup is `cp file.db file.db.bak`.

### Frontend: SvelteKit PWA

- Web first. Installable to home screen for a near-native feel on mobile.
- SvelteKit chosen for terse syntax that produces cleaner output when building with AI assistance.
- PWA setup via `@vite-pwa/sveltekit` or manual service worker + manifest.
- No native iOS / Android. Stated as out of scope.

### Notifications: Telegram bot

- Free, real-time, supports inline buttons (essential for one-tap confirmation).
- Bot libraries are mature in Python (`python-telegram-bot`, `aiogram`).
- Falls back to email if Telegram is unavailable.

### Scheduling: APScheduler

- In-process scheduler. Simpler than running a separate cron daemon.
- Persistent job store via SQLite so jobs survive process restart.
- Cron syntax supported for those who want it.

### Hosting: deferred

Three options on the table, decision deferred until closer to deployment:

- **VPS (~$5/mo):** predictable uptime, accessible from anywhere, requires SSH discipline.
- **Home server:** free, full control, requires reliable home network for deadline-critical jobs.
- **Hybrid:** home as primary with VPS as failover. More complex to operate.

Each has tradeoffs around cost, reliability around deadlines, and operational overhead. The decision is not blocking Phase 1 development.

**Tracked in:** `docs/risks.md` D3.

## Data model

SQLite schema. Tables only — indexes and constraints inferred.

### `players`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | FPL player id |
| name | TEXT | |
| web_name | TEXT | Short display name |
| team_id | INTEGER | FK to teams |
| position | TEXT | GK / DEF / MID / FWD |
| price | REAL | Current price |
| status | TEXT | a (available), d (doubt), i (injured), s (suspended), u (unavailable) |
| ownership | REAL | % |
| form | REAL | FPL's form value (kept for reference, not used by decision engine) |
| updated_at | TIMESTAMP | |

### `teams`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| name | TEXT | |
| short_name | TEXT | |
| strength_attack_home | INTEGER | FPL value |
| strength_attack_away | INTEGER | |
| strength_defence_home | INTEGER | |
| strength_defence_away | INTEGER | |

### `player_stats`

One row per (player, gw, source).

| Column | Type | Notes |
|---|---|---|
| player_id | INTEGER | |
| gw | INTEGER | |
| source | TEXT | "fpl" / "understat" / "fbref" |
| minutes | INTEGER | |
| goals | INTEGER | |
| assists | INTEGER | |
| xg | REAL | |
| xa | REAL | |
| bonus | INTEGER | |
| total_points | INTEGER | FPL points actually earned |
| (PK) | (player_id, gw, source) | |

### `fixtures`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| gw | INTEGER | |
| home_team_id | INTEGER | |
| away_team_id | INTEGER | |
| kickoff_utc | TIMESTAMP | |
| finished | BOOLEAN | |
| home_score | INTEGER | nullable until played |
| away_score | INTEGER | nullable until played |

### `fdr` (computed)

| Column | Type | Notes |
|---|---|---|
| team_id | INTEGER | |
| gw | INTEGER | |
| fdr_attack | INTEGER | 1-5 |
| fdr_defense | INTEGER | 1-5 |
| computed_at | TIMESTAMP | |
| (PK) | (team_id, gw) | |

### `xp` (computed)

| Column | Type | Notes |
|---|---|---|
| player_id | INTEGER | |
| gw | INTEGER | |
| model_version | TEXT | "v1" |
| xp | REAL | |
| xminutes | REAL | |
| xgoals | REAL | |
| xassists | REAL | |
| xcs | REAL | |
| computed_at | TIMESTAMP | |
| (PK) | (player_id, gw, model_version) | |

### `my_team`

One row per gameweek snapshot.

| Column | Type | Notes |
|---|---|---|
| gw | INTEGER PRIMARY KEY | |
| picks_json | TEXT | JSON: 15 players with selling price, multiplier, position |
| bank | REAL | |
| team_value | REAL | |
| free_transfers | INTEGER | |
| chips_used_json | TEXT | JSON array of chip names |
| snapshot_at | TIMESTAMP | |

### `gameweeks`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| name | TEXT | "Gameweek 26" |
| deadline_utc | TIMESTAMP | |
| is_current | BOOLEAN | |
| is_next | BOOLEAN | |
| finished | BOOLEAN | |
| state | TEXT | PENDING / USER_ACTED / SYSTEM_ACTED / DEADGUARD_ACTIVE / DEADGUARD_EXECUTED / DEADGUARD_SKIPPED |
| last_user_action_at | TIMESTAMP | |
| last_system_action_at | TIMESTAMP | |
| deadguard_triggered_at | TIMESTAMP | nullable |

### `activity_log`

Append-only.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | |
| ts_utc | TIMESTAMP | |
| gw | INTEGER | |
| mode | TEXT | auto / manual / hybrid / deadguard |
| decision_type | TEXT | captain / transfer / bench / chip |
| action_taken | TEXT | |
| inputs_json | TEXT | xP values, FDR, confidence |
| alternatives_json | TEXT | other options considered |
| executed | BOOLEAN | |
| exec_outcome_json | TEXT | nullable until GW settles |

### `credentials` (Phase 2)

Single row.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | always 1 |
| fpl_email_encrypted | BLOB | |
| fpl_password_encrypted | BLOB | |
| session_cookie_encrypted | BLOB | |
| csrf_token_encrypted | BLOB | |
| session_last_refreshed | TIMESTAMP | |

## Scheduling

Jobs run by APScheduler. Times below are illustrative — real times depend on deadline of each gameweek.

```
Every hour:
  - Refresh player status (injury, suspension, doubt flags).
  - Refresh prices, watch for price changes.

Tuesday 03:00 UTC (post-GW settle):
  - Refresh bootstrap-static.
  - Refresh fixtures.
  - Refresh player stats (Understat, FBref).
  - Recompute FDR for next 6 GW.
  - Recompute xP for all players for next 6 GW.
  - Generate transfer suggestions for next GW.
  - Reset gameweek state to PENDING.

H-48 hours before deadline:
  - Generate chip recommendations.
  - Send chip preview notification if any chip condition is flagged.

H-24 hours:
  - Finalize transfer & captain recommendations.
  - Send preview notification.

H-6 hours:
  - Re-scan for late team news.
  - Re-evaluate recommendations.

H-2 hours:
  - LOCK auto-modifications outside of deadguard.
  - Send final reminder.
  - In Auto/Hybrid mode: execute pending captain/bench actions.

H-120 minutes (Phase 2):
  - If state is still PENDING, send deadguard warning notification.

H-30 minutes (Phase 2):
  - If state is still PENDING, run deadguard.
```

## Security

- All credentials encrypted at rest using a key derived from a master password (Argon2id, default parameters).
- Master password never persisted. Held in memory only during decryption. Must be supplied at process start.
- Session cookies encrypted the same way.
- Logs sanitized: no full cookies, no passwords, no email addresses in plaintext logs.
- Telegram bot token stored in environment variable, not in DB or config file.
- API key for any third-party LLM (Phase 3) in environment variable.

## Failure handling

- **FPL API down:** retry with backoff (1s, 5s, 30s). After 3 failures, alert user. If during H-2 to deadline window: alert urgently.
- **Understat/FBref scraping fails:** skip the refresh, use last known data, log staleness. xP confidence drops accordingly.
- **DB write fails:** halt and alert. Better to skip a gameweek than corrupt state.
- **Telegram notification fails:** retry, fall back to email, fall back to dashboard banner only.
- **Scheduler missed a job:** detected via external healthcheck (UptimeRobot or Healthchecks.io). Alert if a scheduled run did not check in.

## Deployment

Single Python process. Dockerfile optional but recommended.

```
fpl-autopilot/
├── docker-compose.yml          # optional, single service
├── .env                        # master password, telegram token
├── data/
│   ├── fpl_autopilot.db        # SQLite
│   └── logs/
├── src/
│   ├── data/
│   ├── analytics/
│   ├── decisions/
│   ├── interface/
│   └── scheduler.py
```

External healthcheck pings the scheduler endpoint after every scheduled job. If a ping is missed, alert.

## What this architecture deliberately does not have

- No microservices. Single process.
- No message queue. APScheduler is sufficient for the cadence.
- No Redis. SQLite handles cache and persistence.
- No CDN. The PWA is small enough.
- No external observability stack. Logs to disk + structured log file is sufficient.

These would be the right answers for a multi-tenant product. They are wrong answers for a single-user personal tool.
