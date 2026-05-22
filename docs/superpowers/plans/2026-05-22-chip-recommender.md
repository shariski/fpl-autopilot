# Chip Recommender + DGW/BGW Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `recommend_chip(conn)` returns a single `/api/chips` recommendation (or null) by evaluating the four chip triggers over DGW/BGW-aware fixture data.

**Architecture:** `src/analytics/dgw.py` (fixture-count + FDR helpers) + `src/decisions/chips.py` (DGW-aware per-player xP via reusing `analytics.xp.compute_player_xp`, four pure trigger functions, and `recommend_chip` with priority + chips_used suppression). Wires `/api/chips`. Flag-only; chips never auto-execute (B3/B8).

**Tech Stack:** Python 3.11+, raw `sqlite3`, FastAPI, `pytest`. `.venv` exists; `src/` is the package.

**Spec:** `docs/superpowers/specs/2026-05-22-chip-recommender-design.md`

---

## v1 DGW-FDR note (read before coding)
The `fdr` table PK is `(team_id, gw)` — it stores ONE FDR per team per GW, so for a double gameweek both fixtures share that stored FDR. v1 DGW-xP = `fixture_count × single-fixture xP` using the stored FDR (a documented approximation; proper per-fixture DGW FDR is part of the deferred DGW work). This keeps the slice simple and is fine for flag-only recommendations.

## File Structure

| File | Responsibility |
|---|---|
| `docs/decision-engine.md` | chip recommender v1 note + changelog (B4). |
| `src/analytics/dgw.py` | `team_fixture_count`, `team_gw_fdr`. |
| `src/decisions/chips.py` | `_player_gw_xp`, 4 trigger fns, `recommend_chip`. |
| `src/interface/api.py` | wire `/api/chips` to `recommend_chip`. |
| `tests/test_chips.py` | dgw + triggers + recommend + endpoint tests. |

---

## Task 1: decision-engine.md v1 note (B4 — do first)

**Files:** Modify `docs/decision-engine.md`

- [ ] **Step 1: Add the v1 note.** Find the line `## Confidence score`. Immediately BEFORE it, insert:

```markdown
### v1 implementation (2026-05-22)

- **Wildcard v1** uses only the fixture-swing criterion (≥3 squad players whose `fdr_attack` worsens by ≥2 over the next 3 GW). The "≥4 sell candidates" criterion is deferred until the transfer engine is integrated; "squad value below team average" is dropped (cross-manager data unavailable).
- **DGW-aware xP** for Bench Boost / Triple Captain = `fixture_count × single-fixture xP` (reusing `analytics.xp.compute_player_xp` with the team's stored FDR for that GW). The `fdr` table holds one value per `(team, gw)`, so both DGW fixtures share it (approximation).
- **Single recommendation priority:** Triple Captain → Bench Boost → Free Hit → Wildcard. Already-used chips (from `my_team.chips_used_json`, best-effort) are skipped.
- Flag-only; chips never auto-execute (B3/B8).

```

- [ ] **Step 2: Add a changelog row.** After the `v0.5` row, add:

```
| v0.6 | 2026-05-22 | Chip recommender v1: DGW/BGW detection; Wildcard fixture-swing only (others deferred/dropped); DGW-xP via per-fixture sum; priority TC>BB>FH>WC. |
```

- [ ] **Step 3: Verify**

```bash
grep -cF "Wildcard v1" docs/decision-engine.md
grep -cF "v0.6" docs/decision-engine.md
```
Expected: each prints `1`.

- [ ] **Step 4: Commit**

```bash
git add docs/decision-engine.md
git commit -m "docs(decision-engine): chip recommender v1 (wildcard fixture-swing, DGW-xP, priority) (B4)"
```

---

## Task 2: DGW/BGW detection helpers

**Files:** Create `src/analytics/dgw.py`; Test `tests/test_chips.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_chips.py`

```python
from src.data.db import connect, init_db
from src.analytics import dgw


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_team_fixture_count_single_double_blank():
    conn = _db()
    # team 1: a single (gw5), a double (gw6: two fixtures), a blank (gw7: none)
    conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES "
                 "(1,5,1,2,0),(2,6,1,3,0),(3,6,4,1,0)")
    conn.commit()
    assert dgw.team_fixture_count(conn, 1, 5) == 1
    assert dgw.team_fixture_count(conn, 1, 6) == 2  # double
    assert dgw.team_fixture_count(conn, 1, 7) == 0  # blank


def test_team_gw_fdr():
    conn = _db()
    conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (1,5,2,3,'t')")
    conn.commit()
    fd = dgw.team_gw_fdr(conn, 1, 5)
    assert fd["fdr_attack"] == 2 and fd["fdr_defense"] == 3
    assert dgw.team_gw_fdr(conn, 1, 9) is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_chips.py -v`
