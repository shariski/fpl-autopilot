# Data Layer Foundation — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** Phase 0 (setup/scaffold) + Phase 1.1 (Data Layer), FPL API only
- **Slice goal:** `fpl-autopilot refresh` pulls real FPL data through a hardened client and lands it in SQLite, with tests that fail loudly if the API schema drifts.

This is the first build slice of FPL Autopilot. It is the foundation every later layer (Analytics → Decision → Interface) reads from. It deliberately stops at the Data Layer.

---

## 1. Context

The project is fully specified across `docs/` but has zero code. Per `architecture.md`, the system is a strict 4-layer stack (Data → Analytics → Decision → Interface) where each layer calls only the one below. Nothing computes until the Data Layer feeds real data upward, so the build order is bottom-up and this slice comes first.

Key constraints inherited from the docs:

- **`CLAUDE.md` B6** — the FPL API is unofficial. All calls go through a single client with retry, backoff, schema assertions, ≤1 req/s rate limit, and a realistic User-Agent. Schema assertions must **fail loudly** — silent drift is worse than a crash.
- **`CLAUDE.md` B13** — docs are the source of truth. Where this slice deviates from a doc, the doc is updated in the same change.
- **`risks.md` R1** — schema tests must be specific about field **types and presence**, not just "field exists."
- **`architecture.md`** — SQLite, single process, no ORM-style heaviness, minimalism.

## 2. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| First slice | Phase 0 + Data Layer (FPL API only) | Foundation everything depends on; independently verifiable. |
| Package layout | `src/` **is** the package | Matches `architecture.md` tree and `onboarding.md`'s `python -m src.scheduler`. |
| Env / deps | venv + pip, `requirements.txt` | Matches `onboarding.md` as written; no doc change needed. |
| HTTP client | `requests` (sync) | Simplest; data layer is sync batch work. |
| Storage access | raw `sqlite3` + `schema.sql`, no ORM | Matches `architecture.md` minimalism. |
| Response validation | Pydantic v2 models | Parsing + schema-assertion in one step; fails loud on drift (B6, R1). |
| FPL team ID | `3122849` | The user's real team, for the end-of-slice smoke test. |
| Understat/FBref | **Deferred** to next data-layer slice | Nothing consumes xG/xA until the Analytics layer; pulling it now adds scraping risk (R2) with no payoff yet. |

## 3. Scope

### In scope

- Python project scaffold (`pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore`, `config.yaml`).
- Reorganize the 8 existing spec docs into `docs/` to match every reference in the codebase (`README.md`, `CLAUDE.md`, `plan.md`, `architecture.md`). `CLAUDE.md` and `README.md` stay at root.
- SQLite DB with **all** tables from `architecture.md` created via `schema.sql` (Phase-2 tables created empty so we never migrate later).
- FPL API client covering: `bootstrap-static/`, `fixtures/`, `entry/{team_id}/`, `entry/{team_id}/event/{gw}/picks/`, `element-summary/{player_id}/`.
- Retry/backoff (1s → 5s → 30s), ≤1 req/s rate limiter, realistic User-Agent.
- Pydantic models for each response = the schema assertions.
- Repository layer: parsed models → tables (upsert).
- Cache/staleness logic: read DB first, fetch only when stale.
- `fpl-autopilot refresh` and `refresh --full` CLI commands.
- Tests: schema-assertion tests (with a deliberately-mutated fixture proving loud failure), repository round-trip tests, frozen JSON fixtures.

### Out of scope (this slice)

- Understat/FBref client + name resolution (next data-layer slice).
- Analytics (FDR, xP), Decision Layer, dashboard.
- APScheduler / scheduled jobs — only the **manual** `refresh` CLI here.
- Master password / credentials / encryption (Phase 2).
- Telegram, modes, deadguard, action executor (Phase 2).
- Other CLI commands (`status`, `freeze`, `log`, `config`, `init-*`) — stubs only if trivial, otherwise deferred.

## 4. File layout

