# AI Squad Builder + Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI-picked starting squad (15 players) from a deterministic candidate pool, gated by a legal-squad validator with deterministic fallback, surfaced on `/squad-builder`; plus `apply-squad` CLI that submits the transfers with typed confirm (dry-run default).

**Architecture:** Part A (builder): `src/decisions/squad_builder.py` (candidate pool) → `src/ai/squad/` (digest, prompt, runner) → `src/decisions/squad_validator.py` (law + fallback) → `GET /api/squad/builder` → `/squad-builder` page. Part B (apply): `src/execution/squad.py` (pair builder + sequential `executor.apply_transfers` calls) → `fpl-autopilot apply-squad` CLI.

**Tech Stack:** Python 3.11+, FastAPI, SvelteKit (vitest), pytest. Venv: `.venv/bin/`; frontend: `cd frontend && npm test` / `npm run build`.

## Global Constraints

- **B4:** `docs/decision-engine.md` gains the "Squad builder (S-B)" section in Task 9 **before** the feature is considered done — no threshold changes, decisions stay inspectable (logged with inputs).
- **B7/R3:** no credentials in prompts; `--live` is user-driven only; tests never touch the network (StubProvider + monkeypatched executor).
- **B13:** spec `docs/superpowers/specs/2026-08-14-squad-builder-design.md` is the source of truth.
- **Commits:** conventional style, explicit paths, **never `git add -A`**.
- **Baseline:** backend `658 passed`; frontend `70 passed` (vitest), build green.
- **Branch:** `feat/squad-builder`; merge to main when done.
- **Deviation from spec (implementation detail):** the apply path calls `executor.apply_transfers` directly per out/in pair instead of threading a `rebuild` param through `run_transfer` — same API behavior, no coupling to the suggestion engine (documented in `src/execution/squad.py` docstring).

---

### Task 1: Candidate pool (deterministic)

**Files:**
- Create: `src/decisions/squad_builder.py`
- Test: `tests/test_squad_builder.py`

**Interfaces:**
- Produces: `build_candidate_pool(conn, next_gw=None) -> list[dict]` — sorted dicts:
  `{player_id, web_name, team_short, position, price, status, xp_next, xp_6gw, value}` where
  `value = xp_6gw / price` (None-safe). Filter: status in (a, d); xp_6gw not None; price >= 4.0.
  Per position: top 15 by xp_6gw ∪ top 10 by value (union, dedup, sorted by xp_6gw desc).
  `next_gw` defaults to `MIN(id) WHERE finished=0`; returns `[]` when no next GW.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_squad_builder.py`:

```python
import json
import pathlib

from src.decisions.squad_builder import build_candidate_pool
from src.data.db import connect, init_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _seed():
    from src.data import repository, name_resolver
    from src.data.models import BootstrapStatic, Fixture, UnderstatPlayersResponse
    from src.analytics import fdr, xp

    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(json.loads((FIX / "bootstrap-static.json").read_text()))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
    conn.execute("UPDATE gameweeks SET finished=0 WHERE id=38")
    repository.upsert_fixtures(conn, [Fixture.model_validate(f) for f in json.loads((FIX / "fixtures.json").read_text())])
    us = UnderstatPlayersResponse.model_validate(json.loads((FIX / "understat-players.json").read_text())).players
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(conn, us, res, "2025")
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    conn.commit()
    return conn


def test_pool_shape_and_spread():
    conn = _seed()
    pool = build_candidate_pool(conn)
    assert 20 <= len(pool) <= 200
    by_pos = {}
    for p in pool:
        assert set(p.keys()) == {"player_id", "web_name", "team_short", "position",
                                 "price", "status", "xp_next", "xp_6gw", "value"}
        by_pos.setdefault(p["position"], 0)
        by_pos[p["position"]] += 1
    for pos in ("GKP", "DEF", "MID", "FWD"):
        assert by_pos.get(pos, 0) >= 8, f"position {pos} underrepresented"
    # value tier present: some cheap-ish (< 6.0) players survive the top-xp filter
    assert any(p["price"] < 6.0 for p in pool)
    # sorted by xp_6gw desc
    xps = [p["xp_6gw"] for p in pool]
    assert xps == sorted(xps, reverse=True)
    conn.close()


def test_pool_excludes_injured_and_no_xp():
    conn = _seed()
    conn.execute("UPDATE players SET status='i' WHERE position='GKP'")
    conn.commit()
    pool = build_candidate_pool(conn)
    assert all(p["status"] in ("a", "d") for p in pool)
    conn.close()


def test_pool_empty_without_next_gw():
    conn = _seed()
    conn.execute("UPDATE gameweeks SET finished=1")
    conn.commit()
    assert build_candidate_pool(conn) == []
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_squad_builder.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `src/decisions/squad_builder.py`:

```python
"""Deterministic candidate pool for the AI squad builder.

The AI never sees all 587 players — this module narrows to a budget-flexible,
legal prefiltered pool (~90-100) so the LLM's judgment is applied to a
tractable set. Every field here lands in the digest the AI is grounded against.
"""
from src.decisions import transfers


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def build_candidate_pool(conn, next_gw=None):
    if next_gw is None:
        next_gw = _next_gw(conn)
    if next_gw is None:
        return []
    rows = conn.execute(
        """SELECT p.id AS player_id, p.web_name, p.position, p.price, p.status,
                  t.short_name AS team_short,
                  x1.xp AS xp_next,
                  SUM(x.xp) AS xp_6gw
           FROM players p
           JOIN teams t ON t.id = p.team_id
           JOIN xp x ON x.player_id = p.id AND x.model_version = 'v1'
                AND x.gw BETWEEN ? AND ?
           JOIN xp x1 ON x1.player_id = p.id AND x1.model_version = 'v1' AND x1.gw = ?
           WHERE p.status IN ('a', 'd') AND p.price >= 4.0
           GROUP BY p.id
        """, (next_gw, next_gw + 5, next_gw)).fetchall()
    players = []
    for r in rows:
        xp6 = round(r["xp_6gw"], 2)
        price = r["price"]
        players.append({
            "player_id": r["player_id"], "web_name": r["web_name"],
            "team_short": r["team_short"], "position": r["position"],
            "price": price, "status": r["status"],
            "xp_next": round(r["xp_next"], 2) if r["xp_next"] is not None else None,
            "xp_6gw": xp6,
            "value": round(xp6 / price, 4) if price else None,
        })
    pool = []
    for pos in ("GKP", "DEF", "MID", "FWD"):
        pos_players = [p for p in players if p["position"] == pos]
        by_xp = sorted(pos_players, key=lambda p: p["xp_6gw"], reverse=True)[:15]
        by_val = sorted((p for p in pos_players if p["value"] is not None),
                        key=lambda p: p["value"], reverse=True)[:10]
        seen = set()
        for p in by_xp + by_val:
            if p["player_id"] not in seen:
                seen.add(p["player_id"])
                pool.append(p)
    pool.sort(key=lambda p: p["xp_6gw"], reverse=True)
    return pool
```

