# Current-Season Learning (Live GW Stats → Ratings Blend) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed current-season per-GW stats captured from FPL's `event/{id}/live` endpoint (already fetched hourly by settlement) into the xP v2 / FDR v2 rating windows, blended with the 25-26 databank, so the model learns the live season without Vaastav.

**Architecture:** Extend `player_gw_stats` to store the full stat set; auto-backfill GWs settled pre-change. `ratings.py` gains a row-union helper that aggregates live rows per (player, gw) into a synthetic `fpl_live:<season>` source inside the existing LF(38)/SF(6) windows (ordered by season then gw). New-signing guard shrinks live-only rates toward position averages; the v0.21 defensive-captain penalty auto-off rule moves to SF-majority-live detection. Validated by a simulated-season backtest (prior 24-25, live 25-26, no leakage).

**Tech Stack:** Python 3.14, SQLite (stdlib), pytest, existing `src/data/settlement.py`, `src/analytics/ratings.py`, `src/decisions/captain.py`, `docs/research/calibration/backtest.py`.

## Global Constraints

- **Doc-first (B4/B13):** `docs/decision-engine.md` changelog entry v0.23 is written BEFORE any code (Task 1).
- **Never commit without the full suite green:** `.venv/bin/pytest -q` (820 tests) + `cd frontend && npm test` (77 vitest).
- **Never `git add -A`** — stage explicit paths only (worktree gitlinks).
- **Stored model version stays `v2`** (matches v0.20-22 pattern: recalibration, same formula structure).
- **No new blend-weight constants** — natural window (live rows enter LF/SF like databank rows). The ONLY new constants: `MIN_LIVE_RATE_GWS = 3`, `SF_LIVE_MIN = 3`.
- **Live rows authoritative in-season (R9):** if live rows exist for a season, that season's databank rows are excluded from the windows.
- **Settlement backfill never overwrites existing columns** (audit/residuals stability).
- Commit style: conventional (`feat(scope):`, `fix(scope):`, `docs(scope):`, `test(scope):`).

---

### Task 1: Doc-first — decision-engine.md v0.23 entry + v2 data-source paragraph + risks.md R8

**Files:**
- Modify: `docs/decision-engine.md` (v2 §, ~line 174-184; changelog table, after the v0.22 row)
- Modify: `docs/risks.md` (R8 residual-risk paragraph, ~line 171)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the v2 data-source bullet in `docs/decision-engine.md`**

Replace the bullet starting "- **Rates** (xG/St, xA/St, DC/St, saves/90, YC/90, RC/90, starts, p60) come from `player_stats` databank rows, `LF = last 38 GW`, `SF = last 6 GW`, blend 0.8/0.2. Pre-season the windows span the last complete season (25-26). Per-start rates are `Σ stat ÷ Σ starts` (per-90 for saves/YC/RC)." with:

```markdown
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
```

- [ ] **Step 2: Add the v0.23 changelog row**

Append after the v0.22 row (last row of the changelog table):

```markdown
| v0.23 | 2026-08-25 | In-season data source (Vaastav-free learning): ratings now blend current-season per-GW stats captured from FPL's own `event/{id}/live` (already fetched hourly by settlement) with the databank. `player_gw_stats` gains starts/saves/bps/xG/xA/xGC/DC/YC/RC (verified present in the live payload 2026-08-25); GWs settled pre-change are auto-backfilled (GW1 heals on the first refresh). The LF(38)/SF(6) windows span both sources, ordered by season then gw — natural window, no new blend constants; live rows authoritative in-season (R9). New-signing guard `MIN_LIVE_RATE_GWS = 3` (live-only rates shrink toward pooled position averages). The v0.21 defensive-captain penalty now auto-off once the SF window has ≥ 3 live pairs (`SF_LIVE_MIN = 3`; was: databank-rows detection). Backtest: blend simulation (prior 24-25, live 25-26, strict no-leakage) — verdict: SEE TASK 7 (append after the run). |
```

- [ ] **Step 3: Update risks.md R8 residual note**

After the R8 "Residual risk" paragraph ("...they are simply absent from the xP surface..."), append:

```markdown
**Residual risk (updated v0.23):** new signings gain rates from `player_gw_stats`
(`event/{id}/live`) as soon as they play — still noisy until 3 live GWs (rates shrink
toward pooled position averages until then, `MIN_LIVE_RATE_GWS`).
```

- [ ] **Step 4: Verify + commit**

Run: `git diff --stat`
Expected: 2 files modified.

```bash
git add docs/decision-engine.md docs/risks.md
git commit -m "docs(xp): v0.23 — in-season live GW stats blend (changelog + risks)"
```

---

### Task 2: Schema migration + full-stats upsert

**Files:**
- Modify: `src/data/schema.sql` (player_gw_stats CREATE TABLE)
- Modify: `src/data/db.py` (new `_migrate_player_gw_stats`, called in `init_db`)
- Modify: `src/data/repository.py:441-486` (`upsert_player_gw_stats` writes the 9 new fields)
- Modify: `tests/test_settlement.py` (`_live_payload` gains the new keys; new full-stats test)

**Interfaces:**
- Consumes: existing `event/{id}/live` payload shape (`elements[*].stats` + `explain[*].fixture`).
- Produces: `player_gw_stats` rows with columns `starts INTEGER`, `saves INTEGER`, `bps INTEGER`, `expected_goals REAL`, `expected_assists REAL`, `expected_goals_conceded REAL`, `defensive_contribution INTEGER`, `yellow_cards INTEGER`, `red_cards INTEGER` (nullable — old rows keep NULL until backfilled).

- [ ] **Step 1: Write the failing test**

In `tests/test_settlement.py`, extend `_live_payload` (line 40-59) so each element's stats include the new keys (defaulting to 0/0.0):

```python
def _live_payload(elements):
    """Build a minimal FPL event/{id}/live/ payload from a list of (player_id, fixture_id, **stats) dicts."""
    return {
        "elements": [
            {
                "id": el["player_id"],
                "stats": {
                    "minutes": el.get("minutes", 0),
                    "goals_scored": el.get("goals_scored", 0),
                    "assists": el.get("assists", 0),
                    "clean_sheets": el.get("clean_sheets", 0),
                    "bonus": el.get("bonus", 0),
                    "total_points": el.get("total_points", 0),
                    "starts": el.get("starts", 0),
                    "saves": el.get("saves", 0),
                    "bps": el.get("bps", 0),
                    "expected_goals": el.get("expected_goals", 0.0),
                    "expected_assists": el.get("expected_assists", 0.0),
                    "expected_goals_conceded": el.get("expected_goals_conceded", 0.0),
                    "defensive_contribution": el.get("defensive_contribution", 0),
                    "yellow_cards": el.get("yellow_cards", 0),
                    "red_cards": el.get("red_cards", 0),
                },
                "explain": [{"fixture": el["fixture_id"], "stats": []}]
                if el.get("fixture_id") is not None else [],
            }
            for el in elements
        ]
    }
```

Add a new test at the end of the file:

```python
def test_settlement_writes_full_stat_set():
    """v0.23: settlement captures the full per-GW stat set, not just points."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3])
    client = StubFPLClient({3: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 9,
         "starts": 1, "saves": 2, "bps": 28,
         "expected_goals": 0.5, "expected_assists": 0.2,
         "expected_goals_conceded": 1.4, "defensive_contribution": 3,
         "yellow_cards": 1, "red_cards": 0},
    ])})

    written = settlement.settlement_run(conn, client)
    assert written == 1

    row = conn.execute(
        "SELECT starts, saves, bps, expected_goals, expected_assists, "
        "expected_goals_conceded, defensive_contribution, yellow_cards, red_cards "
        "FROM player_gw_stats WHERE player_id=1 AND gw=3").fetchone()
    assert row["starts"] == 1
    assert row["saves"] == 2
    assert row["bps"] == 28
    assert row["expected_goals"] == 0.5
    assert row["expected_assists"] == 0.2
    assert row["expected_goals_conceded"] == 1.4
    assert row["defensive_contribution"] == 3
    assert row["yellow_cards"] == 1
    assert row["red_cards"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_settlement.py::test_settlement_writes_full_stat_set -v`
