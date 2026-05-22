# Captain Ranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Decision-Layer engine — a captain ranker that, given the squad and xP, returns the top-5 captain options (with reasoning) + vice, in the `/api/captain` shape. Suggest-only (no execution/persistence).

**Architecture:** A pure `rank_captains(candidates)` does the sort + reasoning; a thin `get_captain_picks(conn)` reads Analytics output (`xp`) + squad (`my_team`) + display data (`players`/`fixtures`/`teams`/`fdr`) and feeds the pure function. The Decision Layer only reads tables — it never re-derives FDR/xP and never touches the network (CLAUDE.md B2). No persistence (Phase 1).

**Tech Stack:** Python 3.12, stdlib `sqlite3` (`conn.row_factory = Row`), `json`, pytest with the in-memory `db` fixture from `tests/conftest.py`.

---

## File Structure

- `src/decisions/captain.py` (**create**) — `rank_captains` (pure) + `get_captain_picks` (reader) + small private helpers (`_next_gw`, `_squad_element_ids`, `_build_candidate`, `_fixture_and_fdr`). One responsibility: rank the squad for captaincy.
- `tests/test_captain.py` (**create**) — 4 deterministic pure tests + 1 in-memory-DB integration test.

**Do NOT modify** `pyproject.toml`, `src/decisions/__init__.py`, or `docs/decision-engine.md` — already on `main`; the transfer-engine and API agents depend on them being stable (spec §3).