Note: `from src.decisions import transfers` is unused — drop the import if lint flags it (the
module needs no decision-engine coupling; keep it import-free).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_squad_builder.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/decisions/squad_builder.py tests/test_squad_builder.py
git commit -m "feat(decisions): deterministic candidate pool for AI squad builder"
```

---

### Task 2: Validator + greedy fallback

**Files:**
- Create: `src/decisions/squad_validator.py`
- Test: `tests/test_squad_validator.py`

**Interfaces:**
- Consumes: pool dicts from Task 1.
- Produces:
  - `SLOTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}` and `SLOT_NAMES` list
    `["GKP1","GKP2","DEF1",...,"FWD3"]`.
  - `validate_squad(picks, pool) -> list[str]` — problems; empty = legal. `picks` =
    `[{"player_id": int, "slot": str}]`.
  - `optimize_squad(pool) -> list[{"player_id", "slot"}]` — greedy by value desc, guaranteed
    legal; raises `ValueError` if the pool cannot fill a slot (defensive; pool is designed to).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_squad_validator.py`:

```python
import pytest

from src.decisions.squad_validator import SLOTS, optimize_squad, validate_squad

POOL = [
    {"player_id": i, "web_name": f"P{i}", "team_short": f"T{i % 5}",
     "position": pos, "price": price, "status": "a", "xp_next": 5.0,
     "xp_6gw": 30.0 - i, "value": (30.0 - i) / price}
    for i, (pos, price) in enumerate(
        [("GKP", 5.0)] * 3 + [("DEF", 5.0)] * 7 + [("MID", 7.0)] * 7 + [("FWD", 9.0)] * 5)
]


def _picks(*ids):
    slot_order = ["GKP1", "GKP2", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5",
                  "MID1", "MID2", "MID3", "MID4", "MID5", "FWD1", "FWD2", "FWD3"]
    return [{"player_id": pid, "slot": slot_order[i]} for i, pid in enumerate(ids)]


def test_valid_squad_has_no_problems():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert validate_squad(_picks(*ids), POOL) == []


def test_wrong_position_for_slot():
    ids = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 2, 1]  # GKP2 slot gets DEF
    assert any("position" in p for p in validate_squad(_picks(*ids), POOL))


def test_duplicate_player():
    ids = [0, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert any("duplicate" in p for p in validate_squad(_picks(*ids), POOL))


def test_over_budget():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    expensive = [dict(p, price=p["price"] + 5) for p in POOL]
    assert any("budget" in p for p in validate_squad(_picks(*ids), expensive))


def test_three_per_club():
    ids = [0, 5, 10, 1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14]  # T0,T5,T10 all club 0
    assert any("club" in p for p in validate_squad(_picks(*ids), POOL))


def test_unknown_player_rejected():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 9999]
    assert any("unknown" in p for p in validate_squad(_picks(*ids), POOL))


def test_optimize_fallback_is_always_legal():
    for _ in range(20):
        import random
        random.shuffle(POOL)
        picks = optimize_squad(POOL)
        assert len(picks) == 15
        assert validate_squad(picks, POOL) == []
        counts = {}
        for p in picks:
            slot = p["slot"]
            pos = next(x["position"] for x in POOL if x["player_id"] == p["player_id"])
            assert slot.startswith(pos)
            counts[pos] = counts.get(pos, 0) + 1
        assert counts == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_squad_validator.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `src/decisions/squad_validator.py`:

```python
"""The law of the AI squad builder — deterministic, always enforced.

The AI proposes; this module guarantees the squad is legal (formation,
budget, 3-per-club, uniqueness) or explains exactly why not, so the runner can
retry with feedback. optimize_squad is the deterministic fallback that always
produces a legal squad.
"""
from collections import Counter

SLOTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
SLOT_NAMES = [f"{pos}{n}" for pos, n in SLOTS.items() for n in range(1, n + 1)]
MAX_BUDGET = 100.0
MAX_PER_CLUB = 3
EPS = 1e-9


def _by_id(pool):
    return {p["player_id"]: p for p in pool}


def validate_squad(picks, pool):
    problems = []
    players = _by_id(pool)
    if len(picks) != 15:
        return [f"expected 15 picks, got {len(picks)}"]
    used_slots, used_ids = [], []
    for pick in picks:
        pid = pick.get("player_id")
        slot = pick.get("slot")
        if slot not in SLOT_NAMES:
            problems.append(f"unknown slot {slot!r}")
        if pid in used_ids:
            problems.append(f"duplicate player {pid}")
        used_ids.append(pid)
        p = players.get(pid)
        if p is None:
            problems.append(f"unknown player {pid}")
            continue
        expected_pos = slot[0:3].rstrip("0123456789")
        if slot and not slot.startswith(p["position"]):
            problems.append(f"slot {slot} expects {p['position']}, player {pid} is {p['position']}")
        if slot in used_slots:
            problems.append(f"slot {slot} used twice")
        used_slots.append(slot)
    total = sum(players[p["player_id"]]["price"] for p in picks
                if p["player_id"] in players)
    if total > MAX_BUDGET + EPS:
        problems.append(f"budget exceeded: {total:.1f}m > {MAX_BUDGET}m")
    clubs = Counter(players[p["player_id"]]["team_short"] for p in picks
                    if p["player_id"] in players)
    for club, n in clubs.items():
        if n > MAX_PER_CLUB:
            problems.append(f"{n} players from {club}; max is {MAX_PER_CLUB}")
    return problems


def optimize_squad(pool):
    """Greedy fill by value desc; always legal when the pool can fill each slot."""
    by_pos = {pos: sorted([p for p in pool if p["position"] == pos],
                          key=lambda p: (p["value"] or 0), reverse=True)
              for pos in SLOTS}
    picked, clubs, budget = [], Counter(), 0.0
    for pos, n in SLOTS.items():
        for slot_n in range(1, n + 1):
            chosen = None
            for p in by_pos[pos]:
                if p["player_id"] in {x["player_id"] for x in picked}:
                    continue
                if clubs[p["team_short"]] >= MAX_PER_CLUB:
                    continue
                if budget + p["price"] > MAX_BUDGET + EPS:
                    continue
                chosen = p
                break
            if chosen is None:
                raise ValueError(f"pool cannot fill slot {pos}{slot_n}")
            picked.append({"player_id": chosen["player_id"], "slot": f"{pos}{slot_n}"})
            clubs[chosen["team_short"]] += 1
            budget += chosen["price"]
    return picked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_squad_validator.py`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/decisions/squad_validator.py tests/test_squad_validator.py
git commit -m "feat(decisions): legal-squad validator + greedy fallback"
```

---

### Task 3: Squad digest + prompt

**Files:**
- Create: `src/ai/squad/__init__.py`, `src/ai/squad/digest.py`, `src/ai/squad/prompts/squad.md`, `src/ai/squad/prompt.py`
- Test: `tests/test_ai_squad_digest.py`, `tests/test_ai_squad_prompt.py`

**Interfaces:**
- Consumes: `build_candidate_pool` (Task 1).
- Produces: `build_squad_digest(conn, pool=None, next_gw=None) -> dict` —
  `{"next_gw": int, "budget": 100, "players": [ {player_id, web_name, team, position,
  price, xp_next, xp_6gw, xg90, xa90, ownership_pct, form, fixtures_3: [{opponent,
  venue, fdr_attack, fdr_defense}]} ]}` — xg90/xa90 from understat (latest season, rounded 3dp;
  None when absent), ownership/form rounded 1dp. And `build_squad_prompt(digest) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_squad_digest.py`:

```python
import json
import pathlib

from src.ai.squad.digest import build_squad_digest
from src.data.db import connect, init_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _seed():
    from src.data import repository, name_resolver
    from src.data.models import BootstrapStatic, Fixture, UnderstatPlayersResponse
    from src.analytics import fdr, xp

    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(json.loads((FIX / "bootstrap-static.json").read_text()))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
    conn.execute("UPDATE gameweeks SET finished=0 WHERE id=38")
    repository.upsert_fixtures(conn, [Fixture.model_validate(f) for f in json.loads((FIX / "fixtures.json").read_text())])
    us = UnderstatPlayersResponse.model_validate(json.loads((FIX / "understat-players.json").read_text())).players
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(conn, us, res, "2025")
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    conn.commit()
    return conn


def test_digest_shape():
    conn = _seed()
    d = build_squad_digest(conn)
    assert d["next_gw"] == 38 and d["budget"] == 100
    assert 20 <= len(d["players"]) <= 200
    p0 = d["players"][0]
    assert set(p0.keys()) == {"player_id", "web_name", "team", "position", "price",
                              "xp_next", "xp_6gw", "xg90", "xa90", "ownership_pct",
                              "form", "fixtures_3"}
    assert 0 < len(p0["fixtures_3"]) <= 3
    f0 = p0["fixtures_3"][0]
    assert set(f0.keys()) == {"opponent", "venue", "fdr_attack", "fdr_defense"}
    conn.close()
```

`tests/test_ai_squad_prompt.py`:

```python
import json

from src.ai.squad.prompt import build_squad_prompt


def test_prompt_shape():
    digest = {"next_gw": 1, "budget": 100,
              "players": [{"player_id": 1, "web_name": "X", "team": "NEW",
                           "position": "DEF", "price": 5.0, "xp_next": 4.0,
                           "xp_6gw": 20.0, "xg90": 0.1, "xa90": 0.2,
                           "ownership_pct": 10.0, "form": 3.0, "fixtures_3": []}]}
    p = build_squad_prompt(digest)
    assert "## system" in p and "## user" in p
    assert "GKP1" in p and "FWD3" in p
    assert '"player_id": 1' in p
    parsed = p.split("```json")[-1].split("```")[0]
    assert json.loads(parsed) == digest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ai_squad_digest.py tests/test_ai_squad_prompt.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/ai/squad/__init__.py` — empty.

`src/ai/squad/digest.py`:

```python
"""Squad-level digest: the candidate pool + per-player context the AI is
grounded against. Deterministic, closed-shape, no LLM."""


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def build_squad_digest(conn, pool=None, next_gw=None):
    if next_gw is None:
        next_gw = _next_gw(conn)
    if next_gw is None:
        return {"next_gw": None, "budget": 100, "players": []}
    from src.decisions.squad_builder import build_candidate_pool
    pool = pool if pool is not None else build_candidate_pool(conn, next_gw)
    players = []
    for p in pool:
        prior = conn.execute(
            "SELECT xg_per_90, xa_per_90 FROM understat_players "
            "WHERE fpl_player_id=? ORDER BY season DESC LIMIT 1",
            (p["player_id"],)).fetchone()
        fxs = []
        for r in conn.execute(
                "SELECT gw, home_team_id, away_team_id FROM fixtures "
                "WHERE gw BETWEEN ? AND ? ORDER BY gw", (next_gw, next_gw + 2)):
            team_row = conn.execute("SELECT team_id FROM players WHERE id=?",
                                    (p["player_id"],)).fetchone()
            team_id = team_row["team_id"] if team_row else None
            if team_id is None:
                continue
            if r["home_team_id"] != team_id and r["away_team_id"] != team_id:
                continue
            opp_id = r["away_team_id"] if r["home_team_id"] == team_id else r["home_team_id"]
            venue = "H" if r["home_team_id"] == team_id else "A"
            opp = conn.execute("SELECT short_name FROM teams WHERE id=?",
                               (opp_id,)).fetchone()
            fdr = conn.execute("SELECT fdr_attack, fdr_defense FROM fdr "
                               "WHERE team_id=? AND gw=?", (team_id, r["gw"])).fetchone()
            if opp is None or fdr is None:
                continue
            fxs.append({"opponent": opp["short_name"], "venue": venue,
                        "fdr_attack": fdr["fdr_attack"], "fdr_defense": fdr["fdr_defense"]})
        players.append({
            "player_id": p["player_id"], "web_name": p["web_name"],
            "team": p["team_short"], "position": p["position"], "price": p["price"],
            "xp_next": p["xp_next"], "xp_6gw": p["xp_6gw"],
            "xg90": round(prior["xg_per_90"], 3) if prior else None,
            "xa90": round(prior["xa_per_90"], 3) if prior else None,
            "ownership_pct": round(p["price"] * 0, 1),  # replaced below
            "form": 0.0,  # replaced below
            "fixtures_3": fxs,
        })
    return {"next_gw": next_gw, "budget": 100, "players": players}
```

Note: `ownership_pct`/`form` placeholders are wrong — the pool dict has no ownership/form.
Fix by carrying them through the pool: in `squad_builder.build_candidate_pool`, add
`"ownership_pct": round(r["ownership"], 1) if r["ownership"] is not None else None` and
`"form": round(r["form"], 1) if r["form"] is not None else None` (extend the SELECT with
`p.ownership, p.form`), then here use `p["ownership_pct"]` / `p["form"]` directly and drop
the placeholder lines. Update Task 1's pool shape + its test keys accordingly
(`{"player_id", "web_name", "team_short", "position", "price", "status", "xp_next",
"xp_6gw", "value", "ownership_pct", "form"}`).

`src/ai/squad/prompts/squad.md`:

```markdown
## system

You are an FPL squad-builder. You are given a deterministic digest of candidate
players with their projected points, prices, and upcoming fixtures. Your job is
to pick the most optimal starting 15 for the upcoming gameweek.

Constraints (MANDATORY, verified by a validator after you answer):
- exactly 15 picks, one per slot: GKP1 GKP2, DEF1..DEF5, MID1..MID5, FWD1..FWD3
- slot position MUST match the player's position
- total price <= 100
- at most 3 players from the same team
- every player_id must come from the digest; no duplicates

Output ONLY valid JSON matching this schema:

{
  "picks": [
    {"player_id": 449, "slot": "DEF1", "reason": "one sentence on why this player in this slot"}
  ],
  "template_rationale": "2-3 sentences on the formation and structure chosen",
  "risks": ["player-level or structure-level risks"]
}

Rules:
1. Optimize for total xp_6gw within budget — value (xp per million) matters.
2. Weigh fixtures: prefer players whose next 3 fixtures have low fdr_attack
   for attackers, low fdr_defense for defenders/keepers.
3. Spread risk: avoid 3 players from one team unless they are clearly the best
   value; prefer fixture overlap only when the data supports it.
4. Do not invent players or prices — use only digest values.
5. reasons must be 1 sentence, specific to this player's numbers.
6. No hype words. Plain factual reasoning.

## user

Here is the candidate digest:

```json
<DIGEST_JSON>
```

Pick the optimal 15. Output ONLY the JSON.
```