Expected: FAIL with `sqlite3.OperationalError: no such column: starts`.

- [ ] **Step 3: Implement schema + upsert**

In `src/data/schema.sql`, replace the `player_gw_stats` CREATE TABLE (lines 201-214) with:

```sql
CREATE TABLE IF NOT EXISTS player_gw_stats (
  player_id INTEGER NOT NULL,
  gw INTEGER NOT NULL,
  fixture_id INTEGER NOT NULL,
  minutes INTEGER NOT NULL,
  goals_scored INTEGER NOT NULL,
  assists INTEGER NOT NULL,
  clean_sheets INTEGER NOT NULL,
  bonus INTEGER NOT NULL,
  total_points INTEGER NOT NULL,
  starts INTEGER,
  saves INTEGER,
  bps INTEGER,
  expected_goals REAL,
  expected_assists REAL,
  expected_goals_conceded REAL,
  defensive_contribution INTEGER,
  yellow_cards INTEGER,
  red_cards INTEGER,
  was_substituted_in BOOLEAN,
  settled_at TIMESTAMP NOT NULL,
  PRIMARY KEY (player_id, gw, fixture_id)
);
```

In `src/data/db.py`, add a migration function (after `_migrate_player_stats`, line 62) and call it in `init_db`:

```python
def _migrate_player_gw_stats(conn):
    """v0.23: full per-GW stat capture on player_gw_stats (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player_gw_stats)")}
    for name, decl in [("starts", "INTEGER"), ("saves", "INTEGER"), ("bps", "INTEGER"),
                       ("expected_goals", "REAL"), ("expected_assists", "REAL"),
                       ("expected_goals_conceded", "REAL"),
                       ("defensive_contribution", "INTEGER"),
                       ("yellow_cards", "INTEGER"), ("red_cards", "INTEGER")]:
        if name not in cols:
            conn.execute(f"ALTER TABLE player_gw_stats ADD COLUMN {name} {decl}")
```

In `init_db` (line 81-89), add `_migrate_player_gw_stats(conn)` after `_migrate_player_stats(conn)`.

In `src/data/repository.py` `upsert_player_gw_stats` (lines 465-475), extend the per-element tuple:

```python
            rows.append((
                pid, gw, fixture_id,
                stats.get("minutes", 0),
                stats.get("goals_scored", 0),
                stats.get("assists", 0),
                stats.get("clean_sheets", 0),
                stats.get("bonus", 0),
                stats.get("total_points", 0),
                stats.get("starts", 0),
                stats.get("saves", 0),
                stats.get("bps", 0),
                stats.get("expected_goals", 0.0),
                stats.get("expected_assists", 0.0),
                stats.get("expected_goals_conceded", 0.0),
                stats.get("defensive_contribution", 0),
                stats.get("yellow_cards", 0),
                stats.get("red_cards", 0),
                was_sub,
                now,
            ))
```

and extend the INSERT (lines 478-483):

```python
    cur = conn.executemany(
        """INSERT OR IGNORE INTO player_gw_stats
             (player_id, gw, fixture_id, minutes, goals_scored, assists,
              clean_sheets, bonus, total_points, starts, saves, bps,
              expected_goals, expected_assists, expected_goals_conceded,
              defensive_contribution, yellow_cards, red_cards,
              was_substituted_in, settled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_settlement.py`
Expected: PASS (all settlement tests, including the new one and the pre-existing idempotency/DGW tests).

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/db.py src/data/repository.py tests/test_settlement.py
git commit -m "feat(settlement): capture full per-GW stats from event live"
```

---

### Task 3: Auto-backfill of pre-v0.23 settled GWs

**Files:**
- Modify: `src/data/repository.py` (new `backfill_player_gw_stats` after `upsert_player_gw_stats`)
- Modify: `src/data/settlement.py` (`settlement_run` backfill pass)
- Modify: `tests/test_settlement.py` (backfill tests)

**Interfaces:**
- Consumes: `upsert_player_gw_stats` (Task 2).
- Produces: `repository.backfill_player_gw_stats(conn, gw, payload) -> int` (rows updated); `settlement_run` returns total rows written **including backfilled**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settlement.py`:

```python
def test_settlement_backfills_pre_existing_rows():
    """v0.23: a GW settled before the full-stat columns existed (starts NULL) is
    re-fetched and backfilled; existing columns stay frozen."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1])
    # old-shape row: written by pre-v0.23 code (7 columns, starts NULL)
    conn.execute(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (1, 1, 42, 90, 1, 0, 0, 2, 9, 't')""")
    conn.commit()
    # the re-fetch carries corrected stats (total_points=99 must NOT overwrite 9)
    client = StubFPLClient({1: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 99,
         "starts": 1, "saves": 2, "bps": 28,
         "expected_goals": 0.5, "expected_assists": 0.2,
         "expected_goals_conceded": 1.4, "defensive_contribution": 3,
         "yellow_cards": 1, "red_cards": 0},
    ])})

    written = settlement.settlement_run(conn, client)
    assert written == 1  # backfilled row counts

    row = conn.execute(
        "SELECT total_points, starts, expected_goals FROM player_gw_stats "
        "WHERE player_id=1 AND gw=1").fetchone()
    assert row["total_points"] == 9       # frozen
    assert row["starts"] == 1             # backfilled
    assert row["expected_goals"] == 0.5
    assert client.calls == [1]


def test_settlement_backfill_is_idempotent():
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1])
    conn.execute(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (1, 1, 42, 90, 0, 0, 0, 0, 5, 't')""")
    conn.commit()
    client = StubFPLClient({1: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 5,
         "starts": 1},
    ])})

    first = settlement.settlement_run(conn, client)
    second = settlement.settlement_run(conn, client)

    assert first == 1
    assert second == 0
    assert client.calls == [1]  # second run sees starts already filled


def test_settlement_backfill_failure_is_isolated():
    """A failing backfill re-fetch does not block other GWs (same contract as settlement)."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1, 2])
    conn.executemany(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (?,?,?,90,0,0,0,0,5,'t')""",
        [(1, 1, 42), (2, 2, 50)])
    conn.commit()
    client = StubFPLClient(
        payloads={2: _live_payload([{"player_id": 2, "fixture_id": 50,
                                     "minutes": 90, "total_points": 5, "starts": 1}])},
        raises_on={1},
    )

    settlement.settlement_run(conn, client)

    assert sorted(client.calls) == [1, 2]   # settle pass (gw2) runs before backfill (gw1)
    assert conn.execute("SELECT starts FROM player_gw_stats WHERE gw=2").fetchone()["starts"] == 1
    assert conn.execute("SELECT starts FROM player_gw_stats WHERE gw=1").fetchone()["starts"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_settlement.py -k backfill`
Expected: FAIL with `AttributeError: module 'src.data.repository' has no attribute 'backfill_player_gw_stats'`.

- [ ] **Step 3: Implement backfill**

In `src/data/repository.py`, after `upsert_player_gw_stats` (end of file), add:

