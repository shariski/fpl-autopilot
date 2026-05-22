# Transfer Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Decision-Layer transfer engine — pure functions that turn `xp`/`players`/`my_team` into the top-3 sell→buy suggestions, plus a thin `get_transfer_suggestions(conn)` reader returning the `/api/transfers` shape, with property-tested squad-validity invariants.

**Architecture:** Mirror the established analytics idiom (`fdr.py`, `xp.py`): a *pure* core operating on plain dicts (`xp_5gw_by_player`, `sell_candidates`, `buy_candidates`, `hit_cost`, `is_worth_hit`, `suggest_transfers`) plus a DB-bound reader (`get_transfer_suggestions(conn)`). The Decision Layer consumes Analytics output only — it reads `xp`, `players`, `my_team` from the DB and never touches the network (CLAUDE.md B2). Suggest-only (Phase 1): no persistence, no execution.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (rows via `sqlite3.Row`), `statistics.median`, `json`. Tests with `pytest` + stdlib `random` for property tests (Hypothesis is **not** installed and `pyproject.toml` is **off-limits**, so property tests use seeded `random` generators — same rigor, no new dependency).

**Isolation (critical):** Touch ONLY `src/decisions/transfers.py` and `tests/test_transfers.py` (plus this plan doc). Do NOT modify `pyproject.toml`, `src/decisions/__init__.py`, or `docs/decision-engine.md` — they are already on `main`. Run tests from the worktree root with:

```bash
PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -q
```

**v1 substitutions already baked into this design (B4, documented in `decision-engine.md`):**
1. No `form_adjusted_delta` sell criterion (no per-GW actuals). Sell = `xP_5gw < position median` OR `status != 'a'`.
2. Selling price = current `price` (true sell price is auth-only, Phase 2).
3. Free transfers assumed 1 → every single suggested transfer is free → `hit_cost = 0`. The `hit_cost`/`is_worth_hit` helpers exist and are tested for the future multi-transfer path but are not on the v1 suggestion path.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/decisions/transfers.py` (create) | All pure functions + `get_transfer_suggestions(conn)` reader. ~120 lines. |
| `tests/test_transfers.py` (create) | Unit tests (spec §7) + seeded-`random` property tests (B11). |

### Shared data shapes (used across tasks — define once, reuse verbatim)

A **player dict** (what the pure functions consume; the reader builds these from the DB):

```python
{"player_id": int, "web_name": str, "position": str,  # "GKP"|"DEF"|"MID"|"FWD"
 "team_id": int, "price": float, "status": str,        # "a"|"d"|"i"|"s"|"u"
 "xp_5gw": float}
```

A **suggestion pair** returned by `suggest_transfers` (full player dicts inside `out`/`in` so property tests can apply the swap):

```python
{"out": <player dict>, "in": <player dict>, "ep_delta_5gw": float, "hit_cost": 0}
```

The reader projects each pair to the `/api/transfers` contract shape:

```python
{"out": {"player_id", "web_name", "price"},
 "in":  {"player_id", "web_name", "price"},
 "ep_delta_5gw": float, "hit_cost": 0, "confidence": None}
```

### Module-level constants (top of `src/decisions/transfers.py`)

```python
import json
from statistics import median

POSITIONS = ("GKP", "DEF", "MID", "FWD")
MAX_PER_CLUB = 3
HORIZON = 5
EMPTY_REASON = "No transfers worth making this GW."
_EPS = 1e-9  # float tolerance for price comparisons
```

### Test-file scaffolding (top of `tests/test_transfers.py` — referenced by later tasks)

```python
import json
import random
from collections import Counter

from src.decisions import transfers


def _p(pid, pos, team, price, status, xp5):
    """Build a player dict for the pure-function tests."""
    return {"player_id": pid, "web_name": f"P{pid}", "position": pos,
            "team_id": team, "price": price, "status": status, "xp_5gw": xp5}