`src/ai/squad/prompt.py`:

```python
"""Squad builder prompt builder."""
import json
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_squad_prompt(digest: dict) -> str:
    template = (_PROMPTS_DIR / "squad.md").read_text()
    return template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ai_squad_digest.py tests/test_ai_squad_prompt.py tests/test_squad_builder.py`
Expected: PASS (update the Task 1 pool test's key-set assertion per the ownership/form addition)

- [ ] **Step 5: Commit**

```bash
git add src/ai/squad tests/test_ai_squad_digest.py tests/test_ai_squad_prompt.py src/decisions/squad_builder.py tests/test_squad_builder.py
git commit -m "feat(ai): squad digest + builder prompt (candidate pool w/ ownership+form)"
```

---

### Task 4: Squad runner (AI pick, retry, fallback, cache, log)

**Files:**
- Create: `src/ai/squad/runner.py`
- Test: `tests/test_ai_squad_runner.py`

**Interfaces:**
- Consumes: `build_squad_digest`, `build_squad_prompt` (Task 3), `validate_squad` +
  `optimize_squad` (Task 2), `src.ai.cache` (`pane_type="squad"`), any `LLMProvider`.
- Produces:
  - `extract_json_object(text) -> dict | None` (re-export from `src.ai.insight.runner`).
  - `generate_squad(conn, *, provider, model_id, max_tokens=3000) -> dict | None` —
    returns the full result `{"picks": [...], "template_rationale", "risks", "source"}`
    (source = "ai" | "optimizer"); caches on success; never raises.

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_squad_runner.py`:

```python
import json

from src.ai import cache
from src.ai.squad import runner
from src.data.db import connect, init_db


def _pool():
    return [
        {"player_id": i, "web_name": f"P{i}", "team_short": f"T{i % 5}",
         "position": pos, "price": price, "status": "a", "xp_next": 5.0,
         "xp_6gw": 30.0 - i, "value": (30.0 - i) / price,
         "ownership_pct": 10.0, "form": 3.0}
        for i, (pos, price) in enumerate(
            [("GKP", 5.0)] * 3 + [("DEF", 5.0)] * 7 + [("MID", 7.0)] * 7 + [("FWD", 9.0)] * 5)
    ]


def _seed(conn):
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.commit()


def _legal_picks_json():
    slots = ["GKP1", "GKP2", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5",
             "MID1", "MID2", "MID3", "MID4", "MID5", "FWD1", "FWD2", "FWD3"]
    return json.dumps({
        "picks": [{"player_id": i, "slot": slots[i], "reason": f"r{i}"} for i in range(15)],
        "template_rationale": "Balanced template.",
        "risks": ["Fixture rotation risk."],
    })


class _Seq:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, prompt, **kw):
        self.calls.append(prompt)
        return self._responses.pop(0)