```python
def backfill_player_gw_stats(conn, gw, payload):
    """v0.23: fill the 9 full-stat columns on rows settled before they existed.

    UPDATEs keyed on the (player_id, gw, fixture_id) PK, touching ONLY the v0.23
    columns — existing columns stay frozen (residuals/audit stability). Returns the
    number of rows updated.
    """
    if "elements" not in payload:
        raise ValueError(f"event_live payload for gw={gw} missing 'elements' (schema drift?)")
    updates = []
    for el in payload["elements"]:
        stats = el["stats"]
        for ex in el.get("explain") or []:
            updates.append((
                stats.get("starts", 0), stats.get("saves", 0),
                stats.get("bps", 0), stats.get("expected_goals", 0.0),
                stats.get("expected_assists", 0.0),
                stats.get("expected_goals_conceded", 0.0),
                stats.get("defensive_contribution", 0),
                stats.get("yellow_cards", 0), stats.get("red_cards", 0),
                el["id"], gw, ex["fixture"],
            ))
    if not updates:
        return 0
    cur = conn.executemany(
        """UPDATE player_gw_stats SET starts=?, saves=?, bps=?, expected_goals=?,
                expected_assists=?, expected_goals_conceded=?, defensive_contribution=?,
                yellow_cards=?, red_cards=?
           WHERE player_id=? AND gw=? AND fixture_id=?""",
        updates,
    )
    conn.commit()
    return cur.rowcount
```

In `src/data/settlement.py`, extend `settlement_run` — after the existing per-GW settle loop (after line 33), before `return total_written`:

```python
    # v0.23: backfill the full stat set for GWs settled before the columns existed
    # (GW1 heals on the first refresh after deploy). Per-GW failures are isolated.
    for gw in [r["gw"] for r in conn.execute(
            "SELECT DISTINCT gw FROM player_gw_stats WHERE starts IS NULL ORDER BY gw")]:
        try:
            payload = client.event_live(gw)
            n = repository.backfill_player_gw_stats(conn, gw, payload)
            total_written += n
            log.info("settlement.backfill gw=%s rows=%s", gw, n)
        except Exception:
            log.exception(f"settlement backfill failed for gw={gw}")
    return total_written
```

Update the `settlement_run` docstring: "Returns the total rows written (inserted + backfilled) across all GWs in this run."

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_settlement.py`
Expected: PASS (all settlement tests — the two pre-existing tests that assert `client.calls == [3]` and `written == 2` still hold: new rows are written with `starts` = 0, never NULL).

- [ ] **Step 5: Commit**

```bash
git add src/data/repository.py src/data/settlement.py tests/test_settlement.py
git commit -m "feat(settlement): auto-backfill v0.23 stat columns for settled GWs"
```

---

### Task 4: Ratings row union — live rows enter the LF/SF windows

**Files:**
- Modify: `src/config.py` (new `current_season()` after `databank_seasons`)
- Modify: `src/analytics/ratings.py` (`_window_keys`, `_rating_sources`, `_rating_rows`; `_databank_rows` deleted; `compute_team_ratings` + `compute_player_rates` read the union)
- Create: `tests/test_ratings_live.py`

**Interfaces:**
- Consumes: `player_gw_stats` full-stat rows (Tasks 2-3); `config.season` (start year).
- Produces:
  - `config.current_season(cfg=None) -> str` — databank-form label `"2026-27"`.
  - `ratings._rating_sources(conn, live_season=None) -> (list[dict], list[dict])` — (db_rows, live_rows); live rows keyed with `source`, `gw`, `player_id`, `minutes`, `starts`, `saves`, `bps`, `xg`, `xa`, `xgc`, `dc`, `yellow_cards`, `red_cards`, `team_id`, `position`.
  - `ratings._rating_rows(conn, live_season=None) -> list[dict]` — db_rows + live_rows (union).
  - `compute_team_ratings(conn, lf_gw_count=38, sf_gw_count=6, live_season=None)`; `compute_player_rates(conn, lf_gw_count=38, sf_gw_count=6, live_season=None)` — unchanged defaults; existing callers (xp.py, fdr.py, backtest.py, squad_policy.py) keep working.
  - `ratings._season_year(source) -> int`; `ratings.LIVE_SOURCE_PREFIX = "fpl_live:"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ratings_live.py`:

```python
"""v0.23: ratings row union — current-season live rows (player_gw_stats) enter the
LF/SF windows alongside databank rows (natural window, no new blend constants)."""
from src.analytics import ratings
from src.data import repository


def _seed(conn, prior_gws=5):
    """Teams + players; `prior_gws` GWs of 25-26 databank (everyone starts, xg 0.3)."""
    conn.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                     [(1, "Team A", "TA"), (2, "Team B", "TB")])
    conn.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                     "VALUES (?,?,?,?,?, 'a')",
                     [(101, "Starter", "Starter", 1, "MID", "a"),
                      (102, "Keeper", "Keeper", 1, "GKP", "a"),
                      (103, "NewBoy", "NewBoy", 2, "FWD", "a"),
                      (104, "MidB", "MidB", 2, "MID", "a"),
                      (105, "FwdB", "FwdB", 2, "FWD", "a")])
    for gw in range(1, prior_gws + 1):
        repository.upsert_databank_stats(conn, "2025-26", gw, [
            {"element": pid, "name": n, "team": t, "position": p, "minutes": 90,
             "expected_goals": 0.3, "expected_assists": 0.1,
             "expected_goals_conceded": 1.4, "dc": 2, "saves": 0, "starts": 1,
             "bps": 20, "yellow_cards": 0, "red_cards": 0, "was_home": True,
             "value": 5.0, "bonus": 0, "total_points": 5}
            for pid, n, t, p in [(101, "Starter", "Team A", "MID"),
                                 (102, "Keeper", "Team A", "GK"),
                                 (104, "MidB", "Team B", "MID"),
                                 (105, "FwdB", "Team B", "FWD")]])
    conn.commit()


def _live_row(conn, pid, gw, fixture, *, starts=1, minutes=90, xg=0.5, xa=0.2,
              xgc=1.4, dc=2, saves=0, bps=20, yc=0, rc=0, starts_null=False):
    """Insert one full-stat live row (the settlement output shape, v0.23)."""
    cols = ("player_id, gw, fixture_id, minutes, goals_scored, assists, clean_sheets, "
            "bonus, total_points, starts, saves, bps, expected_goals, expected_assists, "
            "expected_goals_conceded, defensive_contribution, yellow_cards, red_cards, "
            "settled_at")
    vals = (pid, gw, fixture, minutes, 0, 0, 0, 0, 5,
            None if starts_null else starts, saves, bps, xg, xa, xgc, dc, yc, rc, "t")
    conn.execute(f"INSERT INTO player_gw_stats ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 vals)
    conn.commit()


def _rates(conn, **kw):
    return ratings.compute_player_rates(conn, **kw)


def test_window_ordering_mixed_sources():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 101, 1, 42)
    rows = ratings._rating_rows(conn)
    keys = ratings._window_keys(rows, 5)
    # last 5 of [(db,1)..(db,5), (live,1)] ordered by season then gw
    assert keys == {(f"fpl_databank:2025-26", g) for g in (2, 3, 4, 5)} | {("fpl_live:2026-27", 1)}
    # SF window (2) = last prior GW + the live GW
    sf = ratings._window_keys(rows, 2)
    assert sf == {("fpl_databank:2025-26", 5), ("fpl_live:2026-27", 1)}


def test_benched_starter_keeps_sane_rotation():
    """A GW1 benching must NOT zero a player: 37/38-style starts stay ~0.97."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 102, 1, 42, starts=0, minutes=0)   # keeper benched GW1
    pr = _rates(conn, lf_gw_count=5, sf_gw_count=2)[102]
    assert pr.starts == 5            # 5 prior starts + 0 live
    assert pr.squads_made == 6       # 5 prior + 1 live team GW
    assert pr.xg_per_start > 0.0     # rates still anchored by prior season


def test_live_dgw_aggregation():
    """DGW: two fixture rows for one (player, gw) aggregate — starts=OR, rest=SUM."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 100, starts=1, minutes=90, xg=0.4)
    _live_row(conn, 101, 1, 101, starts=0, minutes=45, xg=0.6)
    db_rows, live_rows = ratings._rating_sources(conn)
    live = [r for r in live_rows if r["player_id"] == 101]
    assert len(live) == 1
    assert live[0]["starts"] == 1
    assert live[0]["minutes"] == 135
    assert live[0]["xg"] == 1.0