Expected: `ModuleNotFoundError: No module named 'src.analytics.dgw'`.

- [ ] **Step 3: Write `src/analytics/dgw.py`**

```python
def team_fixture_count(conn, team_id, gw):
    """Number of fixtures a team has in a GW: 0 = blank, 1 = single, 2 = double gameweek."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM fixtures WHERE gw=? AND (home_team_id=? OR away_team_id=?)",
        (gw, team_id, team_id)).fetchone()
    return r["c"]


def team_gw_fdr(conn, team_id, gw):
    """The team's stored FDR row for a GW, or None."""
    return conn.execute(
        "SELECT fdr_attack, fdr_defense FROM fdr WHERE team_id=? AND gw=?", (team_id, gw)).fetchone()
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_chips.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analytics/dgw.py tests/test_chips.py
git commit -m "feat: DGW/BGW fixture-count + FDR detection helpers"
```

---

## Task 3: Chip triggers + recommend_chip

**Files:** Create `src/decisions/chips.py`; Test `tests/test_chips.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_chips.py`

```python
from src.decisions import chips


def _seed_squad(conn, picks, chips_used=None):
    import json
    pj = json.dumps([{"element": e, "position": pos, "multiplier": 1,
                      "is_captain": False, "is_vice_captain": False} for e, pos in picks])
    cj = json.dumps(chips_used or [])
    conn.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, chips_used_json, snapshot_at) "
                 "VALUES (5, ?, 0.0, 100.0, 1, ?, 't')", (pj, cj))
    conn.commit()


def _player(conn, pid, team, position="MID", price=6.0, status="a", xg90=0.5, xa90=0.2, minutes=2700, games=30):
    conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status) VALUES (?,?,?,?,?,?)",
                 (pid, f"P{pid}", team, position, price, status))
    conn.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, minutes, games, "
                 "xg, xa, npg, npxg, xg_per_90, xa_per_90, updated_at) "
                 "VALUES (?,?,?,?,?,0,0,0,0,?,?,'t')",
                 (str(pid), pid, "2025", minutes, games, xg90, xa90))


def _gw6_double_for_all(conn, teams):
    # give every listed team a DOUBLE in gw6 (two fixtures) + an FDR row
    fid = 100
    for t in teams:
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,6,?,?,0)",
                     (fid, t, 99)); fid += 1
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,6,?,?,0)",
                     (fid, 99, t)); fid += 1
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,6,1,1,'t')", (t,))
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    conn.commit()


def test_free_hit_triggers_on_blank():
    conn = _db()
    # 15 players across teams 1..15; gw6 only team 1 has a fixture -> coverage 1 (<8)
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (1,6,1,99,0)")
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.free_hit_trigger(conn, squad, [6]) is not None


def test_bench_boost_triggers_on_dgw():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]  # pick positions 1..15; bench = 12..15
    for i in range(1, 16):
        _player(conn, i, i, position="MID", xg90=0.6, xa90=0.3)
    _gw6_double_for_all(conn, list(range(1, 16)))
    _seed_squad(conn, picks)
    _, squad, _ = chips._squad(conn)
    assert chips.bench_boost_trigger(conn, squad, [6]) is not None


def test_triple_captain_triggers_for_premium_dgw():
    conn = _db()
    picks = [(1, 1)] + [(i, i) for i in range(2, 16)]
    _player(conn, 1, 1, position="FWD", price=14.0, xg90=1.0, xa90=0.3)  # premium, high xG
    for i in range(2, 16):
        _player(conn, i, i)
    _gw6_double_for_all(conn, [1])  # team 1 doubles in gw6, FDR 1
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (6,'GW6',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.triple_captain_trigger(conn, squad, [6]) is not None


def test_wildcard_fixture_swing():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    # next_gw=5; for teams 1,2,3 fdr_attack jumps 1 -> 4 by gw8 (swing >=2)
    for t in (1, 2, 3):
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,5,1,3,'t')", (t,))
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,8,4,3,'t')", (t,))
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5,'GW5',0)")
    _seed_squad(conn, picks)
    conn.commit()
    _, squad, _ = chips._squad(conn)
    assert chips.wildcard_trigger(conn, squad, 5) is not None


def test_recommend_chip_priority_and_chips_used():
    conn = _db()
    # set up a Bench-Boost-triggering DGW for all; recommend should pick bench_boost (no TC premium)
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i, xg90=0.6, xa90=0.3)
    _gw6_double_for_all(conn, list(range(1, 16)))
    _seed_squad(conn, picks)
    rec = chips.recommend_chip(conn)["recommendation"]
    assert rec is not None and rec["chip"] == "bench_boost"
    # now mark bench_boost used -> should fall through (free_hit/wildcard won't trigger here) -> None
    conn.execute("UPDATE my_team SET chips_used_json=? WHERE gw=5", ('["bboost"]',))
    conn.commit()
    assert chips.recommend_chip(conn)["recommendation"] is None


def test_recommend_chip_none_when_nothing_triggers():
    conn = _db()
    picks = [(i, i) for i in range(1, 16)]
    for i in range(1, 16):
        _player(conn, i, i)
    # all single fixtures gw5, full coverage, no swing
    fid = 200
    for i in range(1, 16):
        conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,5,?,99,0)", (fid, i)); fid += 1
        conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,5,3,3,'t')", (i,))
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5,'GW5',0)")
    _seed_squad(conn, picks)
    conn.commit()
    assert chips.recommend_chip(conn)["recommendation"] is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_chips.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.decisions.chips'`.