def test_runner_ai_pick_caches(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    prov = _Seq([_legal_picks_json()])
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "ai"
    assert len(out["picks"]) == 15
    # cached
    prov2 = _Seq([])
    out2 = runner.generate_squad(db, provider=prov2, model_id="m")
    assert out2 == out and prov2.calls == []
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='squad'").fetchone()
    assert row is not None


def test_runner_retries_then_falls_back(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    bad = json.dumps({"picks": [{"player_id": 1, "slot": "GKP1"}],
                      "template_rationale": "x", "risks": []})
    prov = _Seq([bad, bad, bad, bad])
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "optimizer"
    assert len(prov.calls) == 3
    assert "validator" in prov.calls[1].lower() or "legal" in prov.calls[1].lower()


def test_runner_caches_nothing_on_total_failure(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    class Boom:
        def generate(self, prompt, **kw):
            raise RuntimeError("provider down")

    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: None)
    assert runner.generate_squad(db, provider=Boom(), model_id="m") is None
    assert cache.get(db, 1, "squad", cache.recommendation_hash(
        {"next_gw": 1, "budget": 100, "players": _pool()})) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ai_squad_runner.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/ai/squad/runner.py`:

```python
"""Squad runner: digest -> prompt -> LLM picks -> validator -> cache/fallback.

The AI proposes; validate_squad is the law. Illegal proposals get retried with
feedback (<=3); on total failure the deterministic optimize_squad fallback
produces the squad, flagged source="optimizer". Never raises.
"""
import json
import logging

from src.ai import cache
from src.ai.insight.runner import extract_json_object
from src.ai.squad.digest import build_squad_digest
from src.ai.squad.prompt import build_squad_prompt
from src.ai.provider import DeepSeekError, OllamaError
from src.decisions.squad_builder import build_candidate_pool
from src.decisions.squad_validator import validate_squad, optimize_squad

logger = logging.getLogger(__name__)

PANE_TYPE = "squad"
MAX_ATTEMPTS = 3


def _log(conn, gw, model_id, *, result, picks=None, extra=None):
    from src.data import repository
    payload = {"gw": gw, "model_id": model_id, "result": result}
    if picks is not None:
        payload["picks"] = picks
    if extra:
        payload.update(extra)
    repository.log_activity(conn, decision_type="squad", mode="ai",
                            action_taken="squad generate", inputs=payload, executed=True)


def generate_squad(conn, *, provider, model_id, max_tokens: int = 3000,
                   temperature: float = 0.2) -> dict | None:
    pool = build_candidate_pool(conn)
    if not pool:
        return None
    digest = build_squad_digest(conn, pool=pool)
    gw = digest.get("next_gw")
    if gw is None:
        return None
    rec_hash = cache.recommendation_hash(digest)
    hit = cache.get(conn, gw, PANE_TYPE, rec_hash)
    if hit is not None:
        payload = extract_json_object(hit["prose"])
        if payload is not None:
            payload["source"] = payload.get("source", "ai")
            return payload
    prompt = build_squad_prompt(digest)
    for attempt in range(MAX_ATTEMPTS):
        try:
            prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
        except (OllamaError, DeepSeekError):
            logger.exception("ai.squad.provider_error", extra={"gw": gw})
            return None
        payload = extract_json_object(prose) if prose else None
        if payload is None:
            problems = ["not valid JSON"]
        else:
            picks = payload.get("picks")
            problems = validate_squad(picks, pool) if isinstance(picks, list) else \
                ["picks missing or not a list"]
        if not problems:
            payload["source"] = "ai"
            cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(payload, sort_keys=True),
                      model_id)
            _log(conn, gw, model_id, result="passed",
                 picks=[p["player_id"] for p in payload["picks"]])
            return payload
        logger.warning("ai.squad.attempt_rejected",
                       extra={"gw": gw, "attempt": attempt, "problems": problems[:5]})
        if attempt < MAX_ATTEMPTS - 1:
            prompt = f"{prompt}\n\nPrevious proposal was rejected by the validator: " \
                     f"{'; '.join(problems[:5])}. Output ONLY the JSON with a legal squad."
    picks = optimize_squad(pool)
    fallback = {"picks": picks, "template_rationale": "Deterministic fallback: greedy "
                "value-optimized selection.", "risks": [], "source": "optimizer"}
    cache.put(conn, gw, PANE_TYPE, rec_hash, json.dumps(fallback, sort_keys=True), model_id)
    _log(conn, gw, model_id, result="fallback",
         picks=[p["player_id"] for p in fallback["picks"]],
         extra={"problems": problems[:5] if 'problems' in locals() else []})
    return fallback
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ai_squad_runner.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai/squad/runner.py tests/test_ai_squad_runner.py
git commit -m "feat(ai): squad runner — AI picks, validator retry, optimizer fallback"
```

---

### Task 5: API endpoint

**Files:**
- Modify: `src/interface/api.py`
- Test: `tests/test_api_squad_builder.py`

**Interfaces:**
- Consumes: `generate_squad` (Task 4), `build_candidate_pool` (Task 1), `build_squad_digest` (Task 3), `build_provider`, `config.ai_enabled`.
- Produces: `GET /api/squad/builder` per the spec contract; 404 no next GW; `unavailable` on
  runner failure (defensive — fallback makes it rare).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_squad_builder.py`:

```python
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.ai import cache
from src.data.db import connect, init_db
from src.interface import api
from src.interface.deps import get_db

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def _seed(conn):
    from src.data.models import BootstrapStatic, EntryPicks, Fixture, UnderstatPlayersResponse
    from src.data import repository, name_resolver
    from src.analytics import fdr, xp

    bs = BootstrapStatic.model_validate(_load("bootstrap-static.json"))
    repository.upsert_teams(conn, bs.teams)
    repository.upsert_players(conn, bs.elements, bs.element_types)
    repository.upsert_gameweeks(conn, bs.events)
    conn.execute("UPDATE gameweeks SET finished=0 WHERE id=38")
    repository.upsert_fixtures(conn, [Fixture.model_validate(f) for f in _load("fixtures.json")])
    repository.snapshot_my_team(conn, 37, EntryPicks.model_validate(_load("picks.json")))
    us = UnderstatPlayersResponse.model_validate(_load("understat-players.json")).players
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(conn, us, res, "2025")
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    conn.commit()


@pytest.fixture
def client():
    conn = connect(":memory:", check_same_thread=False)
    init_db(conn)
    _seed(conn)

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


def _result():
    return {"picks": [{"player_id": 1, "slot": "GKP1", "reason": "r"}],
            "template_rationale": "T", "risks": [], "source": "ai"}


def test_builder_generates_and_enriches(client, monkeypatch):
    tc, conn = client
    monkeypatch.setattr("src.ai.provider.build_provider", lambda cfg, conn=None: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: _result())
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "ai"
    assert body["picks"][0]["web_name"]  # enriched from the players table
    assert body["budget_used"] is not None


def test_builder_cached_hit_skips_generation(client, monkeypatch):
    tc, conn = client
    from src.ai.squad.digest import build_squad_digest
    from src.ai.squad import runner as squad_runner
    digest = build_squad_digest(conn)
    rec_hash = cache.recommendation_hash(digest)
    cache.put(conn, 38, "squad", rec_hash, json.dumps(_result(), sort_keys=True), "m")

    def _boom(*a, **kw):
        raise AssertionError("must not regenerate on cache hit")

    monkeypatch.setattr(squad_runner, "generate_squad", _boom)
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    assert r.json()["status"] == "cached"


def test_builder_unavailable_when_runner_fails(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr("src.ai.squad.runner.generate_squad", lambda *a, **kw: None)
    r = tc.get("/api/squad/builder")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_api_squad_builder.py`
Expected: FAIL (404 — no route)

- [ ] **Step 3: Implement the endpoint**

Append to `src/interface/api.py` (after `player_insight`):

```python
@app.get("/api/squad/builder")
def squad_builder(conn=Depends(get_db)):
    """AI-picked starting squad. Cache-first; validates + enriches the picks."""
    from src import config
    from src.ai import cache as ai_cache
    from src.ai.squad import runner
    from src.decisions.squad_builder import build_candidate_pool

    pool = build_candidate_pool(conn)
    if not pool:
        return JSONResponse(status_code=404, content={"detail": "no upcoming gameweek"})
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    gw = nxt["gw"] if nxt else None
    if not config.ai_enabled():
        return {"status": "unavailable", "reason": "ai_disabled"}
    try:
        from src.ai.provider import build_provider
        provider = build_provider(config.load_config())
        result = runner.generate_squad(conn, provider=provider,
                                       model_id=config.ai_deepseek_model())
    except Exception:
        return {"status": "unavailable", "reason": "provider_error"}
    if result is None:
        return {"status": "unavailable", "reason": "gate_rejected"}
    digest = runner.build_squad_digest(conn, pool=pool)
    rec_hash = ai_cache.recommendation_hash(digest)
    hit = ai_cache.get(conn, gw, runner.PANE_TYPE, rec_hash)
    by_id = {p["player_id"]: p for p in pool}
    picks = []
    for pk in result["picks"]:
        p = by_id.get(pk["player_id"])
        if p is None:
            continue
        picks.append({"player_id": pk["player_id"], "web_name": p["web_name"],
                      "team": p["team_short"], "position": p["position"],
                      "price": p["price"], "xp_6gw": p["xp_6gw"],
                      "slot": pk["slot"], "reason": pk.get("reason", "")})
    budget_used = round(sum(p["price"] for p in picks), 1)
    return {
        "status": "cached" if hit is not None else "generated",
        "gw": gw, "source": result.get("source", "ai"),
        "picks": picks, "template_rationale": result.get("template_rationale", ""),
        "risks": result.get("risks", []), "budget_used": budget_used,
        "model_id": config.ai_deepseek_model(),
        "generated_at": hit["generated_at"] if hit is not None else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_api_squad_builder.py tests/test_api.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/interface/api.py tests/test_api_squad_builder.py
git commit -m "feat(interface): GET /api/squad/builder endpoint"
```

---

### Task 6: Frontend /squad-builder page

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api/client.ts`
- Create: `frontend/src/routes/squad-builder/+page.ts`, `+page.svelte`, `page.svelte.test.ts`

**Interfaces:**
- Consumes: `getJson<T>` client pattern; mirror the `players/[id]` page structure (runes,
  `onMount` fetch, `prerender = false`).
- Produces: `fetchSquadBuilder(fetchFn?)`; page renders pitch rows per position with
  `PlayerCard` + slot + reason, rationale lead, risks, budget line, source badge, apply hint.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/squad-builder/page.svelte.test.ts` (mirror the insight page test):

```ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

const builder = {
	status: 'generated',
	gw: 38,
	source: 'ai',
	picks: [
		{ player_id: 1, web_name: 'Hall', team: 'NEW', position: 'DEF', price: 5.0,
		  xp_6gw: 24.3, slot: 'DEF1', reason: 'Stable minutes and value.' }
	],
	template_rationale: 'Balanced template with a value defense.',
	risks: ['Fixture rotation risk.'],
	budget_used: 99.5,
	model_id: 'deepseek-chat',
	generated_at: '2026-08-14T00:00:00Z'
};

afterEach(() => vi.unstubAllGlobals());

describe('squad-builder page', () => {
	it('renders picks with reasons and budget', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => builder }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText('Balanced template with a value defense.'));
		expect(screen.getByText('Hall')).toBeInTheDocument();
		expect(screen.getByText('99.5m used')).toBeInTheDocument();
		expect(screen.getByText(/apply-squad/)).toBeInTheDocument();
	});
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL (files don't exist)

- [ ] **Step 3: Implement**

`frontend/src/lib/types.ts` — append:

```ts
export interface SquadPick {
	player_id: number;
	web_name: string;
	team: string;
	position: Position;
	price: number;
	xp_6gw: number;
	slot: string;
	reason: string;
}

export interface SquadBuilder {
	status: 'cached' | 'generated' | 'unavailable';
	gw: number | null;
	source: 'ai' | 'optimizer';
	picks: SquadPick[];
	template_rationale: string;
	risks: string[];
	budget_used: number;
	model_id: string | null;
	generated_at: string | null;
}
```

`frontend/src/lib/api/client.ts` — append:

```ts
import type { SquadBuilder } from '../types';

export async function fetchSquadBuilder(fetchFn: Fetch = fetch): Promise<SquadBuilder> {
	return getJson<SquadBuilder>('/api/squad/builder', fetchFn);
}
```

`frontend/src/routes/squad-builder/+page.ts`:

```ts
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = async () => ({});
```

`frontend/src/routes/squad-builder/+page.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchSquadBuilder } from '$lib/api/client';
	import type { SquadBuilder } from '$lib/types';
	import PlayerCard from '$lib/components/PlayerCard.svelte';

	let builder = $state<SquadBuilder | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			builder = await fetchSquadBuilder();
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	const row = (pos: string) => builder?.picks.filter((p) => p.position === pos) ?? [];
</script>

