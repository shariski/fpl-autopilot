# Data Layer Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** `fpl-autopilot refresh` pulls real FPL data through a hardened client and lands it in SQLite, with tests that fail loudly if the API schema drifts.

**Architecture:** Bottom layer of the documented 4-layer stack (`docs/architecture.md`). A single `FPLClient` wraps the unofficial FPL API with retry/backoff/rate-limit and Pydantic parsing (parsing == schema assertion). A repository layer upserts parsed models into a SQLite DB whose schema mirrors `docs/architecture.md` exactly. A cache layer makes `refresh` read-DB-first. Everything is sync batch work driven by a CLI.

**Tech Stack:** Python 3.11+, `requests`, Pydantic v2, raw `sqlite3` + `schema.sql`, `pyyaml`, `pytest`. venv + pip. `src/` is the importable package (per docs).

**Spec:** `docs/superpowers/specs/2026-05-22-data-layer-foundation-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, `fpl-autopilot` console script. |
| `requirements.txt` | Documented pip install path. |
| `.gitignore`, `.env.example`, `config.yaml` | Project config + secrets template. |
| `src/__init__.py`, `src/config.py`, `src/cli.py` | Package root, config loader, CLI entrypoint. |
| `src/data/fpl_client.py` | The hardened FPL API client. |
| `src/data/models.py` | Pydantic response models = schema assertions. |
| `src/data/schema.sql` | All tables from `architecture.md` + `cache_meta`. |
| `src/data/db.py` | `connect()`, `init_db()`. |
| `src/data/repository.py` | Parsed models → tables (upsert). |
| `src/data/cache.py` | Staleness check + `mark_fetched`. |
| `tests/conftest.py` | Shared `load` + `db` fixtures. |
| `tests/fixtures/*.json` | Frozen real API responses. |
| `tests/test_*.py` | Per-module tests. |

---

## Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.gitignore`, `.env.example`, `config.yaml`
- Create: `src/__init__.py`, `src/data/__init__.py`, `data/.gitkeep`, `data/logs/.gitkeep`

- [x] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "fpl-autopilot"
version = "0.1.0"
description = "Personal Fantasy Premier League assistant"
requires-python = ">=3.11"
dependencies = ["requests", "pydantic>=2", "pyyaml"]

[project.optional-dependencies]
dev = ["pytest"]

[project.scripts]
fpl-autopilot = "src.cli:main"

[tool.setuptools]
packages = ["src", "src.data"]
```

- [x] **Step 2: Create `requirements.txt`**

```
requests
pydantic>=2
pyyaml
pytest
```

- [x] **Step 3: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
*.db
.env
data/logs/*.log
.serena/
.code-review-graph/
*.egg-info/
```

- [x] **Step 4: Create `.env.example`**

```
PORT=8000
HEALTHCHECK_URL=
TELEGRAM_BOT_TOKEN=
```

- [x] **Step 5: Create `config.yaml`** (product-spec schema + `fpl`/`storage` for this slice)

```yaml
fpl:
  team_id: 3122849

storage:
  db_path: data/fpl_autopilot.db

mode:
  current: manual  # auto | manual | hybrid

thresholds:
  min_ep_delta_for_transfer: 2.0
  min_ep_delta_for_hit_minus4: 4.0
  confidence_floor: 70
  max_transfers_per_gw_auto: 2
  max_hit_per_gw_auto: 4

deadguard:
  enabled: true
  warning_window_minutes: 120
  trigger_window_minutes: 30
  scope:
    captain_vice: true
    bench_order: true
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

- [x] **Step 6: Create package skeleton files**

Create empty `src/__init__.py` and `src/data/__init__.py`. Create empty `data/.gitkeep` and `data/logs/.gitkeep`.

```bash
mkdir -p src/data data/logs
touch src/__init__.py src/data/__init__.py data/.gitkeep data/logs/.gitkeep
```

- [x] **Step 7: Create venv and install editable**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```
Expected: installs requests, pydantic, pyyaml, pytest, and the `fpl-autopilot` script without error.

- [x] **Step 8: Verify package imports and script exists**

Run:
```bash
.venv/bin/python -c "import src, src.data; print('ok')"
.venv/bin/fpl-autopilot --help || true
```
Expected: prints `ok`. The `fpl-autopilot --help` will error (no CLI yet) — that's fine at this step; we only confirm the entry point is wired.

- [x] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt .gitignore .env.example config.yaml src data/.gitkeep data/logs/.gitkeep
git commit -m "chore: project scaffold for data-layer foundation"
```

---

## Task 2: Reorganize docs + update architecture.md

The codebase references `docs/` everywhere but the 8 spec docs sit at root. Move them, then record this slice's two data-model deltas in `architecture.md` (per `CLAUDE.md` B13: doc updated in the same change).

**Files:**
- Move: 8 root `.md` files → `docs/`
- Modify: `docs/architecture.md` (add `cache_meta`, annotate `my_team`)

- [x] **Step 1: Move spec docs into `docs/`** (keep `CLAUDE.md` and `README.md` at root)

```bash
git mv architecture.md deadguard.md decision-engine.md onboarding.md plan.md product-spec.md risks.md runbook.md docs/
```

- [x] **Step 2: Add `cache_meta` to the data model in `docs/architecture.md`**

After the `### activity_log` table section (before `### credentials (Phase 2)`), insert:

```markdown
### `cache_meta`

Drives "read DB first, fetch only when stale" in the Data Layer.

| Column | Type | Notes |
|---|---|---|
| resource | TEXT PRIMARY KEY | "bootstrap-static" / "fixtures" / "my_team" |
| last_fetched_utc | TIMESTAMP | NOT NULL |

```

- [x] **Step 3: Annotate the `my_team` public-API limitation in `docs/architecture.md`**

In the `### my_team` table, change the `free_transfers` row's Notes and the `picks_json` row's Notes to record the public-API gap. Replace:

```markdown
| picks_json | TEXT | JSON: 15 players with selling price, multiplier, position |
```
with:
```markdown
| picks_json | TEXT | JSON: 15 players (element, position, multiplier, captain flags). Per-player **selling price is auth-only** (Phase 2); omitted under public API. |
```
and replace:
```markdown
| free_transfers | INTEGER | |
```
with:
```markdown
| free_transfers | INTEGER | Auth-only (Phase 2). NULL under public-API refresh. |
```

- [x] **Step 4: Verify docs moved and references resolve**

Run:
```bash
ls docs/*.md
git status --short
```
Expected: 8 docs now under `docs/`; `README.md` link `docs/plan.md` and `CLAUDE.md` ref `docs/decision-engine.md` now point to real paths.

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: move specs into docs/, record cache_meta + my_team public-API note"
```

---

## Task 3: Capture frozen API fixtures

Tests assert against real API shapes. Capture them once with `curl`.

**Files:**
- Create: `tests/fixtures/bootstrap-static.json`, `fixtures.json`, `entry.json`, `picks.json`

- [x] **Step 1: Create fixtures dir**

```bash
mkdir -p tests/fixtures
```

- [x] **Step 2: Capture real responses** (realistic UA, per B6)

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
B="https://fantasy.premierleague.com/api"
curl -s -A "$UA" "$B/bootstrap-static/" -o tests/fixtures/bootstrap-static.json
sleep 1
curl -s -A "$UA" "$B/fixtures/" -o tests/fixtures/fixtures.json
sleep 1
curl -s -A "$UA" "$B/entry/3122849/" -o tests/fixtures/entry.json
sleep 1
# Determine the latest finished GW id, then capture that GW's picks:
GW=$(.venv/bin/python -c "import json; e=json.load(open('tests/fixtures/bootstrap-static.json'))['events']; fin=[x['id'] for x in e if x['finished']]; print(max(fin) if fin else max(x['id'] for x in e))")
curl -s -A "$UA" "$B/entry/3122849/event/$GW/picks/" -o tests/fixtures/picks.json
echo "captured picks for GW $GW"
```

- [x] **Step 3: Verify fixtures are valid JSON with expected shape**

Run:
```bash
.venv/bin/python -c "import json; bs=json.load(open('tests/fixtures/bootstrap-static.json')); print('players', len(bs['elements']), 'teams', len(bs['teams']), 'events', len(bs['events']))"
.venv/bin/python -c "import json; print('fixtures', len(json.load(open('tests/fixtures/fixtures.json'))))"
.venv/bin/python -c "import json; p=json.load(open('tests/fixtures/picks.json')); print('picks', len(p['picks']))"
```
Expected: players 500+, teams 20, events 38, fixtures 300+, picks 15.

If `picks` is not 15 (e.g. team didn't play that GW), pick the previous finished GW and re-capture picks.json.

- [x] **Step 4: Commit**

```bash
git add tests/fixtures
git commit -m "test: capture frozen FPL API fixtures"
```

---

## Task 4: Pydantic response models

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_models_schema.py`
- Create: `src/data/models.py`

- [x] **Step 1: Create `tests/conftest.py`** (shared fixtures used by this and later tasks)

```python
import json
import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def load():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())
    return _load


@pytest.fixture
def db():
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()
```

- [x] **Step 2: Write the failing tests** in `tests/test_models_schema.py`

```python
import pytest
from pydantic import ValidationError
from src.data.models import BootstrapStatic, Fixture, EntryPicks, Entry


def test_bootstrap_parses(load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    assert len(bs.elements) > 500
    assert len(bs.teams) == 20
    assert {et.singular_name_short for et in bs.element_types} >= {"GKP", "DEF", "MID", "FWD"}


def test_fixtures_parse(load):
    fixtures = [Fixture.model_validate(f) for f in load("fixtures.json")]
    assert len(fixtures) > 300


def test_entry_parses(load):
    entry = Entry.model_validate(load("entry.json"))
    assert entry.id == 3122849


def test_picks_parse(load):
    picks = EntryPicks.model_validate(load("picks.json"))
    assert len(picks.picks) == 15
    assert picks.entry_history.bank >= 0


def test_schema_drift_fails_loudly(load):
    data = load("bootstrap-static.json")
    del data["elements"][0]["id"]  # required field removed -> must raise
    with pytest.raises(ValidationError):
        BootstrapStatic.model_validate(data)
```

- [x] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.models'`.

- [x] **Step 4: Write `src/data/models.py`**

```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    # Unknown extra fields are ignored: API *additions* must not break us,
    # but *renames/retypes/removals* of modeled fields raise loudly (R1).
    model_config = ConfigDict(extra="ignore")


class Event(_Base):
    id: int
    name: str
    deadline_time: datetime
    is_current: bool
    is_next: bool
    finished: bool


class Team(_Base):
    id: int
    name: str
    short_name: str
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int


class Element(_Base):
    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    element_type: int
    now_cost: int
    status: str
    selected_by_percent: float
    form: float


class ElementType(_Base):
    id: int
    singular_name_short: str


class BootstrapStatic(_Base):
    events: list[Event]
    teams: list[Team]
    elements: list[Element]
    element_types: list[ElementType]


class Fixture(_Base):
    id: int
    event: int | None
    team_h: int
    team_a: int
    kickoff_time: datetime | None
    finished: bool
    team_h_score: int | None
    team_a_score: int | None


class Entry(_Base):
    id: int
    name: str
    player_first_name: str
    player_last_name: str
    summary_overall_points: int | None
    summary_overall_rank: int | None


class EntryHistory(_Base):
    event: int
    bank: int
    value: int


class Pick(_Base):
    element: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


class EntryPicks(_Base):
    active_chip: str | None
    entry_history: EntryHistory
    picks: list[Pick]


class ElementSummary(_Base):
    # Modeled lightly; not persisted this slice (consumed by Analytics later).
    history: list[dict]
    fixtures: list[dict]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models_schema.py -v`
Expected: 5 passed.

- [x] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_models_schema.py src/data/models.py
git commit -m "feat: Pydantic FPL response models with loud schema assertions"
```

---

## Task 5: Database schema and connection

**Files:**
- Test: `tests/test_db.py`
- Create: `src/data/schema.sql`, `src/data/db.py`

- [x] **Step 1: Write the failing test** in `tests/test_db.py`

```python
def test_init_db_creates_all_tables(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected = {
        "players", "teams", "player_stats", "fixtures", "fdr", "xp",
        "my_team", "gameweeks", "activity_log", "credentials", "cache_meta",
    }
    assert expected <= tables


def test_gameweeks_state_defaults_to_pending(db):
    db.execute("INSERT INTO gameweeks (id, name) VALUES (1, 'Gameweek 1')")
    row = db.execute("SELECT state FROM gameweeks WHERE id=1").fetchone()
    assert row["state"] == "PENDING"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.db'`.

- [x] **Step 3: Write `src/data/schema.sql`** (mirrors `docs/architecture.md`)

```sql
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY,
  name TEXT,
  web_name TEXT,
  team_id INTEGER,
  position TEXT,
  price REAL,
  status TEXT,
  ownership REAL,
  form REAL,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY,
  name TEXT,
  short_name TEXT,
  strength_attack_home INTEGER,
  strength_attack_away INTEGER,
  strength_defence_home INTEGER,
  strength_defence_away INTEGER
);

CREATE TABLE IF NOT EXISTS player_stats (
  player_id INTEGER,
  gw INTEGER,
  source TEXT,
  minutes INTEGER,
  goals INTEGER,
  assists INTEGER,
  xg REAL,
  xa REAL,
  bonus INTEGER,
  total_points INTEGER,
  PRIMARY KEY (player_id, gw, source)
);

CREATE TABLE IF NOT EXISTS fixtures (
  id INTEGER PRIMARY KEY,
  gw INTEGER,
  home_team_id INTEGER,
  away_team_id INTEGER,
  kickoff_utc TIMESTAMP,
  finished BOOLEAN,
  home_score INTEGER,
  away_score INTEGER
);

CREATE TABLE IF NOT EXISTS fdr (
  team_id INTEGER,
  gw INTEGER,
  fdr_attack INTEGER,
  fdr_defense INTEGER,
  computed_at TIMESTAMP,
  PRIMARY KEY (team_id, gw)
);

CREATE TABLE IF NOT EXISTS xp (
  player_id INTEGER,
  gw INTEGER,
  model_version TEXT,
  xp REAL,
  xminutes REAL,
  xgoals REAL,
  xassists REAL,
  xcs REAL,
  computed_at TIMESTAMP,
  PRIMARY KEY (player_id, gw, model_version)
);

CREATE TABLE IF NOT EXISTS my_team (
  gw INTEGER PRIMARY KEY,
  picks_json TEXT,
  bank REAL,
  team_value REAL,
  free_transfers INTEGER,
  chips_used_json TEXT,
  snapshot_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gameweeks (
  id INTEGER PRIMARY KEY,
  name TEXT,
  deadline_utc TIMESTAMP,
  is_current BOOLEAN,
  is_next BOOLEAN,
  finished BOOLEAN,
  state TEXT NOT NULL DEFAULT 'PENDING',
  last_user_action_at TIMESTAMP,
  last_system_action_at TIMESTAMP,
  deadguard_triggered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TIMESTAMP,
  gw INTEGER,
  mode TEXT,
  decision_type TEXT,
  action_taken TEXT,
  inputs_json TEXT,
  alternatives_json TEXT,
  executed BOOLEAN,
  exec_outcome_json TEXT
);

CREATE TABLE IF NOT EXISTS credentials (
  id INTEGER PRIMARY KEY,
  fpl_email_encrypted BLOB,
  fpl_password_encrypted BLOB,
  session_cookie_encrypted BLOB,
  csrf_token_encrypted BLOB,
  session_last_refreshed TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cache_meta (
  resource TEXT PRIMARY KEY,
  last_fetched_utc TIMESTAMP NOT NULL
);
```

- [x] **Step 4: Write `src/data/db.py`**

```python
import sqlite3
import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 2 passed.

- [x] **Step 6: Commit**

```bash
git add tests/test_db.py src/data/schema.sql src/data/db.py
git commit -m "feat: SQLite schema mirroring architecture.md + cache_meta"
```

---

## Task 6: Repository (upserts)

**Files:**
- Test: `tests/test_repository.py`
- Create: `src/data/repository.py`

- [x] **Step 1: Write the failing tests** in `tests/test_repository.py`

```python
from src.data.models import BootstrapStatic, EntryPicks, Fixture
from src.data import repository


def _bootstrap(load):
    return BootstrapStatic.model_validate(load("bootstrap-static.json"))


def test_upsert_players_maps_fields(db, load):
    bs = _bootstrap(load)
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    count = db.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    assert count == len(bs.elements)
    el = bs.elements[0]
    row = db.execute("SELECT price, position, team_id FROM players WHERE id=?", (el.id,)).fetchone()
    assert row["price"] == el.now_cost / 10.0
    assert row["position"] in {"GKP", "DEF", "MID", "FWD"}
    assert row["team_id"] == el.team


def test_upsert_players_idempotent(db, load):
    bs = _bootstrap(load)
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    repository.upsert_players(db, bs.elements, bs.element_types)
    count = db.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    assert count == len(bs.elements)


def test_upsert_gameweeks_preserves_state(db, load):
    bs = _bootstrap(load)
    repository.upsert_gameweeks(db, bs.events)
    db.execute("UPDATE gameweeks SET state='USER_ACTED' WHERE id=?", (bs.events[0].id,))
    db.commit()
    repository.upsert_gameweeks(db, bs.events)  # second refresh must not reset state
    row = db.execute("SELECT state FROM gameweeks WHERE id=?", (bs.events[0].id,)).fetchone()
    assert row["state"] == "USER_ACTED"


def test_upsert_fixtures(db, load):
    fixtures = [Fixture.model_validate(f) for f in load("fixtures.json")]
    repository.upsert_fixtures(db, fixtures)
    count = db.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"]
    assert count == len(fixtures)


def test_snapshot_my_team(db, load):
    picks = EntryPicks.model_validate(load("picks.json"))
    repository.snapshot_my_team(db, 38, picks)
    row = db.execute("SELECT picks_json, bank, free_transfers FROM my_team WHERE gw=38").fetchone()
    import json
    parsed = json.loads(row["picks_json"])
    assert len(parsed) == 15
    assert row["free_transfers"] is None  # public-API limitation (spec §6)
    assert row["bank"] == picks.entry_history.bank / 10.0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.repository'`.

- [x] **Step 3: Write `src/data/repository.py`**

```python
import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def upsert_teams(conn, teams):
    conn.executemany(
        """INSERT INTO teams (id, name, short_name, strength_attack_home,
             strength_attack_away, strength_defence_home, strength_defence_away)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, short_name=excluded.short_name,
             strength_attack_home=excluded.strength_attack_home,
             strength_attack_away=excluded.strength_attack_away,
             strength_defence_home=excluded.strength_defence_home,
             strength_defence_away=excluded.strength_defence_away""",
        [(t.id, t.name, t.short_name, t.strength_attack_home, t.strength_attack_away,
          t.strength_defence_home, t.strength_defence_away) for t in teams],
    )
    conn.commit()


def upsert_players(conn, elements, element_types):
    pos = {et.id: et.singular_name_short for et in element_types}
    now = _now()
    conn.executemany(
        """INSERT INTO players (id, name, web_name, team_id, position, price,
             status, ownership, form, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, web_name=excluded.web_name,
             team_id=excluded.team_id, position=excluded.position,
             price=excluded.price, status=excluded.status,
             ownership=excluded.ownership, form=excluded.form,
             updated_at=excluded.updated_at""",
        [(e.id, f"{e.first_name} {e.second_name}", e.web_name, e.team,
          pos[e.element_type], e.now_cost / 10.0, e.status,
          e.selected_by_percent, e.form, now) for e in elements],
    )
    conn.commit()


def upsert_gameweeks(conn, events):
    # state column defaults to 'PENDING' on insert and is intentionally NOT
    # touched on conflict, so a refresh never clobbers the state machine.
    conn.executemany(
        """INSERT INTO gameweeks (id, name, deadline_utc, is_current, is_next, finished)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, deadline_utc=excluded.deadline_utc,
             is_current=excluded.is_current, is_next=excluded.is_next,
             finished=excluded.finished""",
        [(ev.id, ev.name, ev.deadline_time.isoformat(), ev.is_current,
          ev.is_next, ev.finished) for ev in events],
    )
    conn.commit()


def upsert_fixtures(conn, fixtures):
    conn.executemany(
        """INSERT INTO fixtures (id, gw, home_team_id, away_team_id, kickoff_utc,
             finished, home_score, away_score)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             gw=excluded.gw, home_team_id=excluded.home_team_id,
             away_team_id=excluded.away_team_id, kickoff_utc=excluded.kickoff_utc,
             finished=excluded.finished, home_score=excluded.home_score,
             away_score=excluded.away_score""",
        [(f.id, f.event, f.team_h, f.team_a,
          f.kickoff_time.isoformat() if f.kickoff_time else None,
          f.finished, f.team_h_score, f.team_a_score) for f in fixtures],
    )
    conn.commit()


def snapshot_my_team(conn, gw, picks):
    picks_json = json.dumps([
        {"element": p.element, "position": p.position, "multiplier": p.multiplier,
         "is_captain": p.is_captain, "is_vice_captain": p.is_vice_captain}
        for p in picks.picks
    ])
    chips = json.dumps([picks.active_chip] if picks.active_chip else [])
    conn.execute(
        """INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers,
             chips_used_json, snapshot_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(gw) DO UPDATE SET
             picks_json=excluded.picks_json, bank=excluded.bank,
             team_value=excluded.team_value, free_transfers=excluded.free_transfers,
             chips_used_json=excluded.chips_used_json, snapshot_at=excluded.snapshot_at""",
        (gw, picks_json, picks.entry_history.bank / 10.0,
         picks.entry_history.value / 10.0, None, chips, _now()),  # free_transfers: auth-only
    )
    conn.commit()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: 5 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_repository.py src/data/repository.py
git commit -m "feat: repository upserts for teams/players/gameweeks/fixtures/my_team"
```

---

## Task 7: Cache / staleness

**Files:**
- Test: `tests/test_cache.py`
- Create: `src/data/cache.py`

- [x] **Step 1: Write the failing tests** in `tests/test_cache.py`

```python
from datetime import datetime, timezone, timedelta
from src.data import cache


def test_is_stale_when_never_fetched(db):
    assert cache.is_stale(db, "bootstrap-static") is True


def test_not_stale_immediately_after_mark(db):
    cache.mark_fetched(db, "bootstrap-static")
    assert cache.is_stale(db, "bootstrap-static") is False


def test_stale_after_ttl_elapsed(db):
    cache.mark_fetched(db, "bootstrap-static")
    future = datetime.now(timezone.utc) + timedelta(hours=7)  # TTL is 6h
    assert cache.is_stale(db, "bootstrap-static", now=future) is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.cache'`.

- [x] **Step 3: Write `src/data/cache.py`**

```python
from datetime import datetime, timezone, timedelta

DEFAULT_TTL = {
    "bootstrap-static": timedelta(hours=6),
    "fixtures": timedelta(hours=6),
    "my_team": timedelta(hours=1),
}


def _now():
    return datetime.now(timezone.utc)


def is_stale(conn, resource, now=None):
    now = now or _now()
    row = conn.execute(
        "SELECT last_fetched_utc FROM cache_meta WHERE resource=?", (resource,)
    ).fetchone()
    if row is None:
        return True
    last = datetime.fromisoformat(row["last_fetched_utc"])
    ttl = DEFAULT_TTL.get(resource, timedelta(0))
    return (now - last) >= ttl


def mark_fetched(conn, resource, now=None):
    now = now or _now()
    conn.execute(
        """INSERT INTO cache_meta (resource, last_fetched_utc) VALUES (?,?)
           ON CONFLICT(resource) DO UPDATE SET last_fetched_utc=excluded.last_fetched_utc""",
        (resource, now.isoformat()),
    )
    conn.commit()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cache.py -v`
Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_cache.py src/data/cache.py
git commit -m "feat: cache staleness layer (read-DB-first)"
```

---

## Task 8: Config loader

**Files:**
- Test: `tests/test_config.py`
- Create: `src/config.py`

- [x] **Step 1: Write the failing test** in `tests/test_config.py`

```python
from src import config


def test_team_id_from_config():
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": "x.db"}}
    assert config.team_id(cfg) == 3122849
    assert config.db_path(cfg) == "x.db"


def test_loads_repo_config_yaml():
    cfg = config.load_config()
    assert cfg["fpl"]["team_id"] == 3122849
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ImportError`/`AttributeError` (no `src.config`).

- [x] **Step 3: Write `src/config.py`**

```python
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_DB_PATH = str(ROOT / "data" / "fpl_autopilot.db")


def load_config(path=None):
    path = pathlib.Path(path) if path else CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)


def team_id(cfg=None):
    cfg = cfg or load_config()
    return cfg["fpl"]["team_id"]


def db_path(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("storage", {}).get("db_path", DEFAULT_DB_PATH)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 2 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_config.py src/config.py
git commit -m "feat: config loader (team_id, db_path)"
```

---

## Task 9: FPL API client

**Files:**
- Test: `tests/test_fpl_client.py`
- Create: `src/data/fpl_client.py`

- [x] **Step 1: Write the failing tests** in `tests/test_fpl_client.py`

```python
import pytest
import requests
from src.data.fpl_client import FPLClient
from src.data.models import BootstrapStatic

EMPTY_BOOTSTRAP = {"events": [], "teams": [], "elements": [], "element_types": []}


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, items):
        self.headers = {}
        self._items = list(items)  # FakeResponse or Exception instances
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(items, sleeps=None, times=None):
    sleeps = sleeps if sleeps is not None else []
    times = times if times is not None else iter(range(0, 10_000_000, 100))
    return FPLClient(
        session=FakeSession(items),
        sleep=sleeps.append,
        monotonic=lambda: next(times),
    )


def test_user_agent_is_realistic():
    session = FakeSession([])
    FPLClient(session=session)
    assert "Mozilla" in session.headers["User-Agent"]


def test_parses_bootstrap_into_model():
    client = _client([FakeResponse(200, EMPTY_BOOTSTRAP)])
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)


def test_retries_on_5xx_then_succeeds():
    sleeps = []
    client = _client(
        [FakeResponse(500), FakeResponse(503), FakeResponse(200, EMPTY_BOOTSTRAP)],
        sleeps=sleeps,
    )
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)
    assert sleeps == [1, 5]  # two backoffs before the third call succeeds


def test_retries_on_connection_error():
    sleeps = []
    client = _client(
        [requests.ConnectionError("boom"), FakeResponse(200, EMPTY_BOOTSTRAP)],
        sleeps=sleeps,
    )
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)
    assert sleeps == [1]


def test_no_retry_on_404():
    session = FakeSession([FakeResponse(404)])
    client = FPLClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    with pytest.raises(requests.HTTPError):
        client.entry(999999999)
    assert len(session.calls) == 1


def test_rate_limit_sleeps_between_calls():
    sleeps = []
    client = FPLClient(
        session=FakeSession([FakeResponse(200, EMPTY_BOOTSTRAP),
                             FakeResponse(200, EMPTY_BOOTSTRAP)]),
        sleep=sleeps.append,
        monotonic=lambda: 0.0,  # no time passes -> must wait ~1s before 2nd call
    )
    client.bootstrap_static()
    client.bootstrap_static()
    assert any(s > 0 for s in sleeps)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fpl_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.data.fpl_client'`.

- [x] **Step 3: Write `src/data/fpl_client.py`**

```python
import time
import requests
from .models import BootstrapStatic, Fixture, Entry, EntryPicks, ElementSummary

BASE_URL = "https://fantasy.premierleague.com/api/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAYS = (1, 5, 30)
MIN_INTERVAL = 1.0  # <= 1 req/s (B6)
TIMEOUT = 10


class FPLClient:
    def __init__(self, session=None, sleep=time.sleep, monotonic=time.monotonic):
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = None

    def _rate_limit(self):
        if self._last_request_at is not None:
            wait = MIN_INTERVAL - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _get(self, path, params=None):
        url = BASE_URL + path
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
                else:
                    resp.raise_for_status()  # 4xx (non-429): fail immediately
            if attempt < len(RETRY_DELAYS):
                self._sleep(RETRY_DELAYS[attempt])
        raise last_exc

    def bootstrap_static(self):
        return BootstrapStatic.model_validate(self._get("bootstrap-static/"))

    def fixtures(self, event=None):
        params = {"event": event} if event is not None else None
        return [Fixture.model_validate(f) for f in self._get("fixtures/", params=params)]

    def entry(self, team_id):
        return Entry.model_validate(self._get(f"entry/{team_id}/"))

    def picks(self, team_id, gw):
        return EntryPicks.model_validate(self._get(f"entry/{team_id}/event/{gw}/picks/"))

    def element_summary(self, player_id):
        return ElementSummary.model_validate(self._get(f"element-summary/{player_id}/"))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fpl_client.py -v`
Expected: 6 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_fpl_client.py src/data/fpl_client.py
git commit -m "feat: hardened FPL API client (retry/backoff/rate-limit/UA)"
```

---

## Task 10: CLI refresh wiring

**Files:**
- Test: `tests/test_cli_refresh.py`
- Create: `src/cli.py`

- [x] **Step 1: Write the failing test** in `tests/test_cli_refresh.py`

```python
from src import cli
from src.data.db import connect, init_db
from src.data.models import BootstrapStatic, EntryPicks, Fixture


class FakeClient:
    def __init__(self, bs, fx, picks):
        self._bs, self._fx, self._picks = bs, fx, picks

    def bootstrap_static(self):
        return self._bs

    def fixtures(self, event=None):
        return self._fx

    def picks(self, team_id, gw):
        return self._picks


def test_refresh_populates_db(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    client = FakeClient(bs, fx, picks)
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"}}

    cli.refresh(full=True, cfg=cfg, conn=conn, client=client)

    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == len(bs.teams)
    assert conn.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == len(fx)
    assert conn.execute("SELECT COUNT(*) c FROM my_team").fetchone()["c"] == 1
    conn.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_refresh.py -v`
Expected: FAIL with `ImportError`/`AttributeError` (no `cli.refresh`).

- [x] **Step 3: Write `src/cli.py`**

```python
import argparse
from .config import load_config, team_id as cfg_team_id, db_path as cfg_db_path
from .data.db import connect, init_db
from .data.fpl_client import FPLClient
from .data import repository, cache


def _current_gw_from_db(conn):
    row = conn.execute("SELECT id FROM gameweeks WHERE is_current=1").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks WHERE finished=1").fetchone()
    if row and row["id"] is not None:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks").fetchone()
    return row["id"] if row else None


def refresh(full=False, cfg=None, conn=None, client=None):
    cfg = cfg or load_config()
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    client = client or FPLClient()
    tid = cfg_team_id(cfg)

    if full or cache.is_stale(conn, "bootstrap-static"):
        bs = client.bootstrap_static()
        repository.upsert_teams(conn, bs.teams)
        repository.upsert_players(conn, bs.elements, bs.element_types)
        repository.upsert_gameweeks(conn, bs.events)
        cache.mark_fetched(conn, "bootstrap-static")
        print(f"bootstrap-static OK ({len(bs.elements)} players, {len(bs.teams)} teams)")

    if full or cache.is_stale(conn, "fixtures"):
        fx = client.fixtures()
        repository.upsert_fixtures(conn, fx)
        cache.mark_fetched(conn, "fixtures")
        print(f"fixtures OK ({len(fx)} fixtures)")

    gw = _current_gw_from_db(conn)
    if gw is not None and (full or cache.is_stale(conn, "my_team")):
        picks = client.picks(tid, gw)
        repository.snapshot_my_team(conn, gw, picks)
        cache.mark_fetched(conn, "my_team")
        print(f"my_team OK (GW{gw}, {len(picks.picks)} picks)")

    if owns_conn:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fpl-autopilot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_refresh = sub.add_parser("refresh", help="fetch FPL data into the local DB")
    p_refresh.add_argument("--full", action="store_true", help="ignore cache, fetch everything")
    args = parser.parse_args(argv)
    if args.command == "refresh":
        refresh(full=args.full)


if __name__ == "__main__":
    main()
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_refresh.py -v`
Expected: 1 passed.

- [x] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (models, db, repository, cache, config, client, cli).

- [x] **Step 6: Commit**

```bash
git add tests/test_cli_refresh.py src/cli.py
git commit -m "feat: fpl-autopilot refresh CLI wiring data layer end-to-end"
```

---

## Task 11: Live smoke test (definition of done)

Verify the slice against the real API and the real team `3122849`. No new code — this is the acceptance check from spec §8.

- [x] **Step 1: Run a full refresh against the live API**

Run:
```bash
.venv/bin/fpl-autopilot refresh --full
```
Expected: prints three OK lines, e.g.
```
bootstrap-static OK (600+ players, 20 teams)
fixtures OK (300+ fixtures)
my_team OK (GW38, 15 picks)
```

- [x] **Step 2: Verify the data landed in SQLite**

Run:
```bash
sqlite3 data/fpl_autopilot.db "SELECT
  (SELECT COUNT(*) FROM players) AS players,
  (SELECT COUNT(*) FROM teams) AS teams,
  (SELECT COUNT(*) FROM fixtures) AS fixtures,
  (SELECT COUNT(*) FROM gameweeks) AS gameweeks,
  (SELECT COUNT(*) FROM my_team) AS my_team_rows;"
```
Expected: players 500+, teams 20, fixtures 300+, gameweeks 38, my_team_rows 1.

- [x] **Step 3: Eyeball the squad snapshot**

Run:
```bash
sqlite3 data/fpl_autopilot.db "SELECT p.web_name, p.position, p.price
  FROM my_team m, json_each(m.picks_json) j
  JOIN players p ON p.id = json_extract(j.value, '$.element')
  WHERE m.gw = (SELECT MAX(gw) FROM my_team);"
```
Expected: 15 recognizable players with positions and prices — your actual squad.

- [x] **Step 4: Verify the cache short-circuits a second refresh**

Run:
```bash
.venv/bin/fpl-autopilot refresh
```
Expected: prints nothing (or fewer lines) because all resources are fresh within TTL — proves read-DB-first caching works.

- [x] **Step 5: Final commit (mark slice complete)**

```bash
git add -A
git commit -m "chore: data-layer foundation slice complete and smoke-tested" --allow-empty
```

---

## Self-Review notes (author)

- **Spec coverage:** scaffold (T1), docs reorg + architecture deltas (T2), fixtures (T3), models/schema-assertions incl. loud-failure (T4), all-tables DB (T5), repository upserts (T6), cache (T7), config (T8), hardened client w/ retry+backoff+rate-limit+UA (T9), refresh CLI (T10), live smoke incl. team 3122849 (T11). Understat deliberately out (spec §3).
- **Known-limitation coverage:** `free_transfers` NULL asserted in T6; documented in T2.
- **Type consistency:** model attribute names (`now_cost`, `team`, `element_type`, `entry_history`, `singular_name_short`) used identically across T4/T6/T9/T10; repository function names (`upsert_teams/players/gameweeks/fixtures`, `snapshot_my_team`) consistent T6↔T10; cache (`is_stale`, `mark_fetched`) consistent T7↔T10.
