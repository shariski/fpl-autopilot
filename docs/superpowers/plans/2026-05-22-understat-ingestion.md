# Understat Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** `fpl-autopilot refresh` ingests Understat season-aggregate xG/xA via its JSON endpoint, conservatively resolves Understat players to FPL ids, and persists into a new `understat_players` table — degrading gracefully if Understat fails.

**Architecture:** Adds to the existing Data Layer (`src/data/`). A hardened `UnderstatClient` (mirrors `FPLClient`) hits `POST /main/getPlayersStats/`. Pure-function `name_resolver` maps Understat→FPL ids (explicit team overrides + comma-split for mid-season transfers + tiered name matching, conservative/unambiguous-only). A repository upsert writes season aggregates + derived per-90 + resolved fpl id into `understat_players`. The CLI `refresh` wraps the Understat step so a failure logs a warning and keeps last data without breaking the FPL refresh.

**Tech Stack:** Python 3.11+, `requests`, Pydantic v2, raw `sqlite3`, `pyyaml`, `pytest`. `.venv` exists; `src/` is the package.

**Spec:** `docs/superpowers/specs/2026-05-22-understat-ingestion-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/data/understat_client.py` | NEW: hardened client for `getPlayersStats`. |
| `src/data/name_resolver.py` | NEW: pure FPL↔Understat resolution (teams + players). |
| `src/data/models.py` | +`UnderstatPlayer`, +`UnderstatPlayersResponse`. |
| `src/data/schema.sql` | +`understat_players` table. |
| `src/data/repository.py` | +`upsert_understat_players`. |
| `src/data/cache.py` | +`understat` TTL. |
| `src/cli.py` | refresh integrates Understat + `--source` + graceful degradation. |
| `config.yaml` | +`understat.season`. |
| `data/name_resolution.yaml` | NEW committed template (manual overrides). |
| `docs/architecture.md` | +`understat_players` table (B13). |
| `docs/onboarding.md` | name-resolution yaml format note (B13). |
| `tests/fixtures/understat-players.json` | NEW frozen endpoint response. |
| `tests/test_understat_*.py`, `tests/test_name_resolver.py` | tests. |

---

## Task 1: Capture the Understat fixture

**Files:** Create `tests/fixtures/understat-players.json`

- [x] **Step 1: Capture the endpoint response (gzip auto-decompressed)**

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
curl -s --compressed -X POST \
  -H "User-Agent: $UA" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/x-www-form-urlencoded; charset=UTF-8" \
  --data "league=EPL&season=2025" \
  "https://understat.com/main/getPlayersStats/" \
  -o tests/fixtures/understat-players.json
```

- [x] **Step 2: Verify shape**

```bash
.venv/bin/python -c "import json; d=json.load(open('tests/fixtures/understat-players.json')); ps=d['players']; print('success', d['success'], 'players', len(ps)); h=[p for p in ps if p['player_name']=='Erling Haaland'][0]; print('haaland xG', h['xG'], 'team', h['team_title'])"
```
Expected: `success True players 500+`, Haaland present with a non-zero `xG`. If `success` is False or players is tiny, STOP and report (endpoint shape changed).

- [x] **Step 3: Commit**

```bash
git add tests/fixtures/understat-players.json
git commit -m "test: capture frozen Understat getPlayersStats fixture"
```

---

## Task 2: Understat Pydantic models

**Files:** Test `tests/test_understat_models.py`; Modify `src/data/models.py`

- [x] **Step 1: Write the failing tests** in `tests/test_understat_models.py`

```python
import pytest
from pydantic import ValidationError
from src.data.models import UnderstatPlayer, UnderstatPlayersResponse


def test_understat_response_parses(load):
    resp = UnderstatPlayersResponse.model_validate(load("understat-players.json"))
    assert resp.success is True
    assert len(resp.players) > 500
    haaland = next(p for p in resp.players if p.player_name == "Erling Haaland")
    assert haaland.xG > 0
    assert haaland.time > 0


def test_understat_numeric_strings_coerce(load):
    resp = UnderstatPlayersResponse.model_validate(load("understat-players.json"))
    p = resp.players[0]
    assert isinstance(p.games, int)
    assert isinstance(p.xG, float)