<svelte:head><title>Squad Builder — FPL Autopilot</title></svelte:head>

<div class="builder-page">
	<h1>AI Squad Builder</h1>

	{#if loading}
		<p class="muted">Building your optimal squad — the first time takes up to a minute.</p>
	{:else if error || builder?.status === 'unavailable'}
		<p class="muted">Squad builder unavailable.</p>
		<button onclick={() => location.reload()}>Retry</button>
	{:else if builder}
		<p class="lead">
			<span class="chip">{builder.source === 'ai' ? 'AI' : 'optimizer'}</span>
			{builder.template_rationale}
		</p>
		{#each ['GKP', 'DEF', 'MID', 'FWD'] as pos}
			<section class="line">
				{#each row(pos) as pk}
					<div class="pick">
						<PlayerCard player={pickCard(pk)} />
						<span class="slot">{pk.slot}</span>
						<p class="reason">{pk.reason}</p>
					</div>
				{/each}
			</section>
		{/each}
		<p class="budget">Budget: {builder.budget_used}m used / 100m</p>
		{#if builder.risks?.length}
			<ul class="risks">{#each builder.risks as r}<li>{r}</li>{/each}</ul>
		{/if}
		<p class="muted hint">Apply it: run <code>fpl-autopilot apply-squad --live</code> on the server.</p>
	{/if}
</div>

<script context="module">
	import type { SquadPick } from '$lib/types';
	function pickCard(pk: SquadPick) {
		return { id: pk.player_id, web_name: pk.web_name, position: pk.position,
			team_short: pk.team, price: pk.price, status: 'a' as const, is_captain: false,
			is_vice_captain: false, multiplier: 1, xp_next: null, xp_next5: null };
	}
</script>

<style>
	.builder-page { padding: 1.25rem 0 2rem; max-width: 680px; margin: 0 auto; }
	h1 { font-size: 1.5rem; margin: 0 0 1rem; }
	.lead { display: flex; gap: 0.5rem; align-items: baseline; background: var(--surface);
		border-left: 3px solid var(--accent); padding: 0.75rem 1rem; border-radius: 0 var(--radius) var(--radius) 0; }
	.chip { font-size: 0.72rem; font-weight: 600; padding: 0.15rem 0.5rem; border-radius: 999px;
		background: var(--surface-2); color: var(--accent); }
	.line { display: grid; grid-auto-flow: column; gap: 8px; overflow-x: auto; padding: 10px 0;
		border-bottom: 1px dashed var(--border); }
	.pick { min-width: 120px; }
	.slot { font-size: 0.7rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
	.reason { font-size: 0.8rem; color: var(--text-dim); margin: 0.3rem 0 0; }
	.budget { margin-top: 1rem; font-weight: 600; }
	.risks { color: var(--text-dim); font-size: 0.85rem; }
	.hint code { background: var(--surface-2); padding: 0.1rem 0.3rem; border-radius: 4px; }
	.muted { color: var(--text-dim); }
	button { background: var(--accent); color: #04100b; border: none; border-radius: var(--radius);
		padding: 0.45rem 1.1rem; font-weight: 600; cursor: pointer; }
</style>
```

Note: if `PlayerCard`'s props differ from `pickCard`'s shape, adjust to its actual interface
(check `PlayerCard.svelte` props in Task 6 before finalizing; the `status` literal must match
`PlayerStatus`).

- [ ] **Step 4: Run tests + build to verify**

Run: `cd frontend && npm test && npm run build`
Expected: PASS + build succeeds

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api/client.ts frontend/src/routes/squad-builder
git commit -m "feat(frontend): /squad-builder page with AI picks and rationale"
```

---

### Task 7: Apply executor (squad.py)

**Files:**
- Create: `src/execution/squad.py`
- Test: `tests/test_execution_squad.py`

**Interfaces:**
- Consumes: `executor.fetch_current_picks`, `executor.build_transfer_payload`,
  `executor.apply_transfers`, `executor.TRANSFERS_URL`, `auth_session.ensure_session`,
  `config.team_id`, `repository.log_activity`, the builder result (via
  `runner.generate_squad(..., provider=...)` — provider injected for tests).
- Produces:
  - `plan_squad_transfers(conn, target_picks) -> list[dict]` — pure: current my_team picks vs
    target player_ids → `[{"element_out", "element_in", "out_name", "in_name"}]` (empty when
    identical).
  - `apply_squad(conn, key, *, live=False, confirm_fn=None, session=None, provider=None,
    model_id="deepseek-chat") -> dict` — `{"applied": [...], "failed": [...], "dry_run": bool}`;
    refuses when no diff; sequential POSTs; any failure aborts and reports; B10 logs
    `decision_type="squad"` per transfer outcome.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_execution_squad.py`:

```python
import pytest

from src.data.db import connect, init_db
from src.execution import squad


def _seed_conn():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    for pid, name in [(10, "Old1"), (11, "Old2"), (12, "Keep")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, 'DEF', 5.0, 'a', 10.0, 3.0)",
                     (pid, name))
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?,
                 '2026-08-22T08:00:00Z')",
                 ('[{"element": 10, "position": 1, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 11, "position": 2, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 12, "position": 3, "is_captain": false, "is_vice_captain": false, "multiplier": 1}]',))
    conn.commit()
    return conn


def test_plan_builds_out_in_pairs():
    conn = _seed_conn()
    target = [{"player_id": 12}, {"player_id": 20}, {"player_id": 21}]
    plan = squad.plan_squad_transfers(conn, target)
    outs = {p["element_out"] for p in plan}
    assert outs == {10, 11}
    assert {p["element_in"] for p in plan} == {20, 21}
    conn.close()


def test_plan_empty_when_identical():
    conn = _seed_conn()
    target = [{"player_id": 10}, {"player_id": 11}, {"player_id": 12}]
    assert squad.plan_squad_transfers(conn, target) == []
    conn.close()


def test_apply_squad_dry_run_posts_nothing(monkeypatch):
    conn = _seed_conn()
    monkeypatch.setattr("src.execution.executor.apply_transfers",
                        lambda s, e, payload, dry_run: None)
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50},
                                      {"element": 11, "selling_price": 50},
                                      {"element": 12, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    out = squad.apply_squad(conn, b"key", live=False, provider=None)
    assert out["dry_run"] is True and len(out["applied"]) == 1
    row = conn.execute("SELECT * FROM activity_log WHERE decision_type='squad'").fetchone()
    assert row is not None
    conn.close()


def test_apply_squad_refuses_when_no_diff(monkeypatch):
    conn = _seed_conn()
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers", lambda c, target: [])
    out = squad.apply_squad(conn, b"key", live=True, provider=None)
    assert out["applied"] == [] and out["failed"] == ["no changes to apply"]
    conn.close()


def test_apply_squad_aborts_on_api_refusal(monkeypatch):
    conn = _seed_conn()

    class _Refused(Exception):
        pass

    def _apply(session, entry, payload, dry_run):
        raise _Refused("transfer refused by API")

    monkeypatch.setattr("src.execution.executor.apply_transfers", _apply)
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    out = squad.apply_squad(conn, b"key", live=True, provider=None)
    assert out["failed"] and "refused" in out["failed"][0].lower()
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_execution_squad.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `src/execution/squad.py`:

```python
"""Apply the AI-built squad to FPL (pre-season unlimited-transfer window).

Dry-run by default; --live requires the master key + typed confirm (R3: the
user drives live). Builds out/in pairs from the current snapshot and submits
them sequentially via the shared executor; any API refusal aborts the rest and
reports what applied. Deviation from the design spec: we call
executor.apply_transfers directly per pair instead of threading a rebuild mode
through run_transfer — same API behavior, no coupling to the suggestion engine.
"""
import json

from src import config
from src.auth import session as auth_session
from src.execution import executor
from src.data import repository


def plan_squad_transfers(conn, target_picks):
    """Pure: current my_team snapshot vs target player ids -> out/in pairs."""
    snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    current = [pk["element"] for pk in json.loads(snap["picks_json"])] if snap else []
    target = [pk["player_id"] for pk in target_picks]
    names = {r["id"]: r["web_name"] for r in conn.execute(
        "SELECT id, web_name FROM players WHERE id IN (%s)"
        % ",".join("?" * (len(current) + len(target))), current + target)}
    keep = set(target) & set(current)
    outs = [pid for pid in current if pid not in keep]
    ins = [pid for pid in target if pid not in keep]
    pairs = []
    for i, out_id in enumerate(outs):
        in_id = ins[i] if i < len(ins) else None
        if in_id is None:
            break
        pairs.append({"element_out": out_id, "element_in": in_id,
                      "out_name": names.get(out_id), "in_name": names.get(in_id)})
    return pairs


def apply_squad(conn, key, *, live=False, confirm_fn=None, session=None, provider=None,
                model_id="deepseek-chat"):
    from src.ai.squad import runner

    result = runner.generate_squad(conn, provider=provider, model_id=model_id)
    if result is None:
        return {"applied": [], "failed": ["squad builder produced no result"], "dry_run": not live}
    pairs = plan_squad_transfers(conn, result["picks"])
    if not pairs:
        repository.log_activity(conn, decision_type="squad", mode="manual",
                                action_taken="refused: squad already matches",
                                inputs={"picks": [p["player_id"] for p in result["picks"]]},
                                executed=False)
        return {"applied": [], "failed": ["no changes to apply"], "dry_run": not live}
    if live and (confirm_fn is None or not confirm_fn(f"{len(pairs)} transfers to apply")):
        return {"applied": [], "failed": ["aborted by user"], "dry_run": False}
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    applied, failed = [], []
    for pair in pairs:
        try:
            current = executor.fetch_current_picks(session, entry)
            selling_price = next((p["selling_price"] for p in current
                                  if p["element"] == pair["element_out"]), None)
            if selling_price is None:
                raise executor.ExecutorError(
                    f"{pair['out_name']} not in current squad")
            payload = executor.build_transfer_payload(
                entry=entry, event=config_team_next_gw(conn),  # see helper below
                element_out=pair["element_out"], element_in=pair["element_in"],
                selling_price=selling_price, purchase_price=price_of(conn, pair["element_in"]))
            result = executor.apply_transfers(session, entry, payload, dry_run=not live)
            if result is None or getattr(result, "ok", True) is False:
                raise executor.ExecutorError("transfer refused by API")
            applied.append(pair)
        except Exception as exc:
            failed.append(f"{pair['out_name']} -> {pair['in_name']}: {exc}")
            break
        repository.log_activity(conn, decision_type="squad", mode="manual",
                                action_taken=f"apply {pair['out_name']} IN {pair['in_name']}",
                                inputs={"pair": pair, "live": live}, executed=live)
    return {"applied": applied, "failed": failed, "dry_run": not live}
```

Note: the plan's `apply_squad` references two helpers that must exist or be inlined:
- `config_team_next_gw(conn)` → inline: `nxt = conn.execute("SELECT MIN(id) AS gw FROM
  gameweeks WHERE finished=0").fetchone(); event = nxt["gw"] if nxt else None`.
- `price_of(conn, pid)` → inline: `conn.execute("SELECT price FROM players WHERE id=?",
  (pid,)).fetchone()["price"] * 10` (purchase price in 10ths).
Implement them as module-private functions `_next_gw(conn)` and `_purchase_price(conn, pid)`
and use them; the tests above don't exercise the payload path with a live API (R3), so the
dry-run test asserts shape only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_execution_squad.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/execution/squad.py tests/test_execution_squad.py
git commit -m "feat(execution): apply-squad executor (dry-run default, abort-on-refusal)"
```

---

### Task 8: CLI apply-squad

**Files:**
- Modify: `src/cli.py` (add parser + handler near `execute-transfer`)
- Test: `tests/test_cli_apply_squad.py`

**Interfaces:**
- Consumes: `apply_squad` (Task 7), master-key loading + typed confirm patterns from the
  existing `execute-transfer` CLI.
- Produces: `fpl-autopilot apply-squad [--live]` — dry-run prints the plan; `--live` prompts
  for the master password (if needed) + typed confirm, then calls `apply_squad(live=True)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_apply_squad.py`:

```python
import pytest

from src import cli
from src.data.db import connect, init_db


def _seed(conn):
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                 "ownership, form) VALUES (10, 'Old1', 1, 'DEF', 5.0, 'a', 10.0, 3.0)")
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?, "
                 "'2026-08-22T08:00:00Z')",
                 ('[{"element": 10, "position": 1, "is_captain": false, "is_vice_captain": false, "multiplier": 1}]',))
    conn.commit()


def test_apply_squad_dry_run_prints_plan(monkeypatch, capsys):
    from src.execution import squad
    conn = connect(":memory:")
    _seed(conn)
    monkeypatch.setattr(squad, "plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr(squad, "apply_squad", lambda *a, **kw: {
        "applied": [], "failed": [], "dry_run": True})
    cli._cmd_apply_squad(conn=conn, live=False)
    out = capsys.readouterr().out
    assert "Old1" in out and "New1" in out and "dry" in out.lower()
    conn.close()