- [ ] **Step 3: Write `src/decisions/chips.py`**

```python
import json
from src.analytics import dgw
from src.analytics.xp import compute_player_xp

PREMIUM_PRICE = 9.5
BENCH_BOOST_THRESHOLD = 4.0
TRIPLE_CAPTAIN_XP = 12.0
FREE_HIT_COVERAGE = 8
WILDCARD_SWING_PLAYERS = 3
WILDCARD_SWING_DELTA = 2

# FPL chip codes -> our names (for chips_used suppression).
FPL_CHIP = {"wildcard": "wildcard", "freehit": "free_hit", "bboost": "bench_boost", "3xc": "triple_captain"}


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def _squad(conn):
    snap = conn.execute(
        "SELECT picks_json, chips_used_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if snap is None:
        return [], [], []
    picks = json.loads(snap["picks_json"])
    chips_used_raw = json.loads(snap["chips_used_json"]) if snap["chips_used_json"] else []
    chips_used = {FPL_CHIP.get(c, c) for c in chips_used_raw}
    rows = []
    for pk in picks:
        p = conn.execute(
            "SELECT p.id, p.web_name, p.position, p.status, p.team_id, p.price, "
            "u.xg_per_90, u.xa_per_90, u.minutes, u.games "
            "FROM players p LEFT JOIN understat_players u ON u.fpl_player_id = p.id WHERE p.id=?",
            (pk["element"],)).fetchone()
        if p is None:
            continue
        d = dict(p)
        d["pick_position"] = pk["position"]
        rows.append(d)
    return picks, rows, chips_used


def _player_gw_xp(conn, r, gw):
    if r["xg_per_90"] is None:  # unmatched player, no Understat rates
        return 0.0
    n = dgw.team_fixture_count(conn, r["team_id"], gw)
    if n == 0:
        return 0.0
    fd = dgw.team_gw_fdr(conn, r["team_id"], gw)
    if fd is None:
        return 0.0
    one = compute_player_xp(r["position"], r["status"], r["xg_per_90"], r["xa_per_90"],
                            r["minutes"], r["games"], fd["fdr_attack"], fd["fdr_defense"])["xp"]
    return round(n * one, 2)


def free_hit_trigger(conn, squad, gws):
    for gw in gws:
        covered = sum(1 for r in squad if dgw.team_fixture_count(conn, r["team_id"], gw) >= 1)
        if covered < FREE_HIT_COVERAGE:
            return f"Blank GW{gw}: only {covered} of 15 squad players have a fixture."
    return None


def bench_boost_trigger(conn, squad, gws):
    for gw in gws:
        if all(dgw.team_fixture_count(conn, r["team_id"], gw) >= 1 for r in squad):
            bench = [r for r in squad if r["pick_position"] >= 12]
            bench_xp = round(sum(_player_gw_xp(conn, r, gw) for r in bench), 1)
            if bench_xp > BENCH_BOOST_THRESHOLD:
                return f"GW{gw}: all 15 have fixtures; bench xP {bench_xp} (> {BENCH_BOOST_THRESHOLD})."
    return None


def triple_captain_trigger(conn, squad, gws):
    for gw in gws:
        for r in squad:
            if r["price"] is None or r["price"] < PREMIUM_PRICE:
                continue
            if dgw.team_fixture_count(conn, r["team_id"], gw) != 2:
                continue
            fd = dgw.team_gw_fdr(conn, r["team_id"], gw)
            if fd is None or fd["fdr_attack"] > 2:
                continue
            x = _player_gw_xp(conn, r, gw)
            if x >= TRIPLE_CAPTAIN_XP:
                return f"GW{gw} DGW: {r['web_name']} DGW-xP {x} (>= {TRIPLE_CAPTAIN_XP}), FDR {fd['fdr_attack']}."
    return None


def wildcard_trigger(conn, squad, next_gw):
    worsening = 0
    for r in squad:
        a = conn.execute("SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (r["team_id"], next_gw)).fetchone()
        b = conn.execute("SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (r["team_id"], next_gw + 3)).fetchone()
        if a and b and (b["fdr_attack"] - a["fdr_attack"]) >= WILDCARD_SWING_DELTA:
            worsening += 1
    if worsening >= WILDCARD_SWING_PLAYERS:
        return f"{worsening} squad players face FDR worsening by {WILDCARD_SWING_DELTA}+ over the next 3 GW."
    return None


def recommend_chip(conn, horizon=6):
    next_gw = _next_gw(conn)
    _, squad, chips_used = _squad(conn)
    if next_gw is None or not squad:
        return {"recommendation": None}
    gws = list(range(next_gw, next_gw + horizon))
    candidates = [
        ("triple_captain", triple_captain_trigger(conn, squad, gws)),
        ("bench_boost", bench_boost_trigger(conn, squad, gws)),
        ("free_hit", free_hit_trigger(conn, squad, gws)),
        ("wildcard", wildcard_trigger(conn, squad, next_gw)),
    ]
    for chip, reason in candidates:
        if reason and chip not in chips_used:
            return {"recommendation": {"chip": chip, "reason": reason}}
    return {"recommendation": None}
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_chips.py -v`
Expected: 9 passed (2 dgw + 7 chips).