```
fpl-autopilot/
├── README.md                      # root (unchanged)
├── CLAUDE.md                      # root (unchanged)
├── pyproject.toml                 # metadata + console script: fpl-autopilot = src.cli:main
├── requirements.txt               # requests, pydantic>=2, pyyaml, pytest
├── .env.example                   # PORT, HEALTHCHECK_URL, (TELEGRAM_BOT_TOKEN placeholder)
├── .gitignore                     # .venv, __pycache__, data/, .env, *.db
├── config.yaml                    # the schema already in product-spec.md
├── docs/
│   ├── product-spec.md  architecture.md  decision-engine.md  deadguard.md
│   ├── plan.md  risks.md  onboarding.md  runbook.md      # moved from root
│   └── superpowers/specs/2026-05-22-data-layer-foundation-design.md   # this file
├── data/                          # gitignored
│   ├── .gitkeep
│   └── logs/.gitkeep
├── src/
│   ├── __init__.py
│   ├── config.py                  # load config.yaml + .env
│   ├── cli.py                     # argparse: refresh [--full]
│   └── data/
│       ├── __init__.py
│       ├── fpl_client.py          # one method per endpoint, hardened
│       ├── models.py              # Pydantic response models (schema assertions)
│       ├── schema.sql             # all tables from architecture.md + cache_meta
│       ├── db.py                  # connect(), init_db()
│       ├── repository.py          # parsed models -> tables (upsert)
│       └── cache.py               # staleness check
└── tests/
    ├── conftest.py
    ├── fixtures/                  # frozen real API JSON samples
    │   ├── bootstrap-static.json  fixtures.json  entry.json  picks.json
    ├── test_models_schema.py      # incl. mutated-fixture loud-failure test
    ├── test_fpl_client.py         # mocked HTTP: retry/backoff/rate-limit
    └── test_repository.py         # round-trip into in-memory SQLite
```

## 5. Component specs

### 5.1 `src/data/fpl_client.py`

A single class `FPLClient` with one method per endpoint. Base URL `https://fantasy.premierleague.com/api/`.

| Method | Endpoint | Returns |
|---|---|---|
| `bootstrap_static()` | `bootstrap-static/` | `BootstrapStatic` model |
| `fixtures(event=None)` | `fixtures/` (`?event=` optional) | `list[Fixture]` |
| `entry(team_id)` | `entry/{team_id}/` | `Entry` model |
| `picks(team_id, gw)` | `entry/{team_id}/event/{gw}/picks/` | `EntryPicks` model |
| `element_summary(player_id)` | `element-summary/{player_id}/` | `ElementSummary` model |

Cross-cutting behavior (every request):

- **Rate limit:** enforce ≤1 req/s (sleep to maintain a minimum interval between requests).
- **Retry/backoff:** on connection error, timeout, `429`, or `5xx` → retry with delays `1s, 5s, 30s` (3 retries). On `4xx` other than 429 → raise immediately (no retry).
- **User-Agent:** a realistic browser UA string (per B6 — default `requests` UA is a flag).
- **Parse through Pydantic:** the raw JSON is fed to the matching model; a validation error is the schema-drift signal and propagates loudly.
- **Timeout:** explicit per-request timeout (e.g., 10s).

### 5.2 `src/data/models.py`