def test_apply_squad_live_requires_confirm(monkeypatch, capsys):
    from src.execution import squad
    conn = connect(":memory:")
    _seed(conn)
    monkeypatch.setattr(squad, "plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr(squad, "apply_squad", lambda *a, **kw: {
        "applied": [], "failed": ["aborted by user"], "dry_run": False})
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "pw")
    monkeypatch.setattr("src.auth.master.load_key", lambda pw: b"key")
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    cli._cmd_apply_squad(conn=conn, live=True)
    out = capsys.readouterr().out
    assert "aborted" in out.lower() or "cancelled" in out.lower()
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_cli_apply_squad.py`
Expected: FAIL — `cli._cmd_apply_squad` doesn't exist

- [ ] **Step 3: Implement**

In `src/cli.py`, add after the `execute-transfer` handler:

```python
def _cmd_apply_squad(*, conn=None, live=False):
    """Apply the AI-built squad (dry-run default; --live = typed confirm)."""
    import getpass
    from .execution import squad
    from .auth import master, session as auth_session

    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    key = None
    if live:
        if master.is_initialized():
            pw = getpass.getpass("Master password: ")
            key = master.load_key(pw)
        else:
            print("Error: master password not initialized (run init-master-password).")
            return
    try:
        result = squad.apply_squad(
            conn, key,
            live=live,
            confirm_fn=(lambda diff: input(f"{diff}. Type 'apply' to confirm: ").strip()
                        == "apply") if live else None,
        )
        print(f"Applied: {len(result['applied'])} | Failed: {result['failed'] or 'none'}")
        if not live:
            print("Dry-run — nothing was written. Re-run with --live to apply.")
    finally:
        if owns:
            conn.close()
