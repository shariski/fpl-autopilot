# Leaders Intelligence (Top-100 Cohort Analytics) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track the global top-100 FPL managers per-GW (chips, transfers, hits, bank, value, rank) and surface deterministic patterns — chip timing, transfer discipline, bank/value trends, rank momentum — in a `leaders` CLI and a `/leaders` dashboard with ECharts.

**Architecture:** Data layer fetches standings + entry histories (B6: 1 req/s, schema assertions, cache) into `leader_entries` + `leader_gw_snapshots`; the hourly refresh snapshots whenever a finished GW has no snapshot (settlement-style trigger). `src/analytics/leaders.py` computes deterministic pattern dicts; one `GET /api/leaders` payload feeds both the CLI and the dashboard. ECharts (tree-shaken core) renders heatmap/bar/line charts.

**Tech Stack:** Python 3.14, SQLite, FastAPI, requests, SvelteKit 5, ECharts, vitest, pytest.

## Global Constraints

- **Doc-first (B13):** agent-contract + runbook updated BEFORE code (Task 1).
- **B4 untouched:** analysis only — nothing feeds the decision engine in this slice.
- **B6:** all FPL calls through the client with retry/backoff/1-req-s; schema drift fails loudly; cache standings (per-day) and histories (per-GW).
- **API routes registered before the SPA catch-all mount** (jumbo shadowing lesson).
- **Never commit without the full suite green** (pytest + vitest); never `git add -A`.
- Commit style: conventional (`feat(scope):`, `fix(scope):`, `test(scope):`).

---

### Task 1: Doc-first — agent-contract + runbook

**Files:**
- Modify: `docs/agent-contract.md` (agent-safe list + `leaders` section)
- Modify: `docs/runbook.md` (one-liner + read-safe list)

- [ ] **Step 1: agent-contract.md**

Add `leaders` to the agent-safe list:

```markdown
       "agent_safe_commands": ["status", "resume", "log", "captain", "transfers", "chips",
                               "squad", "insight", "speculate", "refresh", "note", "leaders",
                               "freeze-status", "auth-status", "review"],
```

Add after the `note` section:

```markdown
### leaders (top-100 cohort analytics)

    fpl-autopilot leaders [--refresh] [--json]

Tracks the global top-100 managers' per-GW behavior: chip timing, transfers/hits,
bank & value, rank momentum (deterministic statistics, no AI). `--refresh` pulls a
fresh snapshot (standings + histories, ~102 requests once per settled GW); without
it, reads the stored data. Agent-safe: `--refresh` writes the local DB only, never FPL.
Shape: {"cohort": [...], "patterns": {"chip_timing", "transfers", "bank_value", "momentum"}} —
documented in docs/decision-engine.md-style analysis notes; same shape as GET /api/leaders.
```

- [ ] **Step 2: runbook.md**

Add `leaders` to the read-safe list and a one-liner after the speculation insights paragraph:

```markdown
**Leader analytics:** `docker compose --project-directory /opt/fpl-autopilot run --rm -T app leaders --json` —
the global top-100's chip timing, transfer discipline, bank/value and rank momentum patterns.
```

- [ ] **Step 3: Verify + commit**

Run: `git diff --stat` — 2 files. Commit:

```bash
git add docs/agent-contract.md docs/runbook.md
git commit -m "docs(leaders): leaders command + agent-safe list"
```

---

### Task 2: FPLClient — leagues_classic + entry_history

**Files:**
- Modify: `src/data/fpl_client.py` (two methods after `event_live`)
- Modify: `tests/test_fpl_client.py` (or add to `tests/test_settlement.py`-style client test file — check existing: `test_fpl_client.py`)

**Interfaces:**
- Produces: `FPLClient.leagues_classic(league_id, page=1) -> dict` (raw standings payload); `FPLClient.entry_history(entry_id) -> dict` (raw history payload). Both via `_get` (retry/backoff preserved). Schema assertions live in the consumer (`src/data/leaders.py`, Task 4) per B6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fpl_client.py` (mirror the existing `test_event_live_returns_expected_shape` FakeSession pattern):

```python
def test_leagues_classic_returns_standings():
    from src.data.fpl_client import FPLClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {"standings": {"results": [{"entry": 1, "rank": 1,
                                               "player_name": "A", "entry_name": "B",
                                               "total": 227}]}}
        def raise_for_status(self): pass

    class FakeSession:
        headers = {}
        def get(self, url, params=None, timeout=None):
            assert "leagues-classic/314/standings" in url
            assert params == {"page_standings": 2}
            return FakeResp()

    client = FPLClient(session=FakeSession(), sleep=lambda _: None, monotonic=lambda: 0.0)
    out = client.leagues_classic(314, page=2)
    assert out["standings"]["results"][0]["entry"] == 1


def test_entry_history_returns_current_and_past():
    from src.data.fpl_client import FPLClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {"current": [{"event": 1, "points": 91}], "past": [{"season_name": "2025/26"}]}
        def raise_for_status(self): pass

    class FakeSession:
        headers = {}
        def get(self, url, params=None, timeout=None):
            assert "entry/4829085/history" in url
            return FakeResp()

    client = FPLClient(session=FakeSession(), sleep=lambda _: None, monotonic=lambda: 0.0)
    out = client.entry_history(4829085)
    assert out["current"][0]["event"] == 1 and out["past"][0]["season_name"] == "2025/26"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_fpl_client.py -k "leagues or entry_history"`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Implement**

In `src/data/fpl_client.py`, after `event_live`:

```python
def leagues_classic(self, league_id, page=1):
    # Raw standings payload (schema assertions live in the leaders consumer, B6).
    return self._get(f"leagues-classic/{league_id}/standings/",
                     params={"page_standings": page})