- [ ] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (74 + 9 = 83).

- [ ] **Step 6: Commit**

```bash
git add src/decisions/chips.py tests/test_chips.py
git commit -m "feat: chip recommender (DGW-aware triggers + priority + chips_used)"
```

---

## Task 4: Wire /api/chips

**Files:** Modify `src/interface/api.py`; Test `tests/test_api.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/test_api.py`

```python
def test_chips_endpoint_wired(client):
    r = client.get("/api/chips")
    assert r.status_code == 200
    body = r.json()
    assert "recommendation" in body  # real recommender output (likely null on the seeded data)
```

- [ ] **Step 2: Run to verify it still passes against the stub, then change the endpoint.** First confirm the suite is green, then replace the chips stub in `src/interface/api.py`. Change:

```python
@app.get("/api/chips")
def chips(conn=Depends(get_db)):
    return {"recommendation": None}  # until the chip recommender slice
```
to:
```python
from src.decisions import chips as chips_engine


@app.get("/api/chips")
def chips(conn=Depends(get_db)):
    return chips_engine.recommend_chip(conn)
```
(Put the `from src.decisions import chips as chips_engine` import at the top of `api.py` with the other imports, not inside the function.)

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass, incl. `test_chips_endpoint_wired` and the existing `test_stub_endpoints` (the chips assertion there checks `{"recommendation": None}`, which still holds on the seeded data since nothing triggers — verify it still passes; if the seeded data happens to trigger a chip, update that one assertion in `test_stub_endpoints` to `assert "recommendation" in client.get("/api/chips").json()`).

- [ ] **Step 4: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (84).

- [ ] **Step 5: Commit**

```bash
git add src/interface/api.py tests/test_api.py
git commit -m "feat: wire /api/chips to the chip recommender"
```

---

## Task 5: Live check (definition of done)

- [ ] **Step 1: Run the recommender against the live DB**

```bash
.venv/bin/python -c "
from src.data.db import connect, init_db
from src.config import db_path
from src.analytics import fdr, xp
from src.decisions import chips
c = connect(db_path()); init_db(c)
fdr.compute_and_store(c); xp.compute_and_store(c)
print('chip recommendation:', chips.recommend_chip(c))
"
```
Expected: `{'recommendation': None}` at season end (no DGW/BGW upcoming) — correct per spec. Correctness is proven by the deterministic tests.

- [ ] **Step 2: Mark complete**

```bash
git commit --allow-empty -m "chore: chip recommender slice complete"
```

---

## Self-Review notes (author)

- **Spec coverage:** decision-engine v1 note + changelog (T1, B4); dgw detection (T2); the four triggers + DGW-xP + recommend priority + chips_used (T3); /api/chips wiring (T4); live check (T5). Wildcard sell-candidate + value-vs-average deferred/dropped per spec §4.
- **Placeholder scan:** none — all triggers, thresholds, and tests are concrete.
- **Type/name consistency:** `dgw.team_fixture_count(conn, team_id, gw)` + `dgw.team_gw_fdr(conn, team_id, gw)` used identically in `chips.py`; `recommend_chip` returns `{"recommendation": {"chip", "reason"} | None}` matching `api-contract.md` `/api/chips`; chip names `triple_captain/bench_boost/free_hit/wildcard`; reuses `compute_player_xp` with its real signature. `_squad` returns `(picks, rows, chips_used)`.
```