Pydantic v2 models with **tight types** (R1). Only the fields this slice persists need to be modeled strictly; unknown extra fields are ignored (additions don't break us — B6/R1 distinction between additions and changes). Models:

- `BootstrapStatic` → `events: list[Event]`, `teams: list[Team]`, `elements: list[Element]`, `element_types: list[ElementType]`.
- `Event` (gameweek): `id:int`, `name:str`, `deadline_time:datetime`, `is_current:bool`, `is_next:bool`, `finished:bool`.
- `Team`: `id:int`, `name:str`, `short_name:str`, `strength_attack_home:int`, `strength_attack_away:int`, `strength_defence_home:int`, `strength_defence_away:int`.
- `Element` (player): `id:int`, `first_name:str`, `second_name:str`, `web_name:str`, `team:int`, `element_type:int`, `now_cost:int`, `status:str`, `selected_by_percent:float`, `form:float`.
- `ElementType` (position): `id:int`, `singular_name_short:str` (GKP/DEF/MID/FWD).
- `Fixture`: `id:int`, `event:int|None`, `team_h:int`, `team_a:int`, `kickoff_time:datetime|None`, `finished:bool`, `team_h_score:int|None`, `team_a_score:int|None`.
- `Entry`: `id:int`, `name:str`, `player_first_name:str`, `player_last_name:str`, `summary_overall_points:int|None`, `summary_overall_rank:int|None`.
- `EntryPicks`: `active_chip:str|None`, `entry_history: EntryHistory`, `picks: list[Pick]`.
- `EntryHistory`: `event:int`, `bank:int`, `value:int` (both in tenths).
- `Pick`: `element:int`, `position:int`, `multiplier:int`, `is_captain:bool`, `is_vice_captain:bool`.
- `ElementSummary`: `history: list[...]`, `fixtures: list[...]` (modeled lightly; not persisted in this slice but the method/model exist for the next slice).

### 5.3 `src/data/schema.sql` + `db.py`

`init_db()` runs `schema.sql` (idempotent `CREATE TABLE IF NOT EXISTS`). Creates **all** tables from `architecture.md`: `players`, `teams`, `player_stats`, `fixtures`, `fdr`, `xp`, `my_team`, `gameweeks`, `activity_log`, `credentials`.

**Addition to the data model (must update `architecture.md` per B13):** a `cache_meta` table to drive staleness:

```sql
CREATE TABLE IF NOT EXISTS cache_meta (
  resource TEXT PRIMARY KEY,        -- 'bootstrap-static' | 'fixtures' | 'my_team' | ...
  last_fetched_utc TIMESTAMP NOT NULL
);
```

DB path from `config.yaml`/default `data/fpl_autopilot.db`. `db.py` exposes `connect()` (row factory = dict-like) and `init_db()`.

### 5.4 `src/data/repository.py`

Upsert functions, one per resource:

- `upsert_teams(teams)` → `teams`.
- `upsert_players(elements, element_types)` → `players`. Map: `name = first_name + ' ' + second_name`; `team_id = team`; `position` via `element_types[element_type].singular_name_short`; `price = now_cost / 10.0`; `ownership = selected_by_percent`; `status`, `form`, `updated_at = now`.
- `upsert_gameweeks(events)` → `gameweeks`. State machine fields default: `state = 'PENDING'`, `last_*_at = NULL`. (Architecture defines these; this slice only sets the data fields + default state.)
- `upsert_fixtures(fixtures)` → `fixtures`. Map `gw = event`, `home_team_id = team_h`, etc.
- `snapshot_my_team(team_id, gw, picks)` → `my_team`. `picks_json` = serialized picks (element/position/multiplier/captain flags), `bank = entry_history.bank/10`, `team_value = entry_history.value/10`, `chips_used_json` from `active_chip`.

### 5.5 `src/data/cache.py`

- `is_stale(resource, max_age) -> bool` reads `cache_meta`. Concrete default TTLs (testable): `bootstrap-static` 6h, `fixtures` 6h, `my_team` 1h. (These mirror `architecture.md`'s cadence — bootstrap ~once/GW, status hourly — while keeping the manual `refresh` cheap.)
- `mark_fetched(resource)` writes `last_fetched_utc = now`.
- `refresh` reads DB first, fetches only stale resources; `refresh --full` ignores cache and fetches everything.

### 5.6 `src/cli.py` + `src/config.py`

- `config.py`: load `config.yaml` (mode/thresholds/etc. — only the data-relevant bits used now) and `.env` (PORT etc.). Team ID read from config (`3122849`).
- `cli.py`: `fpl-autopilot refresh` (incremental, cache-aware) and `fpl-autopilot refresh --full` (force). Prints a summary line per resource (player/team/fixture counts, squad size) mirroring `onboarding.md` Step 5.

## 6. Known limitations (public-API-only, surfaced honestly)

These are real gaps between `architecture.md`'s `my_team` schema and what the **public** API returns. They are filled by the authenticated endpoints in Phase 2; this slice persists `NULL`/proxy and documents it:

- **`free_transfers`** — not available from public `picks/`. Stored `NULL` this slice.
- **Selling price per player** — requires authenticated `my-team/{id}/`. `picks_json` stores positions/multipliers/captain flags only; selling price added in Phase 2.

To record per B4/B13, a one-line note will be added to `architecture.md`'s `my_team` table.

## 7. Testing strategy (`CLAUDE.md` B11, B14)

- **`test_models_schema.py`** — parse each frozen fixture into its model (must pass). Then a **mutated** fixture (e.g., `now_cost` → string, or `id` removed) must raise `ValidationError`. This is the loud-failure guarantee for R1.
- **`test_fpl_client.py`** — mock the HTTP layer: assert retry sequence on 5xx/timeout, no-retry on 404, rate-limit spacing, UA header present.
- **`test_repository.py`** — round-trip: parse fixture → upsert into in-memory SQLite → query back → assert mapped values (price/10, position string, team mapping).

## 8. Definition of done (this slice)

1. `pytest` green, including the mutated-fixture loud-failure test.
2. `fpl-autopilot refresh --full` populates `players` (600+), `teams` (20), `fixtures` (~380), `gameweeks` (38), and `my_team` for team `3122849` (a 15-row picks snapshot), verified by querying the DB.
3. Docs reorganized into `docs/`; `architecture.md` updated for `cache_meta` and the `my_team` public-API note.
4. `requirements.txt` installs cleanly into a fresh venv.

## 9. Risks specific to this slice

- **End-of-season timing (2026-05-22):** the 2025/26 season just finished; the API still serves finished-season data, which is fine for building/validating. A summer rollover to 2026/27 will reset player IDs (`risks.md` D5) — out of scope, noted.
- **R1 (schema drift):** mitigated by Pydantic + the mutated-fixture test.
- **`my_team` public gaps:** documented in §6, not a blocker.