def _pick_valid_squad(market, rng):
    """Greedily pick a legal 15-man squad (2 GKP, 5 DEF, 5 MID, 3 FWD, <=3/club) from `market`."""
    need = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    chosen, chosen_ids, club = [], set(), {}
    pool = market[:]
    rng.shuffle(pool)
    for pos, n in need.items():
        got = 0
        for p in pool:
            if got == n:
                break
            if p["position"] != pos or p["player_id"] in chosen_ids:
                continue
            if club.get(p["team_id"], 0) >= MAX_PER_CLUB if (MAX_PER_CLUB := 3) else False:  # placeholder
                continue
            chosen.append(p); chosen_ids.add(p["player_id"])
            club[p["team_id"]] = club.get(p["team_id"], 0) + 1
            got += 1
        assert got == n, f"market too small for {pos}"
    return chosen


def _random_market_and_squad(seed):
    """Deterministic random market (160 players, 20 clubs) + a legal squad + bank."""
    rng = random.Random(seed)
    market, pid = [], 1
    for pos, count in (("GKP", 20), ("DEF", 40), ("MID", 40), ("FWD", 20)):
        for _ in range(count):
            market.append(_p(pid, pos, rng.randint(1, 20),
                             round(rng.uniform(4.0, 13.0), 1),
                             rng.choice(["a", "a", "a", "i", "d"]),
                             round(rng.uniform(0.0, 40.0), 2)))
            pid += 1
    squad = _pick_valid_squad(market, rng)
    return market, squad, round(rng.uniform(0.0, 5.0), 1)


def _seed_db(db, players, squad_ids, bank, next_gw=1):
    """Seed gameweeks/players/xp/my_team for the reader integration tests.
    `players` are player dicts ({id, web_name, position, team_id, price, status, xp5});
    each player's xp5 is spread evenly across the 5-GW window."""
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (?, 'GW', 0)", (next_gw,))
    for p in players:
        db.execute(
            "INSERT INTO players (id, web_name, team_id, position, price, status) VALUES (?,?,?,?,?,?)",
            (p["id"], p["web_name"], p["team_id"], p["position"], p["price"], p["status"]))
        for g in range(next_gw, next_gw + HORIZON):
            db.execute(
                "INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs,"
                " computed_at) VALUES (?,?, 'v1', ?, 0, 0, 0, 0, 't')",
                (p["id"], g, p["xp5"] / HORIZON))
    picks = json.dumps([{"element": pid, "position": i + 1, "multiplier": 1,
                         "is_captain": False, "is_vice_captain": False}
                        for i, pid in enumerate(squad_ids)])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, snapshot_at) VALUES (?,?,?,'t')",
               (next_gw, picks, bank))
    db.commit()


from src.decisions.transfers import HORIZON, MAX_PER_CLUB  # noqa: E402  (constants for the helpers above)
```

> **NOTE for the executor:** the `_pick_valid_squad` body above contains a deliberate placeholder line (`if club.get(... := 3) ...`). Replace that one line with the clean version shown in Task 5 before use. It is written cleanly in Task 5 Step 1 — do not copy the placeholder.

---

### Task 1: `xp_5gw_by_player` — horizon sum

**Files:**
- Create: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Write the test scaffolding + the first failing test**

First add the imports and the `_p` helper (full scaffolding block above can be added incrementally; for this task only `json`, `random`, `Counter`, `transfers`, and `_p` are needed). Then:

```python
def test_xp_5gw_sums_five_gws():
    rows = [{"player_id": 1, "gw": g, "xp": 2.0} for g in range(10, 15)]  # gw 10..14 -> 5 rows
    rows.append({"player_id": 1, "gw": 15, "xp": 99.0})                   # outside the window
    rows.append({"player_id": 2, "gw": 10, "xp": 1.0})
    out = transfers.xp_5gw_by_player(rows, 10)
    assert out[1] == 10.0   # 5 * 2.0; gw 15 excluded
    assert out[2] == 1.0
    assert 99.0 not in out.values()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_xp_5gw_sums_five_gws -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.decisions.transfers'` (or `AttributeError`).