```

Register the parser next to `execute-transfer`:

```python
    p_apply_squad = sub.add_parser("apply-squad", help="apply the AI-built squad (dry-run unless --live)")
    p_apply_squad.add_argument("--live", action="store_true",
                               help="actually submit the transfers (requires master password + confirm)")
```

And dispatch in `main()`: `elif args.command == "apply-squad": _cmd_apply_squad(live=args.live)`.

Note: match the exact confirm/abort wording the tests assert; if `input()` is monkeypatched to
return "n", the confirm returns False and `apply_squad` reports `aborted by user` — print that
line so the test's assertion holds.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_cli_apply_squad.py tests/test_cli_init_fpl.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_apply_squad.py
git commit -m "feat(cli): apply-squad command (dry-run default, live with confirm)"
```

---

### Task 9: B4 doc entry + docs + full verification

**Files:**
- Modify: `docs/decision-engine.md` (NEW §"Squad builder (S-B)" — the B4 entry, first),
  `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md` (changelog v0.4),
  `docs/architecture.md` (AI box line), `docs/runbook.md` (§10 extension), `docs/onboarding.md`

- [ ] **Step 1: decision-engine.md — add the B4 entry**

Append a new section (place it after the chip recommender section):

```markdown
## Squad builder (S-B) — AI-assisted starting squad

**Status:** added 2026-08-14 (B4 entry; spec `docs/superpowers/specs/2026-08-14-squad-builder-design.md`).

**Division of labour (unchanged principle: deterministic law, inspectable output):**

1. **Deterministic candidate pool** (`src/decisions/squad_builder.py`): legal prefilter
   (status a/d, price ≥ 4.0, xP present), per-position top-15 by xP-6-GW ∪ top-10 by value,
   ~90-100 players. Logged with the result.
2. **AI proposes** (`src/ai/squad/runner.py`): the LLM picks the 15 and explains each pick.
   AI is upstream of this decision — the first such case in the system.
3. **Validator is the law** (`src/decisions/squad_validator.py`): 2-5-5-3, budget ≤ 100,
   ≤ 3/club, unique ids from the digest. Violations → retry with feedback (≤3) → deterministic
   greedy fallback (`source: "optimizer"`, never silent).
4. **Apply** (`src/execution/squad.py`): dry-run default; `--live` requires master key + typed
   confirm; any API refusal aborts with a report. Applies only when the API allows
   (pre-season unlimited window; wildcard).
5. **Logging (B10):** every squad decision logs `decision_type="squad"` with the pool, picks,
   validator result, source, budget, and per-transfer outcomes.

No thresholds changed. The squad the AI proposes is a **suggestion** until the user executes
it — execution rules (chips, hits, caps) are unchanged.
```

- [ ] **Step 2: Other docs**

- AI architecture spec changelog (prepend):

```markdown
| v0.4 | 2026-08-14 | New consumer: AI squad builder (`src/ai/squad/`) — candidate pool → AI picks 15 → legal-squad validator → deterministic fallback; apply-on-confirm executor. B4 entry in `decision-engine.md` §"Squad builder (S-B)". See `2026-08-14-squad-builder-design.md`. |
```

- `docs/architecture.md` AI box: add `│   Squad builder (AI picks + validator)   │` line.
- `docs/runbook.md` §10 append:

```markdown
**Squad builder unavailable / apply-squad fails:** check `ai.squad.*` log entries
(`ai.squad.attempt_rejected`, `provider_error`). `apply-squad --live` requires the master
password on the host and works only while FPL allows unlimited transfers (pre-season /
wildcard). API refusals abort with a per-pair report in the `squad` activity-log rows.
```

- `docs/onboarding.md` AI section append:

```markdown
**AI squad builder:** `/squad-builder` on the dashboard suggests a starting 15. Apply with
`fpl-autopilot apply-squad --live` on the server (dry-run first — it prints the plan).
```

- [ ] **Step 3: Full verification**

```bash
.venv/bin/pytest -q              # baseline 658 + ~20 new = ~678 passed
cd frontend && npm test          # baseline 70 + 1 = 71 passed
cd frontend && npm run build     # green
```

- [ ] **Step 4: Commit**

```bash
git add docs/decision-engine.md docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md docs/architecture.md docs/runbook.md docs/onboarding.md
git commit -m "docs(ai): squad builder — B4 entry, changelog, runbook, onboarding"
```

---

## Self-review notes

- **Spec coverage:** pool (T1), validator+fallback (T2), digest+prompt (T3), runner (T4), API
  (T5), page (T6), apply executor (T7), CLI (T8), B4/docs (T9). Out-of-scope items (Telegram
  one-tap, wildcard-aware rebuild) untouched.
- **Type consistency:** `build_candidate_pool(conn, next_gw=None)` used by digest+runner+API;
  `validate_squad(picks, pool)` / `optimize_squad(pool)` shared by runner and tests;
  `apply_squad(conn, key, *, live, confirm_fn, session, provider, model_id)` used by CLI.
- **Open items flagged inline:** pool ownership/form fields (T3 note updates T1's shape — the
  implementer must update both together), `_next_gw`/`_purchase_price` helpers in T7,
  `PlayerCard` props check in T6, CLI confirm wording in T8.