def test_current_season_databank_excluded_when_live_present():
    """R9: with live rows present, the same season's databank rows are excluded."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=1)
    repository.upsert_databank_stats(conn, "2026-27", 1, [
        {"element": 101, "name": "Starter", "team": "Team A", "position": "MID",
         "minutes": 90, "expected_goals": 99.0, "expected_assists": 0.1,
         "expected_goals_conceded": 1.4, "dc": 2, "saves": 0, "starts": 1,
         "bps": 20, "yellow_cards": 0, "red_cards": 0, "was_home": True,
         "value": 5.0, "bonus": 0, "total_points": 5}])
    _live_row(conn, 101, 1, 42, xg=0.5)
    db_rows, live_rows = ratings._rating_sources(conn)
    assert all(r["source"] != "fpl_databank:2026-27" for r in db_rows)
    assert len(live_rows) == 1
    # without live rows the databank row IS included (pre-season path unchanged)
    conn.execute("DELETE FROM player_gw_stats"); conn.commit()
    db_rows2, live_rows2 = ratings._rating_sources(conn)
    assert any(r["source"] == "fpl_databank:2026-27" for r in db_rows2)
    assert live_rows2 == []


def test_new_signing_appears_with_live_only_rates():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 103, 1, 42, xg=1.0, minutes=90, starts=1)   # no prior rows
    rates = _rates(conn, lf_gw_count=5, sf_gw_count=2)
    assert 103 in rates
    pr = rates[103]
    assert pr.starts == 1 and pr.squads_made == 1
    assert 0.0 < pr.xg_per_start <= 1.0


def test_null_starts_live_rows_skipped():
    """Rows not yet backfilled (starts NULL) must not poison the union."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 42, starts_null=True, xg=0.9)
    db_rows, live_rows = ratings._rating_sources(conn)
    assert live_rows == []
    rates = _rates(conn, lf_gw_count=1, sf_gw_count=1)
    assert 101 in rates          # prior-season rates intact


def test_single_live_gw_no_nan():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 42, xg=0.4)
    _live_row(conn, 104, 1, 42, xg=0.1)
    rates = _rates(conn, lf_gw_count=1, sf_gw_count=1)
    assert rates
    for pr in rates.values():
        for v in (pr.xg_per_start, pr.xa_per_start, pr.dc_hit_rate,
                  pr.saves_per_90, pr.yc_per_90, pr.rc_per_90, pr.p60):
            assert v == v and abs(v) < 1e9   # not NaN, not infinite


def test_team_ratings_include_live_gws():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    r_before, _ = ratings.compute_team_ratings(conn, lf_gw_count=5, sf_gw_count=2)
    _live_row(conn, 101, 1, 42, xg=1.5, xgc=3.0)   # team A: one extreme live match
    _live_row(conn, 102, 1, 42, xg=1.5, xgc=3.0)
    r_after, _ = ratings.compute_team_ratings(conn, lf_gw_count=5, sf_gw_count=2)
    assert r_after[1].gw_count == 6                # 5 prior + 1 live
    assert r_after[1].xg90 > r_before[1].xg90
```

The test file needs imports at the top:

```python
from src.data.db import connect, init_db
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py`
Expected: FAIL — `_rating_rows`/`_rating_sources` do not exist (and the window-ordering assertion fails against the string-sorted `_window_keys`).

- [ ] **Step 3: Implement**

In `src/config.py`, after `databank_seasons` (line 35), add:

```python
def current_season(cfg=None):
    """Current FPL season label in databank form ('2026-27'), from config season start year."""
    cfg = cfg if cfg is not None else load_config()
    start = int(cfg.get("season", "2026"))
    return f"{start}-{str((start + 1) % 100).zfill(2)}"
```

In `src/analytics/ratings.py`:

Replace `_databank_rows` (lines 49-53) and `_window_keys` (lines 56-59) with:

```python
LIVE_SOURCE_PREFIX = "fpl_live:"


def _season_year(source):
    """'fpl_databank:2024-25' | 'fpl_live:2026-27' -> 2024 | 2026 (window ordering key)."""
    return int(source.rsplit(":", 1)[1].split("-")[0])


def _window_keys(rows, gw_count):
    """The last `gw_count` distinct (source, gw) pairs across all rows, season-ordered."""
    keys = sorted({(r["source"], r["gw"]) for r in rows},
                  key=lambda k: (_season_year(k[0]), k[1]))
    return set(keys[-gw_count:]) if gw_count > 0 else set()


def _rating_sources(conn, live_season=None):
    """(db_rows, live_rows) — the unified rating-window row set (v0.23).

    db_rows: databank rows (player_stats). For any season whose live rows are present,
    that season's databank rows are excluded — live is authoritative in-season (R9).
    live_rows: player_gw_stats aggregated per (player_id, gw), synthetic source
    'fpl_live:<season>'; rows not yet backfilled (starts IS NULL) are skipped.
    """
    from . import config
    season = live_season or config.current_season()
    live_agg = {}
    for r in conn.execute(
            """SELECT player_id, gw, minutes, starts, saves, bps,
                      expected_goals, expected_assists, expected_goals_conceded,
                      defensive_contribution, yellow_cards, red_cards,
                      p.team_id, p.position
               FROM player_gw_stats gs JOIN players p ON p.id = gs.player_id
               WHERE gs.starts IS NOT NULL"""):
        key = (r["player_id"], r["gw"])
        a = live_agg.setdefault(key, {"minutes": 0.0, "starts": 0, "saves": 0, "bps": 0,
                                      "xg": 0.0, "xa": 0.0, "xgc": 0.0, "dc": 0,
                                      "yellow_cards": 0, "red_cards": 0,
                                      "team_id": r["team_id"], "position": r["position"]})
        a["minutes"] += r["minutes"]
        a["starts"] = max(a["starts"], r["starts"])   # started >=1 fixture (0/1)
        a["saves"] += r["saves"]
        a["bps"] += r["bps"]
        a["xg"] += r["expected_goals"]
        a["xa"] += r["expected_assists"]
        a["xgc"] += r["expected_goals_conceded"]
        a["dc"] += r["defensive_contribution"]
        a["yellow_cards"] += r["yellow_cards"]
        a["red_cards"] += r["red_cards"]
    live_rows = [dict(source=f"{LIVE_SOURCE_PREFIX}{season}", gw=gw, player_id=pid, **a)
                 for (pid, gw), a in sorted(live_agg.items())]
    db_rows = conn.execute(
        """SELECT ps.source, ps.gw, ps.minutes, ps.xg, ps.xgc, ps.dc, ps.starts, ps.xa,
                  ps.saves, ps.yellow_cards, ps.red_cards, p.team_id, p.position
           FROM player_stats ps JOIN players p ON p.id = ps.player_id
           WHERE ps.source LIKE 'fpl_databank:%'""").fetchall()
    db_rows = [dict(r) for r in db_rows]
    if live_rows:
        db_rows = [r for r in db_rows if r["source"] != f"fpl_databank:{season}"]
    return db_rows, live_rows


def _rating_rows(conn, live_season=None):
    db_rows, live_rows = _rating_sources(conn, live_season=live_season)
    return db_rows + live_rows
```

Update `compute_team_ratings` (line 110): signature becomes `(conn, lf_gw_count=LF_GW_COUNT, sf_gw_count=SF_GW_COUNT, live_season=None)`; replace `rows = _databank_rows(conn)` with `rows = _rating_rows(conn, live_season=live_season)`.

Update `compute_player_rates` (line 217): signature becomes `(conn, lf_gw_count=LF_GW_COUNT, sf_gw_count=SF_GW_COUNT, live_season=None)`; replace the `rows = conn.execute(...)` block (lines 223-228) with:

```python
    rows, live_rows = _rating_sources(conn, live_season=live_season)