- [ ] **Step 3: Create the module with constants + `xp_5gw_by_player`**

```python
import json
from statistics import median

POSITIONS = ("GKP", "DEF", "MID", "FWD")
MAX_PER_CLUB = 3
HORIZON = 5
EMPTY_REASON = "No transfers worth making this GW."
_EPS = 1e-9


def xp_5gw_by_player(xp_rows, start_gw, horizon=HORIZON):
    """Sum each player's xp over GWs [start_gw, start_gw + horizon - 1].

    `xp_rows`: iterable of mappings with player_id, gw, xp (already filtered to model v1).
    Returns {player_id: summed_xp} rounded to 2dp. Players with no rows are absent;
    callers default a missing player to 0.0.
    """
    end_gw = start_gw + horizon - 1
    sums = {}
    for r in xp_rows:
        if start_gw <= r["gw"] <= end_gw:
            sums[r["player_id"]] = sums.get(r["player_id"], 0.0) + r["xp"]
    return {pid: round(v, 2) for pid, v in sums.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_xp_5gw_sums_five_gws -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): xp_5gw_by_player horizon sum"
```

---

### Task 2: `hit_cost` + `is_worth_hit` — the hit calculator (B11 thresholds)

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Write the failing test**

```python
def test_hit_cost_thresholds():
    assert transfers.hit_cost(1, 1) == 0
    assert transfers.hit_cost(2, 1) == -4
    assert transfers.hit_cost(3, 1) == -8
    assert transfers.hit_cost(2, 2) == 0          # 2 FT covers 2 transfers
    # is_worth_hit: ep_delta must exceed the absolute hit
    assert transfers.is_worth_hit(5.0, -4) is True    # 5 > 4
    assert transfers.is_worth_hit(3.0, -4) is False   # 3 < 4
    assert transfers.is_worth_hit(0.1, 0) is True     # free transfer, any positive gain
    assert transfers.is_worth_hit(0.0, 0) is False    # free transfer, no gain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_hit_cost_thresholds -q`
Expected: FAIL — `AttributeError: module 'src.decisions.transfers' has no attribute 'hit_cost'`.

- [ ] **Step 3: Add the helpers to `src/decisions/transfers.py`** (after `xp_5gw_by_player`)

```python
def hit_cost(num_transfers, free_transfers=1):
    """Points cost of `num_transfers` given `free_transfers`, as a non-positive int.

    max(0, num_transfers - free_transfers) transfers cost 4 points each: e.g. 2 transfers
    with 1 FT -> -4. Returns 0 when free transfers cover them.
    """
    return -max(0, num_transfers - free_transfers) * 4


def is_worth_hit(ep_delta, hit_cost):
    """True when the EP gain beats the (absolute) points hit.

    When `hit_cost` is 0 (free), this reduces to `ep_delta > 0`.
    """
    return ep_delta > abs(hit_cost)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_hit_cost_thresholds -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): hit_cost + is_worth_hit calculator"
```

---

### Task 3: `sell_candidates` — below-position-median OR flagged

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sell_candidate_below_median_or_flagged():
    # FWD market xp_5gw = [10, 20, 30, 25] -> median 22.5
    market = [
        _p(1, "FWD", 1, 8.0, "a", 10.0),   # below median -> sell
        _p(2, "FWD", 2, 8.0, "a", 20.0),
        _p(3, "FWD", 3, 8.0, "a", 30.0),   # above median, available -> keep
        _p(4, "FWD", 4, 8.0, "i", 25.0),   # flagged -> sell regardless of xp
    ]
    squad = [market[0], market[2], market[3]]
    sell_ids = {p["player_id"] for p in transfers.sell_candidates(squad, market)}
    assert 1 in sell_ids     # below median
    assert 4 in sell_ids     # flagged status
    assert 3 not in sell_ids # above median and available
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_sell_candidate_below_median_or_flagged -q`
Expected: FAIL — `AttributeError: ... has no attribute 'sell_candidates'`.

- [ ] **Step 3: Add `_median_by_position` + `sell_candidates`**

```python
def _median_by_position(all_players):
    """Median xp_5gw per position across the whole market (not just the squad)."""
    meds = {}
    for pos in POSITIONS:
        vals = [p["xp_5gw"] for p in all_players if p["position"] == pos]
        meds[pos] = median(vals) if vals else 0.0
    return meds