def test_understat_schema_drift_fails_loudly(load):
    data = load("understat-players.json")
    data["players"][0]["xG"] = "not-a-number"  # float field, non-coercible -> must raise
    with pytest.raises(ValidationError):
        UnderstatPlayersResponse.model_validate(data)
```

- [x] **Step 2: Run to verify they FAIL**

Run: `.venv/bin/pytest tests/test_understat_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'UnderstatPlayer'`.

- [x] **Step 3: Append to `src/data/models.py`**

```python
class UnderstatPlayer(_Base):
    id: str
    player_name: str
    team_title: str
    games: int
    time: int            # minutes
    goals: int
    assists: int
    xG: float
    xA: float
    npg: int
    npxG: float


class UnderstatPlayersResponse(_Base):
    success: bool
    players: list[UnderstatPlayer]
```

- [x] **Step 4: Run to verify they PASS**

Run: `.venv/bin/pytest tests/test_understat_models.py -v`
Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_understat_models.py src/data/models.py
git commit -m "feat: Understat Pydantic models with loud drift detection"
```

---

## Task 3: `understat_players` table

**Files:** Test `tests/test_understat_schema.py`; Modify `src/data/schema.sql`, `docs/architecture.md`

- [x] **Step 1: Write the failing test** in `tests/test_understat_schema.py`

```python
def test_understat_players_table_exists(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(understat_players)")}
    expected = {
        "understat_id", "fpl_player_id", "season", "player_name", "team_title",
        "games", "minutes", "goals", "assists", "xg", "xa", "npg", "npxg",
        "xg_per_90", "xa_per_90", "updated_at",
    }
    assert expected <= cols
```

- [x] **Step 2: Run to verify it FAILS**

Run: `.venv/bin/pytest tests/test_understat_schema.py -v`
Expected: FAIL — assertion error (table/columns absent; `PRAGMA` returns empty set).

- [x] **Step 3: Append the table to `src/data/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS understat_players (
  understat_id TEXT PRIMARY KEY,
  fpl_player_id INTEGER,
  season TEXT,
  player_name TEXT,
  team_title TEXT,
  games INTEGER,
  minutes INTEGER,
  goals INTEGER,
  assists INTEGER,
  xg REAL,
  xa REAL,
  npg INTEGER,
  npxg REAL,
  xg_per_90 REAL,
  xa_per_90 REAL,
  updated_at TIMESTAMP
);
```

- [x] **Step 4: Document the table in `docs/architecture.md`** (B13). After the `### cache_meta` section, insert:

```markdown
### `understat_players`

Season-aggregate xG/xA from Understat (supplementary data), with the resolved FPL id.

| Column | Type | Notes |
|---|---|---|
| understat_id | TEXT PRIMARY KEY | Understat player id |
| fpl_player_id | INTEGER | Resolved FPL id; NULL if unmatched |
| season | TEXT | e.g. "2025" (= 2025/26) |
| player_name | TEXT | Understat name |
| team_title | TEXT | Understat team(s); comma-separated on mid-season transfer |
| games, minutes, goals, assists | INTEGER | Season totals |
| xg, xa, npxg | REAL | Season totals (npxg = non-penalty xG) |
| npg | INTEGER | Non-penalty goals |
| xg_per_90, xa_per_90 | REAL | Derived: stat / (minutes/90) |
| updated_at | TIMESTAMP | |

```

- [x] **Step 5: Run to verify it PASSES**

Run: `.venv/bin/pytest tests/test_understat_schema.py -v`
Expected: 1 passed.

- [x] **Step 6: Commit**

```bash
git add tests/test_understat_schema.py src/data/schema.sql docs/architecture.md
git commit -m "feat: understat_players table + architecture.md entry"
```

---

## Task 4: Name resolver (FPL ↔ Understat)

The risky core. Logic validated empirically at 98% match against real data. Conservative: unmatched is safe (degrades to FPL-only); a wrong match is what we refuse to risk.

**Files:** Test `tests/test_name_resolver.py`; Create `src/data/name_resolver.py`

- [x] **Step 1: Write the failing tests** in `tests/test_name_resolver.py`