**Test command (worktree has no local `.venv`; use the main checkout's venv via `python -m` so imports resolve to the worktree `src`):**
`/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest`
Run from the worktree root: `/Users/falah/Work/fpl-autopilot/.claude/worktrees/feat+captain-ranker`.

---

## Data contract (verified against schema.sql + repository.py)

- `my_team(gw PK, picks_json TEXT, ...)` — latest snapshot = `ORDER BY gw DESC LIMIT 1`. `picks_json` is a JSON array of `{"element", "position", "multiplier", "is_captain", "is_vice_captain"}`; the 15 squad ids are the `element` values.
- `players(id, web_name, team_id, position, status, ...)`.
- `teams(id, short_name, ...)`.
- `xp(player_id, gw, model_version, xp, xminutes, ...)` — captain reads `xp`, `xminutes` for `model_version='v1'`.
- `fdr(team_id, gw, fdr_attack, fdr_defense)`.
- `fixtures(id, gw, home_team_id, away_team_id, ...)`.
- `gameweeks(id, ..., finished)` — next GW = `SELECT MIN(id) FROM gameweeks WHERE finished=0` (same as fdr/xp).

**Candidate shape (spec §4):** `{player_id, web_name, position, xp, xminutes, fdr_attack, fixture}` where `fixture` is `"<team_short> v <opp_short> (H|A)"`.
**Pick (output) shape:** `{player_id, web_name, xp, fixture, reason}`.
**No-xp-row players** (blank GW / unmatched): `xp=0.0, xminutes=0.0, fdr_attack=5, fixture="—"` → naturally rank last.

---

### Task 1: `rank_captains` — pure ranking + reasoning

**Files:**
- Create: `src/decisions/captain.py`
- Test: `tests/test_captain.py`

- [ ] **Step 1: Write the failing pure tests**

```python
# tests/test_captain.py
from src.decisions import captain


def _cand(pid, web_name, xp, xminutes=80.0, fdr_attack=3, fixture="ABC v XYZ (H)"):
    return {"player_id": pid, "web_name": web_name, "position": "MID",
            "xp": xp, "xminutes": xminutes, "fdr_attack": fdr_attack, "fixture": fixture}


def test_rank_captains_orders_by_xp():
    picks = captain.rank_captains([
        _cand(2, "Mid", 5.0), _cand(1, "Top", 7.2), _cand(3, "Low", 3.1),
    ])
    assert [p["player_id"] for p in picks] == [1, 2, 3]
    assert [p["xp"] for p in picks] == [7.2, 5.0, 3.1]


def test_rank_captains_tiebreak_minutes_then_fdr():
    # equal xp -> higher xminutes wins
    picks = captain.rank_captains([
        _cand(1, "LowMin", 6.0, xminutes=60.0),
        _cand(2, "HighMin", 6.0, xminutes=88.0),
    ])
    assert [p["player_id"] for p in picks] == [2, 1]
    # equal xp AND equal xminutes -> lower fdr_attack (easier fixture) wins
    picks = captain.rank_captains([
        _cand(3, "HardFix", 6.0, xminutes=88.0, fdr_attack=5),
        _cand(4, "EasyFix", 6.0, xminutes=88.0, fdr_attack=2),
    ])
    assert [p["player_id"] for p in picks] == [4, 3]


def test_rank_captains_reason_includes_gap():
    picks = captain.rank_captains([
        _cand(1, "Haaland", 7.2, fixture="MCI v BOU (H)"),
        _cand(2, "Salah", 6.1),
    ])
    assert "Highest xP (7.2)" in picks[0]["reason"]
    assert "MCI v BOU (H)" in picks[0]["reason"]
    assert "Salah" in picks[0]["reason"]
    assert "gap 1.1" in picks[0]["reason"]
    # ranks 2-5 use the short form
    assert picks[1]["reason"] == "xP 6.1 ABC v XYZ (H)."


def test_rank_captains_vice_is_second():
    # the #2 pick is the vice; assert ranking puts the 2nd-highest xp there
    picks = captain.rank_captains([
        _cand(1, "Top", 7.2), _cand(2, "Second", 6.1), _cand(3, "Third", 5.0),
    ])
    assert picks[1]["player_id"] == 2


def test_rank_captains_caps_at_five():
    picks = captain.rank_captains([_cand(i, f"P{i}", float(20 - i)) for i in range(1, 9)])
    assert len(picks) == 5
    assert [p["player_id"] for p in picks] == [1, 2, 3, 4, 5]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_captain.py -q`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (`captain.rank_captains` does not exist).

- [ ] **Step 3: Write minimal implementation of `rank_captains`**

```python
# src/decisions/captain.py
"""Captain ranker (Decision Layer). Reads Analytics output (xp) + squad/fixtures and
ranks the 15-man squad for captaincy. Suggest-only — no execution/persistence (CLAUDE.md B2)."""
import json

MODEL_VERSION = "v1"


def rank_captains(candidates):
    """Pure: rank captain candidates, build reasoning, return the top-5 picks.

    Each candidate: {player_id, web_name, position, xp, xminutes, fdr_attack, fixture}.
    Order: xp desc, tiebreak xminutes desc, then fdr_attack asc.
    Returns up to 5 picks: {player_id, web_name, xp, fixture, reason}."""
    ranked = sorted(candidates, key=lambda c: (-c["xp"], -c["xminutes"], c["fdr_attack"]))[:5]
    picks = []
    for i, c in enumerate(ranked):
        if i == 0 and len(ranked) > 1:
            s = ranked[1]
            reason = (f"Highest xP ({c['xp']}) {c['fixture']}. "
                      f"Next best {s['web_name']} {s['xp']} — gap {round(c['xp'] - s['xp'], 1)}.")
        elif i == 0:
            reason = f"Highest xP ({c['xp']}) {c['fixture']}."
        else:
            reason = f"xP {c['xp']} {c['fixture']}."
        picks.append({"player_id": c["player_id"], "web_name": c["web_name"],
                      "xp": c["xp"], "fixture": c["fixture"], "reason": reason})
    return picks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_captain.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_captain.py src/decisions/captain.py
git commit -m "feat(captain): pure rank_captains (xp desc, xminutes/fdr tiebreak, reasoning)"
```

---

### Task 2: `get_captain_picks` — DB reader returning the `/api/captain` shape

**Files:**
- Modify: `src/decisions/captain.py`
- Test: `tests/test_captain.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_captain.py`:

```python
import json


def _seed_squad(db):
    # 4 teams (need short_name for the fixture display string)
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)", [
        (1, "Man City", "MCI"), (2, "Bournemouth", "BOU"),
        (3, "Liverpool", "LIV"), (4, "Arsenal", "ARS"),
    ])
    # gw9 finished, gw10 upcoming -> next_gw = 10
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (9,'GW9',1),(10,'GW10',0)")
    # gw10 fixtures: MCI(1) home v BOU(2); LIV(3) home v ARS(4)
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) "
               "VALUES (1,10,1,2,0),(2,10,3,4,0)")
    db.executemany("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
                   "VALUES (?,10,?,?,'t')", [(1, 2, 3), (2, 4, 3), (3, 3, 2), (4, 3, 4)])
    # 15 players, ids 101..115, descending xp so ordering is unambiguous
    teams_cycle = [1, 3, 2, 4]
    xps = [7.2, 6.1, 5.5, 5.0, 4.5, 4.0, 3.8, 3.5, 3.2, 3.0, 2.8, 2.5, 2.2, 2.0, 1.5]
    pids = list(range(101, 116))
    for idx, pid in enumerate(pids):
        web = "Haaland" if pid == 101 else f"P{pid}"
        team = 1 if pid == 101 else teams_cycle[idx % 4]
        db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
                   "VALUES (?,?,?,?,?, 'a')", (pid, web, web, team, "FWD"))
        db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
                   "xassists, xcs, computed_at) VALUES (?,10,'v1',?,85.0,0,0,0,'t')",
                   (pid, xps[idx]))
    picks_json = json.dumps([
        {"element": pid, "position": i + 1, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False} for i, pid in enumerate(pids)])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, snapshot_at) "
               "VALUES (10, ?, 0, 0, 't')", (picks_json,))
    db.commit()


def test_get_captain_picks_integration(db):
    _seed_squad(db)
    result = captain.get_captain_picks(db)

    # shape matches docs/api-contract.md /api/captain
    assert set(result.keys()) == {"picks", "vice_player_id"}
    assert len(result["picks"]) == 5
    for p in result["picks"]:
        assert set(p.keys()) == {"player_id", "web_name", "xp", "fixture", "reason"}

    # descending xp
    xs = [p["xp"] for p in result["picks"]]
    assert xs == sorted(xs, reverse=True)

    # top pick is the premium attacker, home fixture rendered correctly
    top = result["picks"][0]
    assert top["player_id"] == 101 and top["web_name"] == "Haaland"
    assert top["fixture"] == "MCI v BOU (H)"
    assert "gap 1.1" in top["reason"]

    # vice = #2 pick
    assert result["vice_player_id"] == result["picks"][1]["player_id"] == 102


def test_get_captain_picks_no_upcoming_gw_returns_empty(db):
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1)")
    db.commit()
    assert captain.get_captain_picks(db) == {"picks": [], "vice_player_id": None}
```

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_captain.py::test_get_captain_picks_integration -q`
Expected: FAIL — `AttributeError: module 'src.decisions.captain' has no attribute 'get_captain_picks'`.

- [ ] **Step 3: Implement the reader + helpers**

Append to `src/decisions/captain.py`:

```python
def _next_gw(conn):
    row = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row else None


def _squad_element_ids(conn):
    row = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    return [p["element"] for p in json.loads(row["picks_json"])] if row else []


def _fixture_and_fdr(conn, team_id, team_short, gw):
    fx = conn.execute(
        """SELECT f.home_team_id, th.short_name AS home_short, ta.short_name AS away_short
           FROM fixtures f
           JOIN teams th ON th.id = f.home_team_id
           JOIN teams ta ON ta.id = f.away_team_id
           WHERE f.gw = ? AND (f.home_team_id = ? OR f.away_team_id = ?)
           LIMIT 1""", (gw, team_id, team_id)).fetchone()
    fdr_row = conn.execute(
        "SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (team_id, gw)).fetchone()
    fdr_attack = fdr_row["fdr_attack"] if fdr_row else 5
    if fx is None:
        return "—", fdr_attack
    if fx["home_team_id"] == team_id:
        return f"{team_short} v {fx['away_short']} (H)", fdr_attack
    return f"{team_short} v {fx['home_short']} (A)", fdr_attack


def _build_candidate(conn, player_id, gw):
    pl = conn.execute(
        """SELECT p.id AS player_id, p.web_name, p.position, p.team_id,
                  t.short_name AS team_short
           FROM players p JOIN teams t ON t.id = p.team_id
           WHERE p.id = ?""", (player_id,)).fetchone()
    if pl is None:
        return None
    xp_row = conn.execute(
        "SELECT xp, xminutes FROM xp WHERE player_id=? AND gw=? AND model_version=?",
        (player_id, gw, MODEL_VERSION)).fetchone()
    if xp_row is None:
        return {"player_id": pl["player_id"], "web_name": pl["web_name"],
                "position": pl["position"], "xp": 0.0, "xminutes": 0.0,
                "fdr_attack": 5, "fixture": "—"}
    fixture, fdr_attack = _fixture_and_fdr(conn, pl["team_id"], pl["team_short"], gw)
    return {"player_id": pl["player_id"], "web_name": pl["web_name"],
            "position": pl["position"], "xp": xp_row["xp"], "xminutes": xp_row["xminutes"],
            "fdr_attack": fdr_attack, "fixture": fixture}


def get_captain_picks(conn):
    """Reader: returns the /api/captain payload {picks, vice_player_id} for the next GW."""
    gw = _next_gw(conn)
    if gw is None:
        return {"picks": [], "vice_player_id": None}
    candidates = [c for c in (_build_candidate(conn, pid, gw)
                              for pid in _squad_element_ids(conn)) if c is not None]
    picks = rank_captains(candidates)
    vice = picks[1]["player_id"] if len(picks) > 1 else None
    return {"picks": picks, "vice_player_id": vice}
```

- [ ] **Step 4: Run the captain tests to verify they pass**

Run: `/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_captain.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the FULL suite (no regressions)**

Run: `/Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest -q`
Expected: PASS (71 passed = 64 baseline + 7 new).

- [ ] **Step 6: Commit**

```bash
git add tests/test_captain.py src/decisions/captain.py
git commit -m "feat(captain): get_captain_picks reader returning /api/captain payload"
```

---

## Definition of Done (spec §8)

1. `pytest` green incl. captain tests (full suite, no regressions).
2. `get_captain_picks(conn)` returns a valid `/api/captain` payload — keys `{picks, vice_player_id}`, ≤5 picks each `{player_id, web_name, xp, fixture, reason}`, descending xp, vice = #2.
3. `decision-engine.md` v1 note + changelog already on `main` (B4) — **not modified here**.
4. Suggest-only: no execution, no persistence (no writes to `activity_log` or any table).
5. Optional live check: `fpl-autopilot refresh`, then `fdr.compute_and_store(conn)` + `xp.compute_and_store(conn)`, then eyeball that the squad's premium attackers top the picks.
6. Open a PR against `main` from `feat/captain-ranker`.