def sell_candidates(squad_players, all_players):
    """Squad players worth selling: xp_5gw below the position's market median, or non-clear status."""
    meds = _median_by_position(all_players)
    return [p for p in squad_players
            if p["status"] != "a" or p["xp_5gw"] < meds.get(p["position"], 0.0)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py::test_sell_candidate_below_median_or_flagged -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): sell_candidates (below-median or flagged)"
```

---

### Task 4: `buy_candidates` — same position, budget, 3-per-club (B11 properties)

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Write the failing tests (targeted unit + two property tests)**

Add the `_pick_valid_squad` / `_random_market_and_squad` helpers (clean versions — see Task 5 Step 1 for the corrected `_pick_valid_squad`) above these tests, then:

```python
def test_buy_respects_budget():
    sell = _p(1, "MID", 1, 7.0, "a", 10.0)
    market = [
        sell,
        _p(2, "MID", 2, 7.5, "a", 20.0),   # 7.5 <= 7.0 + 1.0 -> allowed
        _p(3, "MID", 3, 9.0, "a", 30.0),   # 9.0 > 8.0 -> excluded
    ]
    ids = {p["player_id"] for p in transfers.buy_candidates(sell, market, [sell], bank=1.0)}
    assert 2 in ids
    assert 3 not in ids


def test_buy_respects_3_per_club():
    squad = [
        _p(1, "DEF", 5, 5.0, "a", 10.0),
        _p(2, "DEF", 5, 5.0, "a", 10.0),
        _p(3, "DEF", 5, 5.0, "a", 10.0),   # club 5 already at 3
        _p(4, "DEF", 9, 5.0, "a", 5.0),    # the sell (different club)
    ]
    market = squad + [
        _p(10, "DEF", 5, 5.0, "a", 40.0),  # would be a 4th from club 5
        _p(11, "DEF", 7, 5.0, "a", 35.0),  # club 7 -> fine
    ]
    ids = {p["player_id"] for p in transfers.buy_candidates(squad[3], market, squad, bank=2.0)}
    assert 10 not in ids   # selling a club-9 player does not free a club-5 slot
    assert 11 in ids
    # but selling a club-5 player DOES free a slot: 3 - 1 + 1 = 3 is legal
    ids2 = {p["player_id"] for p in transfers.buy_candidates(squad[0], market, squad, bank=2.0)}
    assert 10 in ids2


def test_buy_respects_budget_property():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        for sell in squad:
            for buy in transfers.buy_candidates(sell, market, squad, bank):
                assert buy["price"] <= sell["price"] + bank + 1e-9
                assert buy["position"] == sell["position"]
                assert buy["status"] == "a"


def test_buy_respects_3_per_club_property():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        squad_ids = {p["player_id"] for p in squad}
        for sell in squad:
            for buy in transfers.buy_candidates(sell, market, squad, bank):
                assert buy["player_id"] not in squad_ids
                new_squad = [p for p in squad if p["player_id"] != sell["player_id"]] + [buy]
                counts = Counter(p["team_id"] for p in new_squad)
                assert max(counts.values()) <= MAX_PER_CLUB
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k buy -q`
Expected: FAIL — `AttributeError: ... has no attribute 'buy_candidates'`.

- [ ] **Step 3: Add `_club_counts` + `buy_candidates`**

```python
def _club_counts(players):
    counts = {}
    for p in players:
        counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
    return counts