```python
from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import repository, name_resolver


def _fpl_rows(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    return players, teams


def _understat(load):
    return UnderstatPlayersResponse.model_validate(load("understat-players.json")).players


def test_team_title_normalizes_and_overrides(db, load):
    _, teams = _fpl_rows(db, load)
    lookup = name_resolver._team_lookup(teams)
    ids, unmapped = name_resolver._resolve_team_title("Tottenham", lookup)  # -> Spurs (override)
    assert unmapped == [] and len(ids) == 1
    ids2, unmapped2 = name_resolver._resolve_team_title("Arsenal", lookup)  # normalizes directly
    assert unmapped2 == [] and len(ids2) == 1


def test_comma_team_title_is_mid_season_transfer(db, load):
    _, teams = _fpl_rows(db, load)
    lookup = name_resolver._team_lookup(teams)
    ids, unmapped = name_resolver._resolve_team_title("Aston Villa,Crystal Palace", lookup)
    assert unmapped == [] and len(ids) == 2


def test_resolves_known_players_with_high_match_rate(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    res = name_resolver.resolve_players(players, teams, us)
    assert res.unmapped_teams == []
    assert len(res.matched) >= int(0.95 * len(us))  # observed ~98%
    haaland_u = next(p for p in us if p.player_name == "Erling Haaland")
    fpl_haaland = next(r for r in players if r["web_name"] == "Haaland")
    assert res.matched[haaland_u.id] == fpl_haaland["id"]


def test_ambiguous_name_left_unmatched(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    res = name_resolver.resolve_players(players, teams, us)
    gabriel = next((p for p in us if p.player_name == "Gabriel"), None)
    if gabriel is not None:  # Arsenal has multiple "Gabriel"s -> must not force a match
        assert gabriel.id not in res.matched


def test_manual_override_is_authoritative(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    target = us[0]
    res = name_resolver.resolve_players(players, teams, us, overrides={target.id: 99999})
    assert res.matched[target.id] == 99999
```

- [x] **Step 2: Run to verify they FAIL**

Run: `.venv/bin/pytest tests/test_name_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.name_resolver'`.

- [x] **Step 3: Write `src/data/name_resolver.py`**

```python
import re
import unicodedata
from dataclasses import dataclass

# Understat single-team names that don't normalize-match FPL team names (2025/26 PL).
# Season-specific (D5): revisit on rollover. Maps Understat team -> FPL team name.
UNDERSTAT_TEAM_OVERRIDES = {
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham": "Spurs",
    "Wolverhampton Wanderers": "Wolves",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class ResolutionResult:
    matched: dict          # understat_id -> fpl_player_id
    unmatched: list        # UnderstatPlayer objects with no confident match
    unmapped_teams: list   # understat team tokens that mapped to no FPL team


def _team_lookup(fpl_teams):
    lookup = {}
    for t in fpl_teams:
        lookup[_norm(t["name"])] = t["id"]
        lookup[_norm(t["short_name"])] = t["id"]
    return lookup


def _resolve_team_title(team_title, team_lookup):
    # team_title may be comma-separated for mid-season transfers.
    ids, unmapped = [], []
    for token in team_title.split(","):
        token = token.strip()
        tid = team_lookup.get(_norm(UNDERSTAT_TEAM_OVERRIDES.get(token, token)))
        if tid is None:
            unmapped.append(token)
        else:
            ids.append(tid)
    return ids, unmapped


def resolve_players(fpl_players, fpl_teams, understat_players, overrides=None):
    overrides = overrides or {}
    team_lookup = _team_lookup(fpl_teams)
    by_team = {}
    for p in fpl_players:
        by_team.setdefault(p["team_id"], []).append(
            (p["id"], set(_norm(p["name"]).split()), set(_norm(p["web_name"]).split()))
        )

    matched, unmatched, unmapped_teams = {}, [], set()
    for up in understat_players:
        if up.id in overrides:
            matched[up.id] = overrides[up.id]
            continue
        team_ids, unmapped = _resolve_team_title(up.team_title, team_lookup)
        unmapped_teams.update(unmapped)
        u = set(_norm(up.player_name).split())
        cands = [c for tid in team_ids for c in by_team.get(tid, [])]
        # Tier 1: full-name token subset (either direction).
        tier1 = {fid for fid, full, web in cands if u <= full or full <= u}
        if len(tier1) == 1:
            matched[up.id] = next(iter(tier1))
            continue
        # Tier 2 (only if tier 1 found nothing): web_name (surname) tokens inside the Understat name.
        if not tier1:
            tier2 = {fid for fid, full, web in cands if web and web <= u}
            if len(tier2) == 1:
                matched[up.id] = next(iter(tier2))
                continue
        unmatched.append(up)
    return ResolutionResult(matched, unmatched, sorted(unmapped_teams))
```