```

(`live_rows` is unused until Task 5 — keep the name to avoid churn.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py tests/test_fdr_v2.py tests/test_xp_v2.py`
Expected: PASS — new union tests + pre-existing ratings consumers unchanged (no live rows in their fixtures → identical behavior).

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/analytics/ratings.py tests/test_ratings_live.py
git commit -m "feat(ratings): union live GW stats into LF/SF windows (v0.23)"
```

---

### Task 5: New-signing guard (MIN_LIVE_RATE_GWS = 3)

**Files:**
- Modify: `src/analytics/ratings.py` (constant + shrink in `compute_player_rates`)
- Modify: `tests/test_ratings_live.py` (guard tests)

**Interfaces:**
- Consumes: `_rating_sources` (Task 4) — `db_rows`/`live_rows` split.
- Produces: constant `ratings.MIN_LIVE_RATE_GWS = 3`; `PlayerRates` for no-prior players shrunk toward pooled position averages until 3 live GWs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ratings_live.py`:

```python
def test_new_signing_rates_shrink_toward_position_average():
    """v0.23 guard: a player with no prior databank rows and one extreme live GW
    gets w=1/3 live + 2/3 pooled position average (MIN_LIVE_RATE_GWS = 3)."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)                          # MIDs at 0.3 xg/start (101, 104)
    _live_row(conn, 103, 1, 42, xg=1.5, starts=1, minutes=90)   # FWD new signing
    rates = _rates(conn, lf_gw_count=5, sf_gw_count=2)
    pr = rates[103]
    # pooled FWD xg/start over the LF window = 0.3 (prior rows only: 103 had none)
    expected = (1 / 3) * 1.5 + (2 / 3) * 0.3
    assert pr.xg_per_start == pytest.approx(expected, abs=0.02)


def test_new_signing_guard_off_after_three_live_gws():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    for gw in (1, 2, 3):
        _live_row(conn, 103, gw, gw + 100, xg=1.0, starts=1, minutes=90)
    rates = _rates(conn, lf_gw_count=5, sf_gw_count=2)
    assert 103 in rates
    assert rates[103].xg_per_start == pytest.approx(1.0, abs=0.02)   # w = 1.0


def test_guard_does_not_touch_players_with_prior_rows():
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 101, 1, 42, xg=1.5)               # 101 has 5 prior GWs
    rates = _rates(conn, lf_gw_count=5, sf_gw_count=2)
    pr = rates[101]
    assert pr.xg_per_start < 1.5                       # natural blend, no shrink
```

Add `import pytest` at the top of the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py -k shrink`
Expected: FAIL — `test_new_signing_rates_shrink_toward_position_average` expects 0.7 but gets 1.5.

- [ ] **Step 3: Implement**

In `src/analytics/ratings.py`, after the `SF_GW_COUNT`-block constants (after line 24), add:

```python
# v0.23 new-signing guard: players with no prior databank rows have pure-live rates —
# a single GW can be extreme (e.g. a 1.5 xG debut). Shrink toward pooled position
# league averages until MIN_LIVE_RATE_GWS live GWs (decision-engine.md v0.23).
MIN_LIVE_RATE_GWS = 3
```

In `compute_player_rates`, after the existing `out` loop (after line 259, before `return out`), add:

```python
    # v0.23 new-signing guard (see MIN_LIVE_RATE_GWS above)
    if live_rows:
        prior_players = {r["player_id"] for r in db_rows}
        live_counts = {}
        for r in live_rows:
            live_counts[r["player_id"]] = live_counts.get(r["player_id"], 0) + 1
        pos_agg = {}
        for r in rows:
            if (r["source"], r["gw"]) not in lf_keys:
                continue
            a = pos_agg.setdefault(r["position"], [0.0] * 10)
            a[0] += r["minutes"]
            a[1] += r["starts"]
            a[2] += r["xg"]
            a[3] += r["xa"]
            if r["starts"] and r["dc"] >= DC_THRESHOLD.get(r["position"], 9999):
                a[4] += 1
            a[5] += r["saves"]
            a[6] += r["yellow_cards"]
            a[7] += r["red_cards"]
            if r["minutes"] > 0:
                a[8] += 1 if r["minutes"] >= 60 else 0
                a[9] += 1
        pos_avgs = {}
        for pos, a in pos_agg.items():
            starts = a[1] or 1.0
            mins = a[0] or 1.0
            pos_avgs[pos] = {
                "xg": a[2] / starts, "xa": a[3] / starts, "dc": a[4] / starts,
                "saves": a[5] / mins * 90, "yc": a[6] / mins * 90,
                "rc": a[7] / mins * 90, "p60": a[8] / a[9] if a[9] else 0.0,
            }
        for pid, pr in out.items():
            if pid in prior_players:
                continue
            avg = pos_avgs.get(pr.position)
            if avg is None:
                continue
            w = min(1.0, live_counts.get(pid, 0) / MIN_LIVE_RATE_GWS)
            if w >= 1.0:
                continue
            pr.xg_per_start = round(w * pr.xg_per_start + (1 - w) * avg["xg"], 4)
            pr.xa_per_start = round(w * pr.xa_per_start + (1 - w) * avg["xa"], 4)
            pr.dc_hit_rate = round(w * pr.dc_hit_rate + (1 - w) * avg["dc"], 4)
            pr.saves_per_90 = round(w * pr.saves_per_90 + (1 - w) * avg["saves"], 4)
            pr.yc_per_90 = round(w * pr.yc_per_90 + (1 - w) * avg["yc"], 4)
            pr.rc_per_90 = round(w * pr.rc_per_90 + (1 - w) * avg["rc"], 4)
            pr.p60 = round(w * pr.p60 + (1 - w) * avg["p60"], 4)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analytics/ratings.py tests/test_ratings_live.py
git commit -m "feat(ratings): new-signing rate guard MIN_LIVE_RATE_GWS=3"
```

---

### Task 6: v0.21 penalty auto-off — SF majority-live gate

**Files:**
- Modify: `src/analytics/ratings.py` (`SF_LIVE_MIN` + `sf_live_pairs`)
- Modify: `src/decisions/captain.py` (import ratings; replace the hardcoded databank query)
- Modify: `tests/test_ratings_live.py` (gate test)
- Modify: `tests/test_captain.py` (integration test through `get_captain_picks`)

**Interfaces:**
- Consumes: `_rating_rows` (Task 4).
- Produces: `ratings.SF_LIVE_MIN = 3`; `ratings.sf_live_pairs(conn, live_season=None) -> int` (live (season, gw) pairs inside the SF window).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ratings_live.py`:

```python
def test_sf_live_pairs_penalty_gate():
    """v0.23: the pre-season def-penalty gate — SF live pairs < 3 keeps it on."""
    conn = connect(":memory:"); init_db(conn)
    _seed(conn, prior_gws=6)                    # enough prior GWs to fill SF
    assert ratings.sf_live_pairs(conn) == 0     # no live rows -> penalty on
    _live_row(conn, 101, 1, 42)
    assert ratings.sf_live_pairs(conn) == 1
    _live_row(conn, 101, 2, 43)
    assert ratings.sf_live_pairs(conn) == 2
    _live_row(conn, 101, 3, 44)
    assert ratings.sf_live_pairs(conn) == 3
    assert ratings.sf_live_pairs(conn) >= ratings.SF_LIVE_MIN
```

Append to `tests/test_captain.py`:

```python
def _seed_penalty_squad(db, live_gws):
    """Keeper (xp 5.0) vs forward (xp 4.6 + ceiling 0.18): the keeper wins without
    the penalty, loses with it. `live_gws` = count of settled live GWs in the DB."""
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Man City", "MCI"), (2, "Bournemouth", "BOU")])
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (9,'GW9',1),(10,'GW10',0)")
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) "
               "VALUES (1,10,1,2,0)")
    db.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
               "VALUES (1,10,2,3,'t'),(2,10,4,3,'t')")
    db.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                   "VALUES (?,?,?,?,?, 'a')",
                   [(201, "Keeper", "Keeper", 1, "GKP", "a"),
                    (202, "Fwd", "Fwd", 2, "FWD", "a")])
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
               "xassists, xcs, computed_at) VALUES (201,10,'v2',5.0,85.0,0.0,0.0,0,'t')")
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
               "xassists, xcs, computed_at) VALUES (202,10,'v2',4.6,85.0,0.9,0.3,0,'t')")
    picks_json = json.dumps([
        {"element": 201, "position": 1, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False},
        {"element": 202, "position": 2, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False}])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, snapshot_at) "
               "VALUES (10, ?, 0, 0, 't')", (picks_json,))
    # settled live GWs: every player started every one
    db.executemany(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, starts, saves,
           bps, expected_goals, expected_assists, expected_goals_conceded,
           defensive_contribution, yellow_cards, red_cards, settled_at)
           VALUES (?,?,?,90,0,0,0,0,5,1,0,20,0.3,0.1,1.4,2,0,0,'t')""",
        [(pid, gw, gw) for gw in range(1, live_gws + 1)
         for pid in (201, 202)])
    db.commit()


def test_get_captain_picks_penalty_on_with_few_live_gws(db):
    """v0.23: <3 live pairs in the SF window -> GKP penalty still applies."""
    _seed_penalty_squad(db, live_gws=2)
    result = captain.get_captain_picks(db)
    assert result["picks"][0]["player_id"] == 202        # keeper penalized: 3.5 < 4.78
    assert "pre-season" in result["picks"][0]["reason"].lower()


def test_get_captain_picks_penalty_off_at_three_live_gws(db):
    _seed_penalty_squad(db, live_gws=3)
    result = captain.get_captain_picks(db)
    assert result["picks"][0]["player_id"] == 201        # keeper wins: 5.0 > 4.78
    assert "Highest xP (5.0)" in result["picks"][0]["reason"]  # no reorder -> plain reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py tests/test_captain.py`
Expected: FAIL — `ratings.sf_live_pairs` missing; the captain penalty stays on at 3 live GWs.

- [ ] **Step 3: Implement**

In `src/analytics/ratings.py`, after the `MIN_LIVE_RATE_GWS` constant, add:

```python
# v0.23: the v0.21 pre-season defensive-captain penalty applies while the SF window
# has fewer than this many live (season, gw) pairs (i.e. until SF is majority-live).
SF_LIVE_MIN = 3


def sf_live_pairs(conn, live_season=None):
    """Live (season, gw) pairs inside the SF window (v0.23 penalty gate)."""
    rows = _rating_rows(conn, live_season=live_season)
    sf_keys = _window_keys(rows, SF_GW_COUNT)
    return len({(r["source"], r["gw"]) for r in rows
                if r["source"].startswith(LIVE_SOURCE_PREFIX)
                and (r["source"], r["gw"]) in sf_keys})
```

In `src/decisions/captain.py`:
- Add the import at the top (after `from src.decisions import confidence as confidence_mod`): `from src.analytics import ratings`
- Replace the `pre_season` detection block in `get_captain_picks` (lines 138-142):

```python
    # v0.23: pre-season = the SF rating window is not majority-live yet (projections
    # still lean on last season; lineup risk only partially visible). The penalty
    # auto-off rule: SF_LIVE_MIN=3 live pairs (decision-engine.md v0.23).
    pre_season = ratings.sf_live_pairs(conn) < ratings.SF_LIVE_MIN
```
- Update the v0.21 comment block (lines 16-19) to note the v0.23 gate:

```python
# v0.21/v0.23 pre-season defensive penalty: while the SF rating window is not
# majority-live (< 3 live GW pairs), GKP/DEF projections lean on last season's
# team defense and carry lineup risk (GW1 live: a benched keeper captain returned
# 0 pts). Gate: ratings.sf_live_pairs(conn) < ratings.SF_LIVE_MIN.
PRE_SEASON_DEF_PENALTY = 1.5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ratings_live.py tests/test_captain.py`
Expected: PASS (pre-existing captain tests unchanged — their fixtures have no live rows, penalty on as before).

- [ ] **Step 5: Commit**

```bash
git add src/analytics/ratings.py src/decisions/captain.py tests/test_ratings_live.py tests/test_captain.py
git commit -m "feat(captain): v0.23 penalty gate — SF majority-live (SF_LIVE_MIN=3)"
```

---

### Task 7: Simulated-season backtest (blend simulation)

**Files:**
- Modify: `docs/research/calibration/backtest.py` (`_GwMissing`, `_live_payload_rows`, `upsert_players_only`, `run_simulation`, `_report_sim`, `simulate`, argparse in `main`)
- Create: `tests/test_backtest_blend.py` (CI-runnable frozen-slice simulation on synthetic data)
- Modify: `docs/decision-engine.md` (append the real verdict to the v0.23 changelog row — replaces "SEE TASK 7")

**Interfaces:**
- Consumes: `ratings.compute_team_ratings(conn, live_season=...)`, `ratings.compute_player_rates(conn, live_season=...)` (Task 4), `repository.upsert_player_gw_stats` (Task 2), the guard (Task 5), the penalty gate (Task 6).
- Produces: `backtest.run_simulation(sc, prior_season, live_season, rows_for_gw, max_gw=38, feed_live=True) -> list[GWResult]`; `backtest._live_payload_rows(rows, gw) -> dict`; `backtest.upsert_players_only(sc, season, gw, rows)`; `backtest._report_sim(blend, prior)`; CLI `python docs/research/calibration/backtest.py --simulate [--prior 2024-25] [--live 2025-26] [--max-gw 38]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_blend.py`:

```python
"""v0.23 blend simulation (backtest.py run_simulation) on a synthetic scratch DB.

Frozen slice: 8 prior GWs (2024-25) + 6 live GWs (2025-26), 4 teams, no leakage.
The databank CSVs are gitignored, so CI runs this synthetic slice instead of the
full 38-GW CSV run (which stays a manual invocation).
"""
import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKTEST = ROOT / "docs" / "research" / "calibration" / "backtest.py"


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("calib_backtest", BACKTEST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calib_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


TEAMS = {"Alpha": 1, "Beta": 2, "Gamma": 3, "Delta": 4}
# two matches per GW: Alpha v Beta, Gamma v Delta (home team alternates by gw parity)
PAIRS = [(1, 2), (3, 4)]


def _row(element, team, gw, *, xg=0.3, xa=0.1, tp=5, minutes=90, starts=1,
         dc=2, saves=0, yc=0, rc=0, home=None):
    opp = 2 if team == 1 else (1 if team == 2 else (4 if team == 3 else 3))
    is_home = (home if home is not None else ((gw % 2) == 1) == (team <= 2))
    return {"element": element, "name": f"P{element}", "team": team, "position": "MID",
            "minutes": minutes, "expected_goals": xg, "expected_assists": xa,
            "expected_goals_conceded": 1.4, "dc": dc, "saves": saves, "starts": starts,
            "bps": 20, "yellow_cards": yc, "red_cards": rc, "was_home": is_home,
            "value": 5.0, "bonus": 0, "total_points": tp, "opponent_team": opp}


def _gw_rows(gw, players):
    """One full GW of rows for the given players (2 per team, 1 per match pair)."""
    rows = []
    for pid, team in players:
        rows.append(_row(pid, team, gw))
    return rows


def _scratch(bt):
    sc = bt.build_scratch(TEAMS)
    for gw in range(1, 9):
        bt.upsert_rows(sc, "2024-25", gw, _gw_rows(gw, [(i, t) for i, t in enumerate(
            [1, 1, 2, 2, 3, 3, 4, 4], start=1)]))
    return sc


def test_run_simulation_no_leakage_and_adaptation(bt):
    sc = _scratch(bt)
    PRIOR = [(i, t) for i, t in enumerate([1, 1, 2, 2, 3, 3, 4, 4], start=1)]
    live_players = dict(PRIOR)
    live_players[9] = 4   # new signing appears from GW2

    def rows_for_gw(gw):
        if gw > 6:
            raise bt._GwMissing
        out = [dict(r) for r in _gw_rows(gw, [(p, t) for p, t in live_players.items()])]
        for r in out:
            r["expected_goals"] = 1.0 if r["element"] == 1 else r["expected_goals"]
        return out

    blend = bt.run_simulation(sc, "2024-25", "2025-26", rows_for_gw, max_gw=38, feed_live=True)

    # 6 live GWs simulated; all metrics finite (no NaN)
    assert len(blend) == 6
    for r in blend:
        assert math.isfinite(r.mae_v2) and math.isfinite(r.bias_v2)
        assert r.n > 0

    # no leakage: GW1 sees no live rows -> identical to pure prior
    sc2 = bt.build_scratch(TEAMS)
    for gw in range(1, 9):
        bt.upsert_rows(sc2, "2024-25", gw, _gw_rows(gw, [(i, t) for i, t in enumerate(
            [1, 1, 2, 2, 3, 3, 4, 4], start=1)]))
    prior = bt.run_simulation(sc2, "2024-25", "2025-26", rows_for_gw, max_gw=38, feed_live=False)
    assert blend[0].mae_v2 == pytest.approx(prior[0].mae_v2)
    assert blend[0].bias_v2 == pytest.approx(prior[0].bias_v2)
    # adaptation: once live GWs accumulate, blend predictions diverge from pure prior
    assert blend[5].mae_v2 != pytest.approx(prior[5].mae_v2, abs=1e-9) or \
        blend[5].bias_v2 != pytest.approx(prior[5].bias_v2, abs=1e-9)


def test_run_simulation_rates_adapt_to_live_season(bt):
    """Drives the production functions directly: after 3 live GWs the role-change
    player's xg_per_start moved from 0.3 toward 1.0; the new signing (present from
    live GW2, 2 live GWs by GW3) is shrunk toward the MID position average."""
    from src.analytics import ratings
    from src.data import repository
    sc = _scratch(bt)
    base_players = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4}
    for gw in (1, 2, 3):
        players = dict(base_players)
        if gw >= 2:
            players[9] = 4                     # new signing debuts in live GW2
        rows = _gw_rows(gw, [(p, t) for p, t in players.items()])
        for r in rows:
            r["expected_goals"] = 1.0 if r["element"] == 1 else r["expected_goals"]
        bt.upsert_players_only(sc, "2025-26", gw, rows)
        repository.upsert_player_gw_stats(sc.conn, gw, bt._live_payload_rows(rows, gw))
    rates = ratings.compute_player_rates(sc.conn, live_season="2025-26")
    assert rates[1].xg_per_start > 0.35     # moved toward 1.0 (was 0.3)
    assert 9 in rates                        # new signing present
    # 2 live GWs -> w = 2/3: 2/3*1.0 + 1/3*pooled-MID(~0.32) ~= 0.77
    assert 0.5 < rates[9].xg_per_start < 0.95
    assert all(math.isfinite(getattr(rates[p], "xg_per_start")) for p in rates)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_backtest_blend.py`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_simulation'`.

- [ ] **Step 3: Implement**

In `docs/research/calibration/backtest.py`:

(a) Add after `class _LocalSession` (line 76):

```python
class _GwMissing(Exception):
    """Signal from rows_for_gw that the live season has no further GWs."""
```

(b) Add after `upsert_rows` (line 160):

```python
def upsert_players_only(sc, season, gw, rows):
    """Create player rows for a season's GW without writing databank stats
    (mirrors production: bootstrap-static creates every current-season player)."""
    for r in rows:
        tid = sc.teams_by_name.get(r["team"])
        if tid is None:
            continue
        pos = {"GK": "GKP", "AM": "MID"}.get(r["position"], r["position"])
        sc.conn.execute(
            "INSERT INTO players (id, name, web_name, team_id, position, price, status, updated_at) "
            "VALUES (?,?,?,?,?,?,?,'t') ON CONFLICT(id) DO NOTHING",
            (r["element"], r["name"], r["name"], tid, pos, 5.0, "a"))
    sc.conn.commit()


def _live_payload_rows(rows, gw):
    """Map databank-shaped rows to an FPL event/{gw}/live payload (settlement input)."""
    elements = {}
    for r in rows:
        el = elements.setdefault(r["element"], {"id": r["element"], "stats": {
            "minutes": 0, "goals_scored": 0, "assists": 0, "clean_sheets": 0,
            "bonus": 0, "total_points": 0, "starts": 0, "saves": 0, "bps": 0,
            "expected_goals": 0.0, "expected_assists": 0.0,
            "expected_goals_conceded": 0.0, "defensive_contribution": 0,
            "yellow_cards": 0, "red_cards": 0}, "explain": []})
        st = el["stats"]
        st["minutes"] += r["minutes"]
        st["goals_scored"] += r.get("goals_scored", 0)
        st["assists"] += r.get("assists", 0)
        st["clean_sheets"] += r.get("clean_sheets", 0)
        st["bonus"] += r.get("bonus", 0)
        st["total_points"] += r.get("total_points", 0)
        st["starts"] = max(st["starts"], r.get("starts", 0))
        st["saves"] += r.get("saves", 0)
        st["bps"] += r.get("bps", 0)
        st["expected_goals"] += r.get("expected_goals", 0.0)
        st["expected_assists"] += r.get("expected_assists", 0.0)
        st["expected_goals_conceded"] += r.get("expected_goals_conceded", 0.0)
        st["defensive_contribution"] += r.get("dc", 0)
        st["yellow_cards"] += r.get("yellow_cards", 0)
        st["red_cards"] += r.get("red_cards", 0)
        el["explain"].append({"fixture": gw, "stats": []})
    return {"elements": list(elements.values())}
```

(c) Add after `fixtures_for_gw` (line 193):

```python
def run_simulation(sc, prior_season, live_season, rows_for_gw, max_gw=38, feed_live=True):
    """Blend simulation (v0.23): prior_season feeds the databank, live_season is fed
    GW-by-GW as live rows through the production settlement path (player_gw_stats).
    Each live GW is predicted with live rows strictly BEFORE it (no leakage).
    `rows_for_gw(gw)` returns databank-shaped rows or raises _GwMissing. Returns
    per-GW GWResults. feed_live=False runs the pure-prior baseline (identical loop,
    no live rows ever inserted)."""
    results = []
    for gw in range(1, max_gw + 1):
        try:
            rows = rows_for_gw(gw)
        except _GwMissing:
            break
        if not rows:
            break
        team_ratings, la = ratings.compute_team_ratings(sc.conn, live_season=live_season)
        fixtures, _collisions = fixtures_for_gw(sc, rows)
        mults = {x["team_id"]: x for x in fdr.compute_fdr_v2(team_ratings, la, fixtures, {})}
        player_rates = ratings.compute_player_rates(sc.conn, live_season=live_season)

        pred, act = [], []
        for r in rows:
            tid = sc.teams_by_name.get(r["team"])
            if tid is None:
                continue
            venue = "H" if r["was_home"] else "A"
            pr = player_rates.get(r["element"])
            if pr is None or tid not in mults:
                continue
            opp_id = None
            for fx in fixtures:
                if fx["home_team_id"] == tid:
                    opp_id = fx["away_team_id"]
                elif fx["away_team_id"] == tid:
                    opp_id = fx["home_team_id"]
            if opp_id is None:
                continue
            opp_r = team_ratings.get(opp_id)
            dc_ratio = ratings.damp(opp_r.dc90 / la.dc90) if opp_r and la.dc90 else 1.0
            team_xgc90 = team_ratings.get(tid).xgc90 if tid in team_ratings else la.xgc90
            res = xp.compute_player_xp_v2(
                pr.position, "a", 1.0, pr.starts, pr.squads_made,
                pr.xg_per_start, pr.xa_per_start, pr.dc_hit_rate,
                pr.saves_per_90, pr.yc_per_90, pr.rc_per_90, pr.p60,
                team_xgc90, xg_ratio=mults[tid]["fdr_defense_mult"],
                xgc_ratio=mults[tid]["fdr_attack_mult"],
                dc_ratio=dc_ratio, venue=venue)
            pred.append((r["element"], res["xp"]))
            act.append((r["element"], int(r["total_points"])))

        if pred and act:
            act_map = dict(act)
            p2 = [p for e, p in pred if e in act_map]
            a2 = [act_map[e] for e, _p in pred if e in act_map]
            results.append(GWResult(season=live_season, gw=gw, n=len(a2),
                                    mae_v2=_mae(p2, a2), mae_v1=0.0,
                                    bias_v2=(sum(p2) - sum(a2)) / len(a2) if a2 else 0.0,
                                    bias_v1=0.0, cap_v2=0.0, cap_v1=0.0,
                                    cap_win_v2=None, cap_v2c=0.0, cap_win_v2c=None))
        if feed_live:
            upsert_players_only(sc, live_season, gw, rows)
            repository.upsert_player_gw_stats(sc.conn, gw, _live_payload_rows(rows, gw))
    return results
```