def buy_candidates(sell, all_players, squad, bank):
    """Legal replacements for `sell`, ranked by xp_5gw desc.

    A buy must be: not already in the squad, the same position as `sell`, status 'a',
    affordable (price <= sell.price + bank), and keep <= 3 players per club after the swap.
    """
    squad_ids = {p["player_id"] for p in squad}
    counts = _club_counts(squad)
    budget = sell["price"] + bank
    out = []
    for p in all_players:
        if p["player_id"] in squad_ids:
            continue
        if p["position"] != sell["position"] or p["status"] != "a":
            continue
        if p["price"] > budget + _EPS:
            continue
        after = counts.get(p["team_id"], 0) - (1 if sell["team_id"] == p["team_id"] else 0) + 1
        if after > MAX_PER_CLUB:
            continue
        out.append(p)
    out.sort(key=lambda x: x["xp_5gw"], reverse=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k buy -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): buy_candidates with budget + 3-per-club property tests"
```

---

### Task 5: `suggest_transfers` — top-3 pairs + leave-valid-squad property (B11)

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Ensure the clean `_pick_valid_squad` helper is in the test file**

If not already present from Task 4, add (this is the corrected version — note the plain `>= MAX_PER_CLUB` guard, no walrus placeholder):

```python
def _pick_valid_squad(market, rng):
    """Greedily pick a legal 15-man squad (2 GKP, 5 DEF, 5 MID, 3 FWD, <=3/club) from `market`."""
    need = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    chosen, chosen_ids, club = [], set(), {}
    pool = market[:]
    rng.shuffle(pool)
    for pos, n in need.items():
        got = 0
        for p in pool:
            if got == n:
                break
            if p["position"] != pos or p["player_id"] in chosen_ids:
                continue
            if club.get(p["team_id"], 0) >= MAX_PER_CLUB:
                continue
            chosen.append(p)
            chosen_ids.add(p["player_id"])
            club[p["team_id"]] = club.get(p["team_id"], 0) + 1
            got += 1
        assert got == n, f"market too small for {pos}"
    return chosen
```

- [ ] **Step 2: Write the failing tests**

```python
def test_suggest_orders_by_ep_delta_and_caps_at_three():
    # Three sellable FWDs (below market median) each with a clearly better same-club-safe buy.
    squad = [
        _p(1, "FWD", 1, 8.0, "a", 2.0),
        _p(2, "FWD", 2, 8.0, "a", 3.0),
        _p(3, "FWD", 3, 8.0, "a", 4.0),
        _p(4, "FWD", 4, 8.0, "a", 1.0),
    ]
    buys = [
        _p(11, "FWD", 11, 8.0, "a", 30.0),   # delta 28 with sell 1
        _p(12, "FWD", 12, 8.0, "a", 25.0),   # delta 22 with sell 2
        _p(13, "FWD", 13, 8.0, "a", 20.0),   # delta 16 with sell 3
        _p(14, "FWD", 14, 8.0, "a", 10.0),   # delta 9  with sell 4
    ]
    market = squad + buys
    pairs = transfers.suggest_transfers(squad, market, bank=0.0)
    assert len(pairs) == 3                                  # capped at top 3
    deltas = [pr["ep_delta_5gw"] for pr in pairs]
    assert deltas == sorted(deltas, reverse=True)           # descending by delta
    assert pairs[0]["out"]["player_id"] == 1 and pairs[0]["in"]["player_id"] == 11
    assert all(pr["hit_cost"] == 0 for pr in pairs)         # v1 single free transfer


def test_empty_reason_when_no_positive_delta():
    # squad players are the entire market for their position, identical xp, available -> no sells
    squad = [_p(i, "MID", i, 6.0, "a", 10.0) for i in range(1, 4)]
    assert transfers.suggest_transfers(squad, squad, bank=2.0) == []