- [x] **Step 4: Run to verify they PASS**

Run: `.venv/bin/pytest tests/test_name_resolver.py -v`
Expected: 6 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_name_resolver.py src/data/name_resolver.py
git commit -m "feat: conservative FPL<->Understat name resolver (tiered, ~98% match)"
```

---

## Task 5: Understat client

Mirrors `FPLClient` hardening. Deliberately NOT extracting a shared HTTP base — only two sources exist (YAGNI; the spec defers extraction until a third appears).

**Files:** Test `tests/test_understat_client.py`; Create `src/data/understat_client.py`

- [x] **Step 1: Write the failing tests** in `tests/test_understat_client.py`

```python
import pytest
import requests
from src.data.understat_client import UnderstatClient
from src.data.models import UnderstatPlayersResponse

OK_BODY = {"success": True, "players": []}


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
        self._items = list(items)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(items, sleeps=None, times=None):
    sleeps = sleeps if sleeps is not None else []
    times = times if times is not None else iter(range(0, 10_000_000, 100))
    return UnderstatClient(session=FakeSession(items), sleep=sleeps.append, monotonic=lambda: next(times))


def test_user_agent_and_xrw_headers():
    session = FakeSession([])
    UnderstatClient(session=session)
    assert "Mozilla" in session.headers["User-Agent"]
    assert session.headers["X-Requested-With"] == "XMLHttpRequest"


def test_players_stats_posts_correct_body_and_parses():
    session = FakeSession([FakeResponse(200, OK_BODY)])
    client = UnderstatClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    result = client.players_stats("2025")
    assert isinstance(result, UnderstatPlayersResponse)
    url, data = session.calls[0]
    assert url.endswith("/main/getPlayersStats/")
    assert data == {"league": "EPL", "season": "2025"}


def test_retries_on_5xx_then_succeeds():
    sleeps = []
    client = _client([FakeResponse(500), FakeResponse(200, OK_BODY)], sleeps=sleeps)
    result = client.players_stats("2025")
    assert isinstance(result, UnderstatPlayersResponse)
    assert sleeps == [1]


def test_no_retry_on_404():
    session = FakeSession([FakeResponse(404)])
    client = UnderstatClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    with pytest.raises(requests.HTTPError):
        client.players_stats("2025")
    assert len(session.calls) == 1
```

- [x] **Step 2: Run to verify they FAIL**

Run: `.venv/bin/pytest tests/test_understat_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.understat_client'`.

- [x] **Step 3: Write `src/data/understat_client.py`**

```python
import time
import requests
from .models import UnderstatPlayersResponse

BASE_URL = "https://understat.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAYS = (1, 5, 30)
MIN_INTERVAL = 1.0  # <= 1 req/s (B6)
TIMEOUT = 15


class UnderstatClient:
    def __init__(self, session=None, sleep=time.sleep, monotonic=time.monotonic):
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = None

    def _rate_limit(self):
        if self._last_request_at is not None:
            wait = MIN_INTERVAL - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _post(self, path, data):
        url = BASE_URL + path
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._rate_limit()
            try:
                resp = self._session.post(url, data=data, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
                else:
                    resp.raise_for_status()
            if attempt < len(RETRY_DELAYS):
                self._sleep(RETRY_DELAYS[attempt])
        raise last_exc

    def players_stats(self, season="2025"):
        data = self._post("/main/getPlayersStats/", {"league": "EPL", "season": season})
        return UnderstatPlayersResponse.model_validate(data)
```

- [x] **Step 4: Run to verify they PASS**

Run: `.venv/bin/pytest tests/test_understat_client.py -v`
Expected: 4 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_understat_client.py src/data/understat_client.py
git commit -m "feat: hardened Understat client (getPlayersStats, retry/backoff/rate-limit)"
```

---

## Task 6: Repository upsert for Understat

**Files:** Test `tests/test_understat_repository.py`; Modify `src/data/repository.py`

- [x] **Step 1: Write the failing tests** in `tests/test_understat_repository.py`

```python
from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import repository, name_resolver