def entry_history(self, entry_id):
    # Raw per-GW history payload: current[] (per-GW stats), past[] (season
    # summaries), chips[] (name + event). Schema assertions live in the consumer.
    return self._get(f"entry/{entry_id}/history/")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_fpl_client.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/data/fpl_client.py tests/test_fpl_client.py
git commit -m "feat(fpl-client): leagues_classic + entry_history endpoints"
```

---

### Task 3: Data layer — leader_entries + leader_gw_snapshots

**Files:**
- Modify: `src/data/schema.sql` (2 tables)
- Modify: `src/data/repository.py` (upsert + read helpers)
- Create: `tests/test_leaders_repo.py`

**Interfaces:**
- Produces: `repository.upsert_leader_entry(conn, entry_id, player_name, entry_name, past_rank, past_pts, first_gw, rank, total)`; `repository.upsert_leader_snapshot(conn, entry_id, gw, points, total_points, overall_rank, bank, value, transfers, hit_cost, chip_played)` (chip COALESCE-updated); `repository.latest_leader_gw(conn) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leaders_repo.py`:

```python
"""v0.27: leaders cohort — repository round-trip."""
from src.data import repository


def test_leader_entry_upsert(db):
    repository.upsert_leader_entry(db, 4829085, "Harman Messi", "shadi",
                                   past_rank=12310989, past_pts=1144,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_entry(db, 4829085, "Harman Messi", "shadi",
                                   past_rank=12310989, past_pts=1144,
                                   first_gw=1, rank=2, total=265)
    row = db.execute("SELECT * FROM leader_entries WHERE entry_id=4829085").fetchone()
    assert row["last_rank"] == 2 and row["last_total"] == 265


def test_leader_snapshot_upsert_and_chip_coalesce(db):
    repository.upsert_leader_snapshot(db, 1, 2, points=38, total_points=265,
                                      overall_rank=12900, bank=4, value=1000,
                                      transfers=1, hit_cost=0, chip_played=None)
    repository.upsert_leader_snapshot(db, 1, 2, points=38, total_points=265,
                                      overall_rank=12900, bank=4, value=1000,
                                      transfers=1, hit_cost=0, chip_played="3xc")
    row = db.execute("SELECT * FROM leader_gw_snapshots WHERE entry_id=1 AND gw=2").fetchone()
    assert row["chip_played"] == "3xc"     # late chip arrival fills the slot


def test_latest_leader_gw(db):
    assert repository.latest_leader_gw(db) is None
    repository.upsert_leader_snapshot(db, 1, 1, 0, 0, 0, 0, 0, 0, 0, None)
    repository.upsert_leader_snapshot(db, 1, 3, 0, 0, 0, 0, 0, 0, 0, None)
    assert repository.latest_leader_gw(db) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_leaders_repo.py`
Expected: FAIL — no such table.

- [ ] **Step 3: Implement**

`schema.sql` append:

```sql
CREATE TABLE IF NOT EXISTS leader_entries (
  entry_id INTEGER PRIMARY KEY,
  player_name TEXT,
  entry_name TEXT,
  past_season_rank INTEGER,
  past_season_pts INTEGER,
  first_seen_gw INTEGER,
  last_rank INTEGER,
  last_total INTEGER,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leader_gw_snapshots (
  entry_id INTEGER NOT NULL,
  gw INTEGER NOT NULL,
  points INTEGER,
  total_points INTEGER,
  overall_rank INTEGER,
  bank INTEGER,
  value INTEGER,
  event_transfers INTEGER,
  hit_cost INTEGER,
  chip_played TEXT,
  PRIMARY KEY (entry_id, gw)
);
```

`repository.py` append:

```python
def upsert_leader_entry(conn, entry_id, player_name, entry_name, past_rank, past_pts,
                        first_gw, rank, total):
    conn.execute(
        """INSERT INTO leader_entries (entry_id, player_name, entry_name, past_season_rank,
           past_season_pts, first_seen_gw, last_rank, last_total, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(entry_id) DO UPDATE SET
             player_name=excluded.player_name, entry_name=excluded.entry_name,
             past_season_rank=excluded.past_season_rank,
             past_season_pts=excluded.past_season_pts,
             last_rank=excluded.last_rank, last_total=excluded.last_total,
             updated_at=excluded.updated_at""",
        (entry_id, player_name, entry_name, past_rank, past_pts, first_gw, rank, total, _now()))
    conn.commit()


def upsert_leader_snapshot(conn, entry_id, gw, points, total_points, overall_rank,
                           bank, value, transfers, hit_cost, chip_played):
    conn.execute(
        """INSERT INTO leader_gw_snapshots (entry_id, gw, points, total_points,
           overall_rank, bank, value, event_transfers, hit_cost, chip_played)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(entry_id, gw) DO UPDATE SET
             points=excluded.points, total_points=excluded.total_points,
             overall_rank=excluded.overall_rank, bank=excluded.bank,
             value=excluded.value, event_transfers=excluded.event_transfers,
             hit_cost=excluded.hit_cost,
             chip_played=COALESCE(excluded.chip_played, leader_gw_snapshots.chip_played)""",
        (entry_id, gw, points, total_points, overall_rank, bank, value,
         transfers, hit_cost, chip_played))
    conn.commit()


def latest_leader_gw(conn):
    row = conn.execute("SELECT MAX(gw) AS gw FROM leader_gw_snapshots").fetchone()
    return row["gw"] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_leaders_repo.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/repository.py tests/test_leaders_repo.py
git commit -m "feat(leaders): leader_entries + leader_gw_snapshots storage"
```

---

### Task 4: Fetch flow — snapshot module + scheduler trigger

**Files:**
- Create: `src/data/leaders.py`
- Modify: `src/scheduler.py` (trigger after settlement + report counts)
- Create: `tests/test_leaders_fetch.py`

**Interfaces:**
- Consumes: FPLClient methods (Task 2), repository (Task 3).
- Produces: `leaders.fetch_leader_snapshot(conn, client, pages=2, league_id=314) -> (entries, snapshots)`; raises `ValueError` on schema drift (B6), never partial-writes silently.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leaders_fetch.py`:

```python
"""v0.27: leaders snapshot fetch — standings + histories → DB."""
import pytest

from src.data import leaders
from src.data.db import connect, init_db


class StubClient:
    def __init__(self, standings, histories):
        self._standings = standings
        self._histories = histories
        self.calls = []

    def leagues_classic(self, league_id, page=1):
        self.calls.append(("standings", page))
        return {"standings": {"results": self._standings[page]}}

    def entry_history(self, entry_id):
        self.calls.append(("history", entry_id))
        return self._histories[entry_id]


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1),(2,'GW2',0)")
    conn.commit()
    return conn


def _standings(n):
    return [[{"entry": i, "rank": i, "player_name": f"P{i}", "entry_name": f"E{i}",
              "total": 200 - i} for i in range(1, n + 1)] for _ in range(2)]


def test_fetch_snapshots_latest_settled_gw(monkeypatch):
    conn = _db()
    hist = {i: {"current": [{"event": 1, "points": 90, "total_points": 200,
                             "overall_rank": i, "bank": 10, "value": 1000,
                             "event_transfers": 1, "event_transfers_cost": 0}],
                "past": [{"season_name": "2025/26", "rank": 1000000, "total_points": 2000}],
                "chips": [{"name": "3xc", "event": 1}]} for i in range(1, 4)}
    client = StubClient(_standings(3), hist)
    n_e, n_s = leaders.fetch_leader_snapshot(conn, client, pages=2)
    assert n_e == 3 and n_s == 3
    row = conn.execute("SELECT * FROM leader_gw_snapshots WHERE entry_id=1").fetchone()
    assert row["gw"] == 1 and row["chip_played"] == "3xc" and row["points"] == 90
    ent = conn.execute("SELECT * FROM leader_entries WHERE entry_id=1").fetchone()
    assert ent["past_season_rank"] == 1000000 and ent["last_rank"] == 1
    # idempotent: re-run snapshots nothing new (gw2 unfinished)
    assert leaders.fetch_leader_snapshot(conn, client, pages=2) == (0, 0)


def test_fetch_skips_when_no_unsettled_gw(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    assert leaders.fetch_leader_snapshot(conn, StubClient([[]], {}), pages=2) == (0, 0)


def test_fetch_schema_drift_fails_loudly(monkeypatch):
    conn = _db()

    class BadClient:
        def leagues_classic(self, league_id, page=1):
            return {"standings": {}}
        def entry_history(self, entry_id):
            return {}

    with pytest.raises(ValueError):
        leaders.fetch_leader_snapshot(conn, BadClient(), pages=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_leaders_fetch.py`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/data/leaders.py`:

```python
"""Top-100 leaders snapshot (v0.27): standings + per-entry histories into the DB.

Runs once per settled GW (settlement-style trigger). B6: schema drift raises
ValueError (never a silent partial write); the caller swallows + logs.
"""
import logging

log = logging.getLogger(__name__)

GLOBAL_LEAGUE_ID = 314
CHIP_NAMES = {"wildcard": "wildcard", "free_hit": "free_hit",
              "bench_boost": "bench_boost", "3xc": "3xc"}


def _unsettled_gw(conn):
    return conn.execute(
        """SELECT MAX(id) AS gw FROM gameweeks
           WHERE finished=1 AND id NOT IN (SELECT DISTINCT gw FROM leader_gw_snapshots)"""
    ).fetchone()["gw"]


def fetch_leader_snapshot(conn, client, pages=2, league_id=GLOBAL_LEAGUE_ID):
    """Fetch standings (top-100) + each entry's history; upsert entries + the
    settled GW's snapshot. Returns (entries_written, snapshots_written)."""
    from . import repository
    gw = _unsettled_gw(conn)
    if gw is None:
        return 0, 0
    standings = []
    for page in range(1, pages + 1):
        payload = client.leagues_classic(league_id, page)
        results = (payload.get("standings") or {}).get("results")
        if not isinstance(results, list):
            raise ValueError("leagues-classic payload missing standings.results (schema drift?)")
        standings.extend(results)
    n_e = n_s = 0
    for r in standings:
        eid = r.get("entry")
        if eid is None:
            continue
        history = client.entry_history(eid)
        current = history.get("current")
        if not isinstance(current, list):
            raise ValueError(f"entry/{eid} history missing current[] (schema drift?)")
        past = (history.get("past") or [{}])[0]
        row = next((x for x in current if x.get("event") == gw), None)
        chip = None
        for c in history.get("chips") or []:
            if c.get("event") == gw and c.get("name") in CHIP_NAMES:
                chip = CHIP_NAMES[c["name"]]
        repository.upsert_leader_entry(
            conn, eid, r.get("player_name"), r.get("entry_name"),
            past.get("rank"), past.get("total_points"), gw,
            r.get("rank"), r.get("total"))
        n_e += 1
        if row is not None:
            repository.upsert_leader_snapshot(
                conn, eid, gw, row.get("points"), row.get("total_points"),
                row.get("overall_rank"), row.get("bank"), row.get("value"),
                row.get("event_transfers"), row.get("event_transfers_cost"), chip)
            n_s += 1
    log.info("leaders.snapshot gw=%s entries=%s snapshots=%s", gw, n_e, n_s)
    return n_e, n_s
```

`src/scheduler.py` — in `refresh_and_recompute`, after the settlement block:

```python
        try:
            from .data.leaders import fetch_leader_snapshot
            n_le, n_ls = fetch_leader_snapshot(conn, client or FPLClient())
        except Exception:
            log.exception("leaders.snapshot_failed")
            n_le = n_ls = 0
```

and include in the report dict:

```python
            return {**(rpt or {}),
                    "recompute": {"fdr_v1": fdr_n, "fdr_v2": fdr_v2_n,
                                  "xp_v1": xp_n, "xp_v2": xp_v2_n},
                    "settlement_written": settle_n,
                    "leaders": {"entries": n_le, "snapshots": n_ls}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_leaders_fetch.py tests/test_scheduler.py`
Expected: PASS (existing scheduler pipeline-order test asserts calls == ["refresh", "fdr", "xp", "ping"] — the leaders fetch is additive and wrapped; verify it still passes; if the monkeypatched pipeline test fails because fetch_leader_snapshot imports, monkeypatch it there too).

- [ ] **Step 5: Commit**

```bash
git add src/data/leaders.py src/scheduler.py tests/test_leaders_fetch.py
git commit -m "feat(leaders): per-GW snapshot fetch + scheduler trigger"
```

---

### Task 5: Analysis — src/analytics/leaders.py

**Files:**
- Create: `src/analytics/leaders.py`
- Create: `tests/test_leaders_analysis.py`

**Interfaces:**
- Consumes: `leader_gw_snapshots` + `leader_entries` (Task 3).
- Produces: `chip_timing(conn)`, `transfer_discipline(conn)`, `bank_value(conn)`, `rank_momentum(conn)`, `cohort_stats(conn)`, and `analyze(conn) -> dict` (the /api/leaders payload). All pure/deterministic.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leaders_analysis.py`:

```python
"""v0.27: leaders pattern analysis — deterministic statistics."""
from statistics import median

from src.analytics import leaders as la
from src.data import repository


def _seed(db, gws=(1, 2)):
    for eid in (1, 2, 3):
        repository.upsert_leader_entry(db, eid, f"P{eid}", f"E{eid}",
                                       past_rank=(50000 if eid == 1 else 9000000),
                                       past_pts=2000, first_gw=1, rank=eid, total=100)
    for eid in (1, 2, 3):
        for gw in gws:
            repository.upsert_leader_snapshot(db, eid, gw,
                points=60 if gw == 1 else 30, total_points=100 + 30 * gw,
                overall_rank=eid * 10 + gw, bank=5, value=1000 + gw,
                transfers=1 if gw == 1 else 0, hit_cost=4 if (eid == 1 and gw == 1) else 0,
                chip_played="3xc" if (eid == 1 and gw == 1) else None)


def test_chip_timing_cluster(db):
    _seed(db)
    out = la.chip_timing(db)
    row = next(r for r in out["rows"] if r["chip"] == "3xc" and r["gw"] == 1)
    assert row["count"] == 1
    assert out["first_chip"]["3xc"]["gw"] == 1


def test_transfer_discipline(db):
    _seed(db)
    out = la.transfer_discipline(db)
    # 6 leader-GWs: transfers = [1,0,1,0,1,0]; hits: one gw with cost 4
    assert out["mean_per_gw"] == 0.5
    assert out["median_per_gw"] == 0.5
    assert out["hit_freq"] == 1 / 6
    assert out["mean_hit_cost"] == 4.0
    assert {h["transfers"]: h["count"] for h in out["histogram"]} == {0: 3, 1: 3}


def test_bank_value_trajectories(db):
    _seed(db)
    out = la.bank_value(db)
    assert out["bank"][0]["gw"] == 1 and out["bank"][0]["mean"] == 5.0
    assert out["value"][1]["mean"] == 1002.0


def test_rank_momentum_and_sustained_elite(db):
    _seed(db)
    out = la.rank_momentum(db)
    # entry 1: rank 11 -> 12 (fell), entry 3: rank 13 -> 32... use gw1->gw2 deltas
    assert out["sustained_elite"] == [1]          # only entry 1 has past rank <= 250000
    assert len(out["top_movers"]) == 3


def test_cohort_stats(db):
    _seed(db)
    out = la.cohort_stats(db)
    assert len(out) == 3
    e1 = next(x for x in out if x["entry_id"] == 1)
    assert e1["chips_used"] == ["3xc"] and e1["past_rank"] == 50000


def test_analyze_empty_db_guards(db):
    out = la.analyze(db)
    assert out["cohort"] == [] and out["patterns"]["chip_timing"]["rows"] == []
    assert out["patterns"]["transfers"]["mean_per_gw"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_leaders_analysis.py`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/analytics/leaders.py`:

```python
"""Top-100 leaders pattern analysis (v0.27). Deterministic statistics — no AI.

Consumes leader_entries + leader_gw_snapshots (written by src/data/leaders.py).
Every function returns plain dicts; empty-DB guards return empty structures.
"""
from statistics import mean, median

SUSTAINED_ELITE_MAX_RANK = 250_000  # top ~5% of 25-26 (12.3M entries)


def chip_timing(conn):
    rows = [dict(r) for r in conn.execute(
        """SELECT chip_played AS chip, gw, COUNT(*) AS count
           FROM leader_gw_snapshots WHERE chip_played IS NOT NULL
           GROUP BY chip_played, gw ORDER BY gw, chip_played""")]
    first_chip = {}
    for r in rows:
        first_chip.setdefault(r["chip"], {"gw": r["gw"], "count": r["count"]})
    return {"rows": rows, "first_chip": first_chip}


def transfer_discipline(conn):
    all_rows = [dict(r) for r in conn.execute(
        "SELECT event_transfers, hit_cost FROM leader_gw_snapshots")]
    if not all_rows:
        return {"mean_per_gw": None, "median_per_gw": None, "hit_freq": None,
                "mean_hit_cost": None, "histogram": []}
    transfers = [r["event_transfers"] for r in all_rows]
    hits = [r["hit_cost"] for r in all_rows if r["hit_cost"]]
    hist = {}
    for t in transfers:
        hist[t] = hist.get(t, 0) + 1
    return {"mean_per_gw": round(mean(transfers), 3),
            "median_per_gw": median(transfers),
            "hit_freq": round(len(hits) / len(all_rows), 3),
            "mean_hit_cost": round(mean(hits), 2) if hits else 0.0,
            "histogram": [{"transfers": t, "count": c}
                          for t, c in sorted(hist.items())]}


def bank_value(conn):
    def _series(col):
        out = []
        for r in conn.execute(
                f"SELECT gw, AVG({col}) AS m, COUNT(*) AS n FROM leader_gw_snapshots "
                f"GROUP BY gw ORDER BY gw"):
            vals = [x[col] for x in conn.execute(
                f"SELECT {col} FROM leader_gw_snapshots WHERE gw=? ORDER BY {col}",
                (r["gw"],))]
            out.append({"gw": r["gw"], "mean": round(r["m"], 2),
                        "median": median(vals)})
        return out
    return {"bank": _series("bank"), "value": _series("value")}


def rank_momentum(conn):
    rows = [dict(r) for r in conn.execute(
        """SELECT entry_id, gw, overall_rank FROM leader_gw_snapshots
           ORDER BY entry_id, gw""")]
    series = {}
    for r in rows:
        series.setdefault(r["entry_id"], []).append(r)
    movers = []
    for eid, pts in series.items():
        for prev, cur in zip(pts, pts[1:]):
            gain = prev["overall_rank"] - cur["overall_rank"]  # positive = climbed
            movers.append({"entry_id": eid, "from_gw": prev["gw"], "to_gw": cur["gw"],
                           "rank_gain": gain})
    movers.sort(key=lambda m: -m["rank_gain"])
    elite = [r["entry_id"] for r in conn.execute(
        "SELECT entry_id FROM leader_entries WHERE past_season_rank IS NOT NULL "
        "AND past_season_rank <= ?", (SUSTAINED_ELITE_MAX_RANK,))]
    return {"top_movers": movers[:10], "sustained_elite": elite}


def cohort_stats(conn):
    rows = conn.execute(
        """SELECT e.entry_id, e.player_name, e.entry_name, e.last_rank AS rank,
                  e.last_total AS total, e.past_season_rank AS past_rank,
                  s.points AS last_gw_points, s.event_transfers AS transfers,
                  s.hit_cost, s.bank, s.value, s.chip_played
           FROM leader_entries e
           LEFT JOIN leader_gw_snapshots s
             ON s.entry_id = e.entry_id
            AND s.gw = (SELECT MAX(gw) FROM leader_gw_snapshots s2
                        WHERE s2.entry_id = e.entry_id)
           ORDER BY e.last_rank""").fetchall()
    chips = {}
    for r in conn.execute(
            "SELECT entry_id, chip_played FROM leader_gw_snapshots "
            "WHERE chip_played IS NOT NULL"):
        chips.setdefault(r["entry_id"], []).append(r["chip_played"])
    out = []
    for r in rows:
        d = dict(r)
        d["chips_used"] = chips.get(r["entry_id"], [])
        out.append(d)
    return out


def analyze(conn):
    return {"cohort": cohort_stats(conn),
            "patterns": {"chip_timing": chip_timing(conn),
                         "transfers": transfer_discipline(conn),
                         "bank_value": bank_value(conn),
                         "momentum": rank_momentum(conn)}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_leaders_analysis.py`
Expected: PASS. (Adjust the expected numbers in the tests only if the seed math disagrees — recompute by hand first.)

- [ ] **Step 5: Commit**

```bash
git add src/analytics/leaders.py tests/test_leaders_analysis.py
git commit -m "feat(leaders): deterministic pattern analysis (chip/transfers/bank/momentum)"
```

---

### Task 6: API — GET /api/leaders

**Files:**
- Modify: `src/interface/api.py` (route BEFORE the static mount — jumbo lesson)
- Modify: `tests/test_leaders_api.py` (create)

**Interfaces:**
- Consumes: `leaders.analyze(conn)` (Task 5).
- Produces: `GET /api/leaders` → the analyze payload.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leaders_api.py` (client fixture pattern from `test_speculation_notes_api.py`):

```python
"""v0.27: GET /api/leaders — cohort + patterns payload."""
import pytest
from fastapi.testclient import TestClient

from src.data.db import connect, init_db
from src.data import repository
from src.interface import api
from src.interface.deps import get_db


@pytest.fixture
def client():
    conn = connect(":memory:", check_same_thread=False)
    init_db(conn)

    def override_get_db():
        try:
            yield conn
        finally:
            pass

    app = api.app
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def test_leaders_empty(client):
    tc, _ = client
    r = tc.get("/api/leaders")
    assert r.status_code == 200
    body = r.json()
    assert body["cohort"] == [] and body["patterns"]["chip_timing"]["rows"] == []


def test_leaders_with_seeded_snapshots(client):
    tc, conn = client
    repository.upsert_leader_entry(conn, 1, "P1", "E1", past_rank=100, past_pts=2000,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_snapshot(conn, 1, 1, 91, 227, 10937, 0, 1000,
                                      0, 0, "3xc")
    r = tc.get("/api/leaders")
    body = r.json()
    assert body["cohort"][0]["entry_id"] == 1
    assert body["cohort"][0]["chips_used"] == ["3xc"]
    assert body["patterns"]["chip_timing"]["rows"] == [
        {"chip": "3xc", "gw": 1, "count": 1}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_leaders_api.py`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement**

In `src/interface/api.py`, add BEFORE the `# --- Static frontend` section:

```python
from src.analytics import leaders as leaders_analytics


@app.get("/api/leaders")
def leaders(conn=Depends(get_db)):
    return leaders_analytics.analyze(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_leaders_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/interface/api.py tests/test_leaders_api.py
git commit -m "feat(api): GET /api/leaders — cohort + patterns payload"
```

---

### Task 7: CLI — leaders command

**Files:**
- Modify: `src/cli.py` (parser + handler `_cmd_leaders_cli` + dispatch + agent-safe list)
- Create: `tests/test_cli_leaders.py`

**Interfaces:**
- Consumes: `leaders.fetch_leader_snapshot` (Task 4) + `leaders_analytics.analyze` (Task 5).
- Produces: `fpl-autopilot leaders [--refresh] [--json]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_leaders.py`:

```python
"""v0.27: leaders CLI — read + --refresh."""
import json
from argparse import Namespace

from src import cli
from src.data import repository


def _seed(db):
    repository.upsert_leader_entry(db, 1, "P1", "E1", past_rank=100, past_pts=2000,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_snapshot(db, 1, 1, 91, 227, 10937, 0, 1000, 0, 0, "3xc")


def test_leaders_read_json(db, capsys):
    _seed(db)
    cli._cmd_leaders_cli(Namespace(refresh=False, json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["data"]["cohort"][0]["entry_id"] == 1
    assert out["data"]["patterns"]["chip_timing"]["rows"][0]["count"] == 1


def test_leaders_refresh_calls_fetch(db, capsys, monkeypatch):
    from src.data import leaders as leaders_data
    seen = {}

    def fake_fetch(conn, client, pages=2, league_id=314):
        seen["called"] = True
        return (3, 3)

    monkeypatch.setattr(leaders_data, "fetch_leader_snapshot", fake_fetch)
    cli._cmd_leaders_cli(Namespace(refresh=True, json=True), conn=db)
    assert seen["called"] is True
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_cli_leaders.py`
Expected: FAIL — `_cmd_leaders_cli` missing.

- [ ] **Step 3: Implement**

In `src/cli.py`:

Parser (after the `note` parsers):

```python
    p_leaders = sub.add_parser("leaders", help="top-100 cohort analytics (chip timing, transfers, bank, momentum)")
    p_leaders.add_argument("--refresh", action="store_true",
                           help="pull a fresh snapshot (standings + histories) before reading")
    p_leaders.add_argument("--json", action="store_true",
                           help="output the machine-readable JSON envelope (agent contract)")
```

Handler (after `_cmd_note_cli`):

```python
def _cmd_leaders_cli(args, conn=None, cfg=None):
    """leaders — top-100 cohort analytics (v0.27). --refresh pulls a fresh snapshot."""
    from .analytics import leaders as leaders_analytics
    from .data import leaders as leaders_data
    from .data.fpl_client import FPLClient
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        if args.refresh:
            try:
                leaders_data.fetch_leader_snapshot(conn, FPLClient())
            except Exception as exc:  # noqa: BLE001 — never break the read path
                return _json_err("leaders", "E_RUNTIME",
                                 f"leader snapshot failed ({exc})",
                                 "retry later; the stored data is still readable")
        payload = leaders_analytics.analyze(conn)
        if args.json:
            _json_ok("leaders", payload)
        else:
            print("== leaders (global top-100) ==")
            print(f"  entries: {len(payload['cohort'])}")
            ct = payload["patterns"]["chip_timing"]
            print(f"  chips played: {len(ct['rows'])} leader-GWs" +
                  ("; first: " + "; ".join(f"{c} @GW{r['gw']} x{r['count']}"
                                           for c, r in ct["first_chip"].items()) if ct["first_chip"] else ""))
            tr = payload["patterns"]["transfers"]
            if tr["mean_per_gw"] is not None:
                print(f"  transfers: mean {tr['mean_per_gw']}/GW, median {tr['median_per_gw']}, "
                      f"hit freq {tr['hit_freq']}, mean hit cost {tr['mean_hit_cost']}")
            else:
                print("  transfers: no snapshots yet")
            for c in payload["cohort"][:10]:
                print(f"  #{c['rank']:<4} {c['player_name']:<18} {c['total']} pts "
                      f"{c['chips_used'] or ''}")
    finally:
        if owns:
            conn.close()
```

Dispatch (in `main()`):

```python
    elif args.command == "leaders":
        _cmd_leaders_cli(args)
```

Agent-safe list: add `"leaders"` (cli.py ~line 230 and the resume list ~line 111).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_cli_leaders.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_leaders.py
git commit -m "feat(cli): leaders command (read + --refresh)"
```

---

### Task 8: Dashboard — /leaders page with ECharts

**Files:**
- Modify: `frontend/package.json` (add `echarts`)
- Create: `frontend/src/lib/components/LeaderChart.svelte` (thin ECharts wrapper)
- Create: `frontend/src/routes/leaders/+page.svelte`, `+page.ts`, `page.svelte.test.ts`
- Modify: `frontend/src/lib/api/client.ts` + `frontend/src/lib/types.ts` (LeadersPayload)

**Interfaces:**
- Consumes: `GET /api/leaders` (Task 6).
- Produces: `/leaders` page — cohort table, chip heatmap, transfer histogram, bank/value lines, momentum table.

- [ ] **Step 1: Install echarts**

```bash
cd frontend && npm install echarts
```

- [ ] **Step 2: Write the failing vitest**

Create `frontend/src/routes/leaders/page.svelte.test.ts` (echarts mocked — jsdom has no canvas):

```ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

vi.mock('echarts/core', () => ({
	use: vi.fn(),
	init: vi.fn(() => ({
		setOption: vi.fn(),
		dispose: vi.fn(),
		resize: vi.fn()
	}))
}));
vi.mock('echarts/charts', () => ({ LineChart: {}, BarChart: {}, HeatmapChart: {} }));
vi.mock('echarts/components', () => ({
	GridComponent: {}, TooltipComponent: {}, LegendComponent: {}, TitleComponent: {}
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

afterEach(() => vi.unstubAllGlobals());

const payload = {
	cohort: [{
		entry_id: 1, player_name: 'Harman Messi', entry_name: 'shadi', rank: 1, total: 227,
		last_gw_points: 91, transfers: 0, hit_cost: 0, bank: 0, value: 1000,
		chips_used: ['3xc'], past_rank: 12310989
	}],
	patterns: {
		chip_timing: { rows: [{ chip: '3xc', gw: 2, count: 23 }], first_chip: { '3xc': { gw: 2, count: 23 } } },
		transfers: { mean_per_gw: 1.1, median_per_gw: 1.0, hit_freq: 0.08, mean_hit_cost: 2.1,
					 histogram: [{ transfers: 0, count: 410 }] },
		bank_value: { bank: [{ gw: 1, mean: 3.2, median: 2.5 }], value: [{ gw: 1, mean: 1005, median: 1004 }] },
		momentum: { top_movers: [{ entry_id: 1, player_name: 'Harman Messi', from_gw: 1, to_gw: 2, rank_gain: 900 }],
					sustained_elite: [] }
	}
};

describe('leaders page', () => {
	it('renders cohort and pattern sections', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => payload }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/Harman Messi/));
		expect(screen.getByText(/Chip timing/)).toBeInTheDocument();
		expect(screen.getByText(/Transfer discipline/)).toBeInTheDocument();
		expect(screen.getByText(/Bank & value/)).toBeInTheDocument();
		expect(screen.getByText(/Rank momentum/)).toBeInTheDocument();
	});

	it('renders the empty state', async () => {
		const empty = { cohort: [], patterns: {
			chip_timing: { rows: [], first_chip: {} },
			transfers: { mean_per_gw: null, median_per_gw: null, hit_freq: null, mean_hit_cost: null, histogram: [] },
			bank_value: { bank: [], value: [] },
			momentum: { top_movers: [], sustained_elite: [] }
		} };
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => empty }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/first snapshot/i));
	});
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test` in `frontend/`
Expected: FAIL — route missing.

- [ ] **Step 4: Implement**

`frontend/src/lib/types.ts` append:

```ts
export interface LeaderCohortRow {
	entry_id: number;
	player_name: string;
	entry_name: string;
	rank: number;
	total: number;
	last_gw_points: number | null;
	transfers: number | null;
	hit_cost: number | null;
	bank: number | null;
	value: number | null;
	chips_used: string[];
	past_rank: number | null;
}

export interface LeadersPayload {
	cohort: LeaderCohortRow[];
	patterns: {
		chip_timing: { rows: { chip: string; gw: number; count: number }[]; first_chip: Record<string, { gw: number; count: number }> };
		transfers: { mean_per_gw: number | null; median_per_gw: number | null; hit_freq: number | null;
					 mean_hit_cost: number | null; histogram: { transfers: number; count: number }[] };
		bank_value: { bank: { gw: number; mean: number; median: number }[]; value: { gw: number; mean: number; median: number }[] };
		momentum: { top_movers: { entry_id: number; player_name?: string; from_gw: number; to_gw: number; rank_gain: number }[];
					sustained_elite: number[] };
	};
}
```

`frontend/src/lib/api/client.ts` — add `fetchLeaders`:

```ts
export async function fetchLeaders(fetchFn: Fetch = fetch): Promise<LeadersPayload> {
	return getJson<LeadersPayload>('/api/leaders', fetchFn);
}
```

(add `LeadersPayload` to the type import.)

Create `frontend/src/lib/components/LeaderChart.svelte`:

```svelte
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import * as echarts from 'echarts/core';
	import { BarChart, HeatmapChart, LineChart } from 'echarts/charts';
	import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
	import { CanvasRenderer } from 'echarts/renderers';

	echarts.use([BarChart, HeatmapChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

	let { option, height = '240px' }: { option: Record<string, unknown>; height?: string } = $props();
	let el: HTMLDivElement | undefined = $state();
	let chart: echarts.ECharts | undefined;

	onMount(() => {
		if (!el) return;
		chart = echarts.init(el, 'dark');
		chart.setOption(option);
	});

	$effect(() => {
		chart?.setOption(option);
	});

	onDestroy(() => {
		chart?.dispose();
	});
</script>

<div bind:this={el} style="width:100%; height:{height}"></div>
```

Create `frontend/src/routes/leaders/+page.svelte` — sections: cohort table, chip heatmap, transfer histogram, bank/value lines, momentum. Build ECharts options from the payload:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchLeaders } from '$lib/api/client';
	import type { LeadersPayload } from '$lib/types';
	import LeaderChart from '$lib/components/LeaderChart.svelte';

	let payload = $state<LeadersPayload | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			payload = await fetchLeaders();
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	const CHIP_LABELS: Record<string, string> = {
		wildcard: 'WC', free_hit: 'FH', bench_boost: 'BB', '3xc': 'TC'
	};

	$derived(chipHeatOption) {
		const rows = payload?.patterns.chip_timing.rows ?? [];
		const chips = [...new Set(rows.map((r) => r.chip))];
		const gws = [...new Set(rows.map((r) => r.gw))].sort((a, b) => a - b);
		return {
			tooltip: { position: 'top' },
			grid: { left: 60, right: 20, top: 30, bottom: 30 },
			xAxis: { type: 'category', data: gws, name: 'GW' },
			yAxis: { type: 'category', data: chips.map((c) => CHIP_LABELS[c] ?? c), name: 'Chip' },
			visualMap: { min: 0, max: Math.max(1, ...rows.map((r) => r.count)), inRange: { color: ['#121821', '#00e6a8'] } },
			series: [{
				type: 'heatmap', data: rows.map((r) => [gws.indexOf(r.gw), chips.indexOf(r.chip), r.count]),
				label: { show: true }
			}]
		};
	}

	$derived(transferOption) {
		const hist = payload?.patterns.transfers.histogram ?? [];
		return {
			tooltip: {},
			grid: { left: 50, right: 20, top: 20, bottom: 30 },
			xAxis: { type: 'category', data: hist.map((h) => String(h.transfers)) },
			yAxis: { type: 'value' },
			series: [{ type: 'bar', data: hist.map((h) => h.count), itemStyle: { color: '#00e6a8' } }]
		};
	}

	$derived(bankOption) {
		const bank = payload?.patterns.bank_value.bank ?? [];
		const value = payload?.patterns.bank_value.value ?? [];
		return {
			tooltip: { trigger: 'axis' },
			legend: { data: ['bank mean', 'bank median'] },
			grid: { left: 50, right: 20, top: 30, bottom: 30 },
			xAxis: { type: 'category', data: bank.map((b) => `GW${b.gw}`) },
			yAxis: { type: 'value', name: 'bank (£m)' },
			series: [
				{ name: 'bank mean', type: 'line', data: bank.map((b) => b.mean), smooth: true },
				{ name: 'bank median', type: 'line', data: bank.map((b) => b.median), smooth: true }
			]
		};
	}
</script>
```

(Pages render sections with `{#if payload}`; momentum = top-movers table; heatmap/histogram/lines via `<LeaderChart option={...} />`. Add `+page.ts` with `export const prerender = false;`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test` in `frontend/`; `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/types.ts \
        frontend/src/lib/api/client.ts frontend/src/lib/components/LeaderChart.svelte \
        frontend/src/routes/leaders/
git commit -m "feat(dashboard): leaders page — cohort table + ECharts patterns"
```

---

### Task 9: Smoke + suite + deploy

- [ ] **Step 1: Live smoke (local DB, real API)**

```bash
.venv/bin/python - <<'PY'
from src.data.db import connect, init_db
from src.data.leaders import fetch_leader_snapshot
from src.data.fpl_client import FPLClient
from src.analytics.leaders import analyze
conn = connect("data/fpl_autopilot.db"); init_db(conn)
n_e, n_s = fetch_leader_snapshot(conn, FPLClient())
print("entries:", n_e, "snapshots:", n_s)
a = analyze(conn)
print("cohort rows:", len(a["cohort"]))
print("chip_timing:", a["patterns"]["chip_timing"]["rows"][:5])
print("transfers:", a["patterns"]["transfers"])
print("bank/value gw1:", a["patterns"]["bank_value"]["bank"][:2])
print("sustained elite:", len(a["patterns"]["momentum"]["sustained_elite"]))
PY
```

Expected: entries ≈ 100, snapshots ≈ 100 (GW1 or GW2 settled rows), chip_timing shows any TC plays, transfers stats finite. Runtime ~2-3 min (102 requests at 1 req/s).

- [ ] **Step 2: Full suite**

Run: `.venv/bin/pytest -q` and `npm test` in `frontend/`
Expected: all green.

- [ ] **Step 3: Commit + push**

```bash
git add -A -- docs  # only docs if the smoke found nothing; otherwise fix first
git push origin main
```

- [ ] **Step 4: Verify on jumbo after deploy**

```bash
ssh jumbo 'docker compose --project-directory /opt/fpl-autopilot run --rm -T app leaders --json' | python3 -m json.tool | head -40
```

Expected: cohort + patterns present. The next hourly cycle after a GW settles will snapshot automatically.

- [ ] **Step 5: Report**

Summarize the insight tables for the user: chip timing clusters, transfer discipline stats, bank/value trends, momentum movers, sustained-elite count — and note that products (e.g., chip-recommender priors) come next after they study the patterns (B4 follow-up).