def test_property_suggestions_leave_valid_squad():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        squad_ids = {p["player_id"] for p in squad}
        before_pos = Counter(p["position"] for p in squad)
        pairs = transfers.suggest_transfers(squad, market, bank)
        assert len(pairs) <= 3
        for pr in pairs:
            sell, buy = pr["out"], pr["in"]
            assert sell["player_id"] in squad_ids
            assert buy["player_id"] not in squad_ids
            assert sell["position"] == buy["position"]        # same-position swap
            assert pr["ep_delta_5gw"] > 0                      # only positive deltas
            new_squad = [p for p in squad if p["player_id"] != sell["player_id"]] + [buy]
            assert len(new_squad) == 15                        # 15-man squad preserved
            assert Counter(p["position"] for p in new_squad) == before_pos
            assert max(Counter(p["team_id"] for p in new_squad).values()) <= MAX_PER_CLUB
            assert bank - (buy["price"] - sell["price"]) >= -1e-9   # within budget
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k "suggest or property_suggestions or empty_reason_when" -q`
Expected: FAIL — `AttributeError: ... has no attribute 'suggest_transfers'`.

- [ ] **Step 4: Add `suggest_transfers`**

```python
def suggest_transfers(squad_players, all_players, bank, top_n=3):
    """Top `top_n` sell->buy pairs by EP delta over the 5-GW horizon.

    For each sell candidate, take its best legal buy; keep only positive EP deltas; sort all
    pairs by delta desc; return the top `top_n`. v1 assumes a single free transfer, so every
    suggested transfer is free (hit_cost 0). `out`/`in` carry the full player dicts so callers
    can inspect/apply the swap; the reader projects them to the API shape.
    """
    pairs = []
    for sell in sell_candidates(squad_players, all_players):
        buys = buy_candidates(sell, all_players, squad_players, bank)
        if not buys:
            continue
        buy = buys[0]
        ep_delta = buy["xp_5gw"] - sell["xp_5gw"]
        if ep_delta <= 0:
            continue
        pairs.append({"out": sell, "in": buy,
                      "ep_delta_5gw": round(ep_delta, 2), "hit_cost": 0})
    pairs.sort(key=lambda pr: pr["ep_delta_5gw"], reverse=True)
    return pairs[:top_n]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k "suggest or property_suggestions or empty_reason_when" -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): suggest_transfers top-3 + leave-valid-squad property"
```

---

### Task 6: `get_transfer_suggestions(conn)` — the `/api/transfers` reader

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 1: Write the failing tests (use the `db` fixture from conftest + `_seed_db`)**

Add the `_seed_db` helper (full version in the scaffolding block at the top of this plan), then:

```python
def test_get_transfer_suggestions_integration(db):
    players = [
        {"id": 1, "web_name": "Out",     "position": "FWD", "team_id": 1, "price": 8.0,  "status": "a", "xp5": 5.0},
        {"id": 2, "web_name": "KeepMid", "position": "MID", "team_id": 2, "price": 7.0,  "status": "a", "xp5": 30.0},
        {"id": 3, "web_name": "KeepDef", "position": "DEF", "team_id": 3, "price": 5.0,  "status": "a", "xp5": 25.0},
        {"id": 4, "web_name": "In",      "position": "FWD", "team_id": 4, "price": 8.0,  "status": "a", "xp5": 25.0},
        {"id": 5, "web_name": "PremFwd", "position": "FWD", "team_id": 5, "price": 12.0, "status": "a", "xp5": 40.0},
    ]
    _seed_db(db, players, squad_ids=[1, 2, 3], bank=1.0)
    out = transfers.get_transfer_suggestions(db)

    assert out["empty_reason"] is None
    assert 1 <= len(out["suggestions"]) <= 3
    s = out["suggestions"][0]
    assert s["out"]["player_id"] == 1 and s["in"]["player_id"] == 4   # p5 unaffordable (12 > 8+1)
    assert s["in"]["price"] <= s["out"]["price"] + 1.0 + 1e-9         # within budget
    assert s["hit_cost"] == 0
    assert s["confidence"] is None
    # exact contract shape
    assert set(s.keys()) == {"out", "in", "ep_delta_5gw", "hit_cost", "confidence"}
    assert set(s["out"].keys()) == {"player_id", "web_name", "price"}
    assert set(s["in"].keys()) == {"player_id", "web_name", "price"}