def _setup(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    us = UnderstatPlayersResponse.model_validate(load("understat-players.json")).players
    res = name_resolver.resolve_players(players, teams, us)
    return us, res


def test_upsert_understat_players_maps_and_derives(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    count = db.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert count == len(us)
    haaland = next(p for p in us if p.player_name == "Erling Haaland")
    row = db.execute(
        "SELECT fpl_player_id, xg_per_90, minutes, xg FROM understat_players WHERE understat_id=?",
        (haaland.id,),
    ).fetchone()
    assert row["fpl_player_id"] is not None
    assert row["xg_per_90"] == round(haaland.xG / (haaland.time / 90.0), 4)


def test_upsert_understat_zero_minutes_per90_is_zero(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    # any zero-minute player must have per-90 == 0.0, never a division error
    rows = db.execute("SELECT xg_per_90 FROM understat_players WHERE minutes=0").fetchall()
    assert all(r["xg_per_90"] == 0.0 for r in rows)


def test_upsert_understat_idempotent(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    repository.upsert_understat_players(db, us, res, "2025")
    count = db.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert count == len(us)
```

- [x] **Step 2: Run to verify they FAIL**

Run: `.venv/bin/pytest tests/test_understat_repository.py -v`
Expected: FAIL — `AttributeError: module 'src.data.repository' has no attribute 'upsert_understat_players'`.

- [x] **Step 3: Append to `src/data/repository.py`**

```python
def _per90(value, minutes):
    return round(value / (minutes / 90.0), 4) if minutes else 0.0


def upsert_understat_players(conn, understat_players, resolution, season):
    now = _now()
    rows = [
        (up.id, resolution.matched.get(up.id), season, up.player_name, up.team_title,
         up.games, up.time, up.goals, up.assists, up.xG, up.xA, up.npg, up.npxG,
         _per90(up.xG, up.time), _per90(up.xA, up.time), now)
        for up in understat_players
    ]
    conn.executemany(
        """INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name,
             team_title, games, minutes, goals, assists, xg, xa, npg, npxg,
             xg_per_90, xa_per_90, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(understat_id) DO UPDATE SET
             fpl_player_id=excluded.fpl_player_id, season=excluded.season,
             player_name=excluded.player_name, team_title=excluded.team_title,
             games=excluded.games, minutes=excluded.minutes, goals=excluded.goals,
             assists=excluded.assists, xg=excluded.xg, xa=excluded.xa, npg=excluded.npg,
             npxg=excluded.npxg, xg_per_90=excluded.xg_per_90, xa_per_90=excluded.xa_per_90,
             updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()
```

- [x] **Step 4: Run to verify they PASS**

Run: `.venv/bin/pytest tests/test_understat_repository.py -v`
Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add tests/test_understat_repository.py src/data/repository.py
git commit -m "feat: upsert_understat_players (resolved fpl id + derived per-90)"
```

---

## Task 7: Cache TTL + CLI integration + graceful degradation

**Files:** Modify `src/data/cache.py`, `src/cli.py`, `config.yaml`; Create `data/name_resolution.yaml`; Test `tests/test_cli_refresh.py` (extend); Modify `docs/onboarding.md`

- [x] **Step 1: Add the Understat TTL** in `src/data/cache.py`. Change the `DEFAULT_TTL` dict to include understat:

```python
DEFAULT_TTL = {
    "bootstrap-static": timedelta(hours=6),
    "fixtures": timedelta(hours=6),
    "my_team": timedelta(hours=1),
    "understat": timedelta(hours=6),
}
```

- [x] **Step 2: Add the season to `config.yaml`.** After the `storage:` block, add:

```yaml
understat:
  season: "2025"   # Understat uses the start year; "2025" = the 2025/26 season
```

- [x] **Step 3: Create the override template `data/name_resolution.yaml`**

```yaml
# Manual FPL<->Understat overrides for players the resolver can't match confidently.
# Format: "<understat_id>": <fpl_player_id>
# Find the understat_id in the understat_players table (player_name/team_title columns),
# and the fpl_player_id in the players table. Example:
# "8260": 12345
```

- [x] **Step 4: Write the failing tests** — append to `tests/test_cli_refresh.py`

```python
class FakeUnderstatClient:
    def __init__(self, resp):
        self._resp = resp
        self.called = False

    def players_stats(self, season="2025"):
        self.called = True
        return self._resp


class BoomUnderstatClient:
    def players_stats(self, season="2025"):
        raise RuntimeError("understat down")


def _understat_resp(load):
    from src.data.models import UnderstatPlayersResponse
    return UnderstatPlayersResponse.model_validate(load("understat-players.json"))


def test_refresh_populates_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=FakeUnderstatClient(_understat_resp(load)),
    )
    n = conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert n == len(_understat_resp(load).players)
    matched = conn.execute(
        "SELECT COUNT(*) c FROM understat_players WHERE fpl_player_id IS NOT NULL"
    ).fetchone()["c"]
    assert matched >= int(0.95 * n)
    conn.close()


def test_refresh_understat_failure_degrades_gracefully(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=BoomUnderstatClient(),
    )
    # FPL refresh still succeeded despite Understat failure
    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    assert "WARNING" in capsys.readouterr().out
    conn.close()


def test_refresh_source_filter_fpl_only_skips_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    uc = FakeUnderstatClient(_understat_resp(load))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks),
                understat_client=uc, sources=("fpl",))
    assert uc.called is False
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    conn.close()
```

- [x] **Step 5: Run to verify they FAIL**

Run: `.venv/bin/pytest tests/test_cli_refresh.py -v`
Expected: FAIL — `TypeError` (refresh has no `understat_client`/`sources` params) or `NameError`.

- [x] **Step 6: Update `src/cli.py`.** Add imports, an overrides loader, gate the FPL block on `sources`, and add the Understat block. Replace the whole file with:

```python
import argparse
import pathlib
import yaml
from .config import load_config, team_id as cfg_team_id, db_path as cfg_db_path
from .data.db import connect, init_db
from .data.fpl_client import FPLClient
from .data.understat_client import UnderstatClient
from .data import repository, cache, name_resolver

NAME_RESOLUTION_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "name_resolution.yaml"


def _current_gw_from_db(conn):
    row = conn.execute("SELECT id FROM gameweeks WHERE is_current=1").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks WHERE finished=1").fetchone()
    if row and row["id"] is not None:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks").fetchone()
    return row["id"] if row else None


def _load_name_overrides():
    if not NAME_RESOLUTION_PATH.exists():
        return {}
    data = yaml.safe_load(NAME_RESOLUTION_PATH.read_text()) or {}
    return {str(k): int(v) for k, v in data.items()}


def _refresh_fpl(conn, client, tid, full):
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


def _refresh_understat(conn, understat_client, cfg, full):
    # Supplementary data: a failure must NOT break the FPL refresh (R2).
    try:
        if not (full or cache.is_stale(conn, "understat")):
            return
        season = cfg.get("understat", {}).get("season", "2025")
        resp = understat_client.players_stats(season)
        fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
        fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
        res = name_resolver.resolve_players(fpl_players, fpl_teams, resp.players, _load_name_overrides())
        repository.upsert_understat_players(conn, resp.players, res, season)
        cache.mark_fetched(conn, "understat")
        print(f"understat OK (matched {len(res.matched)}/{len(resp.players)}, "
              f"{len(res.unmatched)} unmatched, {len(res.unmapped_teams)} unmapped teams)")
    except Exception as exc:  # noqa: BLE001 - supplementary source degrades gracefully
        print(f"WARNING: understat refresh failed ({exc}); keeping last data")


def refresh(full=False, cfg=None, conn=None, client=None, understat_client=None, sources=None):
    cfg = cfg or load_config()
    sources = sources or ("fpl", "understat")
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)

    if "fpl" in sources:
        _refresh_fpl(conn, client or FPLClient(), cfg_team_id(cfg), full)
    if "understat" in sources:
        _refresh_understat(conn, understat_client or UnderstatClient(), cfg, full)

    if owns_conn:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fpl-autopilot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_refresh = sub.add_parser("refresh", help="fetch FPL + Understat data into the local DB")
    p_refresh.add_argument("--full", action="store_true", help="ignore cache, fetch everything")
    p_refresh.add_argument("--source", choices=["fpl", "understat"], default=None,
                           help="restrict to one source (default: both)")
    args = parser.parse_args(argv)
    if args.command == "refresh":
        sources = (args.source,) if args.source else ("fpl", "understat")
        refresh(full=args.full, sources=sources)


if __name__ == "__main__":
    main()
```

- [x] **Step 7: Update the name-resolution format note in `docs/onboarding.md`** (B13). Find the `name_resolution.yaml` example block:

```yaml
  - fpl_id: 12345
    understat_name: "M. Salah"
```
Replace it with:
```yaml
  # data/name_resolution.yaml maps understat_id -> fpl_id (id-based is robust vs name drift):
  "8260": 12345
```

- [x] **Step 8: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (foundation tests + all new Understat tests + the 3 new CLI tests).

- [x] **Step 9: Verify `--help` shows `--source`**

Run: `.venv/bin/fpl-autopilot refresh --help`
Expected: usage lists `--full` and `--source {fpl,understat}`. (Do NOT run a live refresh here — that is Task 8.)

- [x] **Step 10: Commit**

```bash
git add src/data/cache.py src/cli.py config.yaml data/name_resolution.yaml tests/test_cli_refresh.py docs/onboarding.md
git commit -m "feat: refresh ingests Understat with graceful degradation + --source filter"
```

---

## Task 8: Live smoke test (definition of done)

No new code — the acceptance check from spec §7.

- [x] **Step 1: Live full refresh**

Run: `.venv/bin/fpl-autopilot refresh --full`
Expected: the FPL lines as before, plus e.g. `understat OK (matched ~520/533, ~10 unmatched, 0 unmapped teams)`.

- [x] **Step 2: Verify Understat data landed and resolved**

Run:
```bash
sqlite3 data/fpl_autopilot.db "SELECT
  (SELECT COUNT(*) FROM understat_players) AS total,
  (SELECT COUNT(*) FROM understat_players WHERE fpl_player_id IS NOT NULL) AS matched,
  (SELECT COUNT(*) FROM understat_players WHERE minutes=0) AS zero_min;"
```
Expected: total 500+, matched ≥ 95% of total, no errors.

- [x] **Step 3: Spot-check a joined per-90 value for your squad**

Run:
```bash
sqlite3 -box data/fpl_autopilot.db "SELECT p.web_name, u.xg_per_90, u.xa_per_90
  FROM understat_players u JOIN players p ON p.id = u.fpl_player_id
  WHERE p.id IN (SELECT json_extract(j.value,'\$.element') FROM my_team m, json_each(m.picks_json) j
                 WHERE m.gw=(SELECT MAX(gw) FROM my_team))
  ORDER BY u.xg_per_90 DESC;"
```
Expected: your squad's attackers (e.g. Haaland) show sensible xG/90 values; defenders lower.

- [x] **Step 4: Verify graceful degradation manually (offline simulation already covered by tests)** — confirm a second `refresh` short-circuits Understat via cache:

Run: `.venv/bin/fpl-autopilot refresh`
Expected: no `understat OK` line (fresh within 6h TTL).

- [x] **Step 5: Mark complete**

```bash
git commit --allow-empty -m "chore: Understat ingestion slice complete and smoke-tested"
```

---

## Self-Review notes (author)

- **Spec coverage:** client (T5), models (T2), name resolver incl. team overrides + comma transfers + tiered matching (T4), understat_players table + architecture.md (T3), repository upsert + per-90 (T6), cache TTL + CLI + `--source` + graceful degradation + name_resolution.yaml + onboarding note (T7), live smoke (T8). Fixture (T1). Per-GW / FBref / freshness-confidence deferred per spec §3.
- **Placeholder scan:** none — all code is concrete; team-override map and tiered matching empirically validated at 98%.
- **Type/name consistency:** `resolve_players(fpl_players, fpl_teams, understat_players, overrides)` returns `ResolutionResult(matched, unmatched, unmapped_teams)`, consumed identically in T6 (`resolution.matched.get(up.id)`) and T7. `UnderstatPlayer` fields (`id, player_name, team_title, games, time, goals, assists, xG, xA, npg, npxG`) used consistently across T2/T4/T6. `upsert_understat_players(conn, understat_players, resolution, season)` signature matches T6↔T7. CLI `refresh(..., understat_client, sources)` matches the T7 tests.
```