(d) Add after `_report` (line 431):

```python
def _report_sim(blend, prior, buckets=((1, 2), (3, 5), (6, 38))):
    """Per-bucket blend vs pure-prior comparison (v0.23 simulation)."""
    print("=== blend simulation: natural-window live rows vs pure prior ===")
    for lo, hi in buckets:
        bs = [r for r in blend if lo <= r.gw <= hi and r.n > 0]
        ps = [r for r in prior if lo <= r.gw <= hi and r.n > 0]
        if not bs:
            continue
        n = sum(r.n for r in bs)
        mae_b = sum(r.mae_v2 * r.n for r in bs) / n
        mae_p = sum(r.mae_v2 * r.n for r in ps) / n
        bias_b = sum(r.bias_v2 * r.n for r in bs) / n
        bias_p = sum(r.bias_v2 * r.n for r in ps) / n
        print(f"  live-GWs {lo}-{hi}: MAE blend {mae_b:.3f} vs prior {mae_p:.3f} "
              f"({'blend better' if mae_b < mae_p else 'prior better'}), "
              f"bias blend {bias_b:+.3f} vs prior {bias_p:+.3f}")
```

(e) Add `simulate()` before `main()` and rewire `main()`:

```python
def simulate(args):
    """--simulate: prior season as databank, live season fed GW-by-GW as live rows."""
    season_maps, canonical = _season_team_maps()
    sc = build_scratch(canonical)
    client = DatabankClient(session=_LocalSession())
    for gw in range(1, 39):
        try:
            raw = client.fetch_gw(args.prior, gw)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                break
            raise
        upsert_rows(sc, args.prior, gw, _canonicalize_rows(
            args.prior, raw, season_maps[args.prior][1], canonical))

    def rows_for_gw(gw):
        try:
            raw = client.fetch_gw(args.live, gw)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise _GwMissing from exc
            raise
        return _canonicalize_rows(args.live, raw, season_maps[args.live][1], canonical)

    blend = run_simulation(sc, args.prior, args.live, rows_for_gw, args.max_gw, feed_live=True)
    sc2 = build_scratch(canonical)
    for gw in range(1, 39):
        try:
            raw = client.fetch_gw(args.prior, gw)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                break
            raise
        upsert_rows(sc2, args.prior, gw, _canonicalize_rows(
            args.prior, raw, season_maps[args.prior][1], canonical))
    prior = run_simulation(sc2, args.prior, args.live, rows_for_gw, args.max_gw, feed_live=False)
    _report_sim(blend, prior)
    return blend, prior


def main():
    import argparse
    ap = argparse.ArgumentParser(description="xP backtest (default) or blend simulation (v0.23)")
    ap.add_argument("--simulate", action="store_true",
                    help="blend simulation: prior season as databank, live season fed as live rows")
    ap.add_argument("--prior", default="2024-25")
    ap.add_argument("--live", default="2025-26")
    ap.add_argument("--max-gw", type=int, default=38)
    args = ap.parse_args()
    if args.simulate:
        simulate(args)
        return
    client = DatabankClient(session=_LocalSession())
    ...existing main() body unchanged...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_backtest_blend.py`
Expected: PASS.

Run the full-slice sanity locally (CSVs exist under `data/databank/`):

```bash
.venv/bin/python docs/research/calibration/backtest.py --simulate --prior 2024-25 --live 2025-26 --max-gw 38
```

Expected: the bucket report prints (live-GWs 1-2, 3-5, 6+); no exception.

- [ ] **Step 5: Append the verdict to the v0.23 changelog row**

Replace "verdict: SEE TASK 7 (append after the run)." in `docs/decision-engine.md` with the actual numbers from the `_report_sim` output, e.g.:

```markdown
verdict: blend MAE x.xxx vs pure-prior x.xxx on live-GWs 6+ (bias +x.xx vs +x.xx) — see simulation output.
```

- [ ] **Step 6: Commit**

```bash
git add docs/research/calibration/backtest.py tests/test_backtest_blend.py docs/decision-engine.md
git commit -m "feat(backtest): blend simulation — prior/live seasons, no-leakage"
```

---

### Task 8: Manual smoke (B14) + final suite

**Files:**
- Modify: none (unless the smoke uncovers a bug — fix + commit separately)

- [ ] **Step 1: Run the smoke against the local dev DB**

```bash
.venv/bin/python - <<'PY'
from src.data.db import connect, init_db
from src.data import settlement
from src.data.fpl_client import FPLClient
from src.analytics import ratings
conn = connect("data/fpl_autopilot.db"); init_db(conn)
n = settlement.settlement_run(conn, FPLClient())
print("settlement inserted+backfilled:", n)
print("rows still missing starts:", conn.execute(
    "SELECT COUNT(*) c FROM player_gw_stats WHERE starts IS NULL").fetchone()["c"])
print("GW1 backfill sample:", list(conn.execute(
    "SELECT player_id, starts, expected_goals, expected_assists, bps "
    "FROM player_gw_stats WHERE gw=1 AND starts IS NOT NULL ORDER BY total_points DESC LIMIT 3")))
rates = ratings.compute_player_rates(conn)
print("players with rates:", len(rates))
print("sf_live_pairs:", ratings.sf_live_pairs(conn))
PY
```

Expected:
- `settlement inserted+backfilled:` ≈ 610 (GW1 rows backfilled; no new unsettled GWs)
- `rows still missing starts:` 0
- `players with rates:` > 0 (previous behavior, now including GW1 evidence)
- `sf_live_pairs:` 1 (penalty still on until 3 live GWs)

If the DB shows `rows still missing starts` > 0, investigate the backfill (payload shape vs stored fixture ids) before proceeding.

- [ ] **Step 2: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (820 + new tests).

Run: `npm test` in `frontend/`
Expected: 77 pass (untouched).

- [ ] **Step 3: Commit any stragglers + final commit**

```bash
git status --short
# stage ONLY intended paths (never git add -A); e.g. leftover doc edits from Task 7's verdict
git commit -m "docs(xp): v0.23 smoke — GW1 backfilled, rates live"
```

- [ ] **Step 4: Report to the user**

Summarize: what shipped, the backtest verdict numbers, the smoke output, and the note that the next scheduled refresh (or a manual `fpl-autopilot refresh`) recomputes FDR/xP with the live-blended windows. Flag open follow-ups: bonus recalibration from 26-27 actuals (~6 GWs in), the formal B5 GW1 review now unblocked, and that Auto mode still waits for 3 GWs of dry-run comparison (item 9).