def test_get_transfer_suggestions_empty_reason(db):
    # squad players are the only players in their position, all available -> no sells
    players = [
        {"id": 1, "web_name": "A", "position": "MID", "team_id": 1, "price": 6.0, "status": "a", "xp5": 10.0},
        {"id": 2, "web_name": "B", "position": "MID", "team_id": 2, "price": 6.0, "status": "a", "xp5": 10.0},
        {"id": 3, "web_name": "C", "position": "MID", "team_id": 3, "price": 6.0, "status": "a", "xp5": 10.0},
    ]
    _seed_db(db, players, squad_ids=[1, 2, 3], bank=2.0)
    out = transfers.get_transfer_suggestions(db)
    assert out["suggestions"] == []
    assert out["empty_reason"] == "No transfers worth making this GW."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k get_transfer -q`
Expected: FAIL — `AttributeError: ... has no attribute 'get_transfer_suggestions'`.

- [ ] **Step 3: Add the reader (+ small private helpers) to `src/decisions/transfers.py`**

```python
def _next_gw(conn):
    row = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row else None


def _latest_squad(conn):
    """Latest my_team snapshot -> (element_ids, bank), or None when there is no snapshot."""
    row = conn.execute("SELECT picks_json, bank FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if row is None:
        return None
    ids = [pick["element"] for pick in json.loads(row["picks_json"])]
    bank = row["bank"] if row["bank"] is not None else 0.0
    return ids, bank


def get_transfer_suggestions(conn):
    """Reader: build the /api/transfers payload from current DB state (Analytics output + squad).

    Returns {"suggestions": [...up to 3...], "empty_reason": str | None}. `confidence` is out of
    scope this slice and is returned as null. No persistence, no execution (Phase 1).
    """
    next_gw = _next_gw(conn)
    squad = _latest_squad(conn)
    if next_gw is None or squad is None:
        return {"suggestions": [], "empty_reason": EMPTY_REASON}
    squad_ids, bank = squad

    xp_rows = conn.execute(
        "SELECT player_id, gw, xp FROM xp WHERE model_version='v1' AND gw BETWEEN ? AND ?",
        (next_gw, next_gw + HORIZON - 1)).fetchall()
    xp5 = xp_5gw_by_player(xp_rows, next_gw)

    all_players = [
        {"player_id": r["id"], "web_name": r["web_name"], "position": r["position"],
         "team_id": r["team_id"], "price": r["price"], "status": r["status"],
         "xp_5gw": xp5.get(r["id"], 0.0)}
        for r in conn.execute(
            "SELECT id, web_name, position, team_id, price, status FROM players")
    ]
    squad_set = set(squad_ids)
    squad_players = [p for p in all_players if p["player_id"] in squad_set]

    pairs = suggest_transfers(squad_players, all_players, bank)
    suggestions = [
        {"out": {k: pr["out"][k] for k in ("player_id", "web_name", "price")},
         "in":  {k: pr["in"][k] for k in ("player_id", "web_name", "price")},
         "ep_delta_5gw": pr["ep_delta_5gw"], "hit_cost": pr["hit_cost"], "confidence": None}
        for pr in pairs
    ]
    return {"suggestions": suggestions, "empty_reason": None if suggestions else EMPTY_REASON}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -k get_transfer -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat(transfers): get_transfer_suggestions /api/transfers reader"
```

---

### Task 7: Full-suite verification + PR

**Files:** none (verification only).

- [ ] **Step 1: Run the whole transfer suite**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest tests/test_transfers.py -v`
Expected: all transfer tests PASS (≈11 tests).

- [ ] **Step 2: Run the entire project suite (no regressions)**

Run: `PYTHONPATH=$PWD /Users/falah/Work/fpl-autopilot/.venv/bin/python -m pytest -q`
Expected: all green (baseline 64 + the new transfer tests).

- [ ] **Step 3: Confirm isolation — only the two intended files changed**

Run: `git diff --name-only main...HEAD`
Expected: `docs/superpowers/plans/2026-05-22-transfer-engine.md`, `src/decisions/transfers.py`, `tests/test_transfers.py` — and **nothing else** (no `pyproject.toml`, `src/decisions/__init__.py`, or `docs/decision-engine.md`).

- [ ] **Step 4: Optional live smoke check (DoD §2)**

If the live DB is populated (`.venv/bin/fpl-autopilot refresh`, then FDR + xP compute), call `get_transfer_suggestions(conn)` and eyeball that each suggestion is a same-position swap within budget. Skip if the live DB is not available — the integration test already proves the shape.

- [ ] **Step 5: Push and open the PR against `main`**

```bash
git push -u origin feat/transfer-engine
gh pr create --base main --title "feat: transfer engine v1 (suggest-only)" \
  --body "Decision-Layer transfer engine. Pure core (xp_5gw_by_player, sell_candidates, buy_candidates, hit_cost/is_worth_hit, suggest_transfers) + get_transfer_suggestions(conn) reader returning the /api/transfers shape. B11 property tests cover budget, 3-per-club, and resulting-squad validity. v1 substitutions per decision-engine.md v0.5: no form_adjusted_delta, sell price = current price, FT assumed 1 (hit 0). confidence returned null this slice."
```

---

## Self-Review

**Spec coverage (§ → task):**
- §4 `xP_5gw` (sum next-5, missing→0) → Task 1. ✅
- §4 `sell_candidates` (below position-market median OR status≠a) → Task 3. ✅
- §4 `buy_candidates` (same pos, status a, budget, 3-per-club, ranked) → Task 4. ✅
- §4 `suggest_transfers` (best buy per sell, positive delta, sort desc, top 3, hit 0) → Task 5. ✅
- §4 `hit_cost` / `is_worth_hit` thresholds → Task 2. ✅
- §4 `get_transfer_suggestions` reader + exact contract shape incl. `confidence: null` → Task 6. ✅
- §7 every named test → Tasks 1–6 (xp_5gw_sums_five_gws, sell_candidate_below_median_or_flagged, buy_respects_budget [unit+property], buy_respects_3_per_club [unit+property], suggestion_leaves_valid_squad [property], hit_cost_thresholds, empty_reason_when_no_positive_delta, get_transfer_suggestions_integration). ✅
- §8 DoD: pytest green + property tests (Task 7 Step 1–2), reader payload valid (Task 6 + Task 7 Step 4), decision-engine.md already documents v1 (no change — verified in Task 7 Step 3). ✅
- §3 out-of-scope (form_adjusted_delta, real sell price/FT, multi-transfer −4/−8 path beyond the tested helper, FastAPI endpoint, chip recommender): none implemented. ✅

**Placeholder scan:** The only intentional placeholder is the flagged `_pick_valid_squad` line in the top scaffolding block; Task 4 Step 1 and Task 5 Step 1 both instruct using the clean version, and Task 5 Step 1 prints it in full. No "TBD"/"add error handling"/"similar to" placeholders elsewhere — every code step shows complete code.

**Type/name consistency:** Player-dict keys (`player_id`, `web_name`, `position`, `team_id`, `price`, `status`, `xp_5gw`) are identical across `_p`, the pure functions, and the reader's `all_players`. Constants `HORIZON`, `MAX_PER_CLUB`, `EMPTY_REASON`, `_EPS` defined once (Task 1) and reused. `suggest_transfers` returns `out`/`in` full dicts + `ep_delta_5gw`/`hit_cost`; the reader projects to `player_id`/`web_name`/`price` + adds `confidence: None` — matching `docs/api-contract.md` exactly. `hit_cost` is both a function and a pair key; they never collide (different scopes).
