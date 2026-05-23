# Confidence Scoring Implementation Plan — Phase 2.3a

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the documented confidence formula (`src/decisions/confidence.py`) and surface a real integer `confidence` on captain and transfer decisions (currently `None`).

**Architecture:** A pure `confidence.score(...)` plus a `hours_since_refresh` cache reader. Wire it into `get_captain_picks` (new `confidence` field) and `get_transfer_suggestions` (fill the existing per-suggestion `confidence`). Pure decision-layer work — no auth/execution/network. Document the status mapping in `decision-engine.md` (B4) and the new captain field in `api-contract.md`.

**Tech Stack:** Python 3.11+, `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-confidence-scoring-design.md`

**Baseline:** suite is green at 180 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/decisions/confidence.py` | Create | pure `score()`, `hours_since_refresh()`, status-penalty map |
| `src/decisions/captain.py` | Modify | `get_captain_picks` returns `confidence` |
| `src/decisions/transfers.py` | Modify | `get_transfer_suggestions` fills per-suggestion `confidence` |
| `docs/decision-engine.md` | Modify | pin status-code→penalty mapping + staleness anchor (changelog) |
| `docs/api-contract.md` | Modify | note `/captain` now returns `confidence` |
| `tests/test_confidence.py` | Create | `score` + `hours_since_refresh` tests |
| `tests/test_captain.py` | Modify | integration test asserts the new `confidence` field |
| `tests/test_transfers.py` | Modify | suggestion test asserts an int `confidence` |

---

### Task 1: `confidence.py` (pure score + cache reader)

**Files:** Create `src/decisions/confidence.py`, `tests/test_confidence.py`; Modify `docs/decision-engine.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_confidence.py`:

```python
from datetime import datetime, timedelta, timezone
from src.decisions import confidence


def test_score_all_clear():
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=3.0) == 75


def test_score_staleness_tiers():
    assert confidence.score(staleness_hours=12.0, statuses=["a"], gap=3.0) == 65   # 6-24h: -10
    assert confidence.score(staleness_hours=30.0, statuses=["a"], gap=3.0) == 45   # >24h: -30
    assert confidence.score(staleness_hours=None, statuses=["a"], gap=3.0) == 45   # unknown: -30


def test_score_status_tiers():
    assert confidence.score(staleness_hours=0.0, statuses=["d"], gap=3.0) == 60          # -15
    assert confidence.score(staleness_hours=0.0, statuses=["i"], gap=3.0) == 45          # -30
    assert confidence.score(staleness_hours=0.0, statuses=["a", "d"], gap=3.0) == 60     # worst-of
    assert confidence.score(staleness_hours=0.0, statuses=["x"], gap=3.0) == 45          # unknown -30


def test_score_gap_tiers():
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=1.5) == 70   # 1-2: -5
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=0.7) == 60   # .5-1: -15
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=0.2) == 50   # <.5: -25
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=None) == 75  # no alt: 0


def test_score_clamps_at_zero():
    assert confidence.score(staleness_hours=30.0, statuses=["i"], gap=0.1) == 0    # 75-30-30-25 -> clamp 0
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=10.0) == 75   # no penalties


def test_hours_since_refresh(db):
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO cache_meta (resource, last_fetched_utc) VALUES (?, ?)",
               ("bootstrap-static", (now - timedelta(hours=12)).isoformat()))
    db.commit()
    h = confidence.hours_since_refresh(db)
    assert 11.5 < h < 12.5


def test_hours_since_refresh_missing_row(db):
    assert confidence.hours_since_refresh(db) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_confidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.decisions.confidence'`.

- [ ] **Step 3: Implement** — create `src/decisions/confidence.py`:

```python
from datetime import datetime, timezone

_STATUS_PENALTY = {"a": 0, "d": 15}  # default 30 for i/s/u/n/unknown/empty


def _status_penalty(status):
    return _STATUS_PENALTY.get(status, 30)


def score(*, staleness_hours, statuses, gap):
    c = 75
    if staleness_hours is None or staleness_hours > 24:
        c -= 30
    elif staleness_hours > 6:
        c -= 10
    c -= max((_status_penalty(s) for s in statuses), default=0)
    if gap is None or gap > 2:
        c -= 0
    elif gap >= 1:
        c -= 5
    elif gap >= 0.5:
        c -= 15
    else:
        c -= 25
    return max(0, min(100, c))


def hours_since_refresh(conn, resource="bootstrap-static"):
    row = conn.execute("SELECT last_fetched_utc FROM cache_meta WHERE resource=?",
                       (resource,)).fetchone()
    if row is None:
        return None
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(row["last_fetched_utc"])
    return delta.total_seconds() / 3600.0
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_confidence.py -v`
Expected: 7 passed.

- [ ] **Step 5: Document the mapping in `decision-engine.md`** (B4) — under the "Confidence score" section, add a sub-note pinning the implementation detail:

```markdown
**Implementation detail (v0.7, 2026-05-23):** status-uncertainty maps the FPL `status` code —
`a`→0, `d`→+15, and `i`/`s`/`u`/`n`/unknown→+30 — taking the worst among the players involved
in the decision (captain + vice for captaincy; in + out for a transfer). Staleness is measured
from the `bootstrap-static` cache timestamp. Alternative-proximity uses the gap between the top
two options (captain: top-2 xP; transfer: a suggestion's EP delta vs the next suggestion's).
```
Add a matching one-line entry to the decision-engine changelog.

- [ ] **Step 6: Commit**

```bash
git add src/decisions/confidence.py tests/test_confidence.py docs/decision-engine.md
git commit -m "feat: confidence scoring (score + cache staleness); document status mapping"
```

---

### Task 2: wire confidence into `get_captain_picks`

**Files:** Modify `src/decisions/captain.py`, `tests/test_captain.py`, `docs/api-contract.md`

- [ ] **Step 1: Update the integration test to expect `confidence`** — in `tests/test_captain.py`:

Change the keys assertion (currently `assert set(result.keys()) == {"picks", "vice_player_id"}`) to:
```python
    assert set(result.keys()) == {"picks", "vice_player_id", "confidence"}
    assert isinstance(result["confidence"], int)
    assert 0 <= result["confidence"] <= 100
```
Change the empty-GW assertion (currently `assert captain.get_captain_picks(db) == {"picks": [], "vice_player_id": None}`) to:
```python
    assert captain.get_captain_picks(db) == {"picks": [], "vice_player_id": None, "confidence": None}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_captain.py -v`
Expected: FAIL — the keys assertion fails (`confidence` missing from the result).

- [ ] **Step 3: Implement** — in `src/decisions/captain.py`:

Add the import after the existing `import json`:
```python
from src.decisions import confidence as confidence_mod
```
Replace the body of `get_captain_picks` (from the `gw is None` early return through the final `return`) with:
```python
    gw = _next_gw(conn)
    if gw is None:
        return {"picks": [], "vice_player_id": None, "confidence": None}
    candidates = [c for c in (_build_candidate(conn, pid, gw)
                              for pid in _squad_element_ids(conn)) if c is not None]
    picks = rank_captains(candidates)
    if not picks:
        return {"picks": [], "vice_player_id": None, "confidence": None}
    vice = picks[1]["player_id"] if len(picks) > 1 else None
    ids = [picks[0]["player_id"]] + ([vice] if vice is not None else [])
    rows = conn.execute(
        f"SELECT status FROM players WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    statuses = [r["status"] for r in rows]
    gap = picks[0]["xp"] - picks[1]["xp"] if len(picks) > 1 else None
    conf = confidence_mod.score(staleness_hours=confidence_mod.hours_since_refresh(conn),
                                statuses=statuses, gap=gap)
    return {"picks": picks, "vice_player_id": vice, "confidence": conf}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_captain.py -v`
Expected: all captain tests pass (the integration test now sees `confidence`).

- [ ] **Step 5: Update `api-contract.md`** — in the `/captain` payload description, add that the response now includes a top-level `"confidence"` (integer 0–100, or `null` when there is no pick).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 186 passed (180 + 6 from Task 1; Task 2 modifies existing tests, no net new).

- [ ] **Step 7: Commit**

```bash
git add src/decisions/captain.py tests/test_captain.py docs/api-contract.md
git commit -m "feat: get_captain_picks returns confidence"
```

---

### Task 3: wire confidence into `get_transfer_suggestions`

**Files:** Modify `src/decisions/transfers.py`, `tests/test_transfers.py`

- [ ] **Step 1: Update the suggestion test to expect an int `confidence`** — in `tests/test_transfers.py`, change `assert s["confidence"] is None` to:
```python
    assert isinstance(s["confidence"], int) and 0 <= s["confidence"] <= 100
```
(Leave the `set(s.keys()) == {"out", "in", "ep_delta_5gw", "hit_cost", "confidence"}` assertion as-is — the key is unchanged.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_transfers.py -v`
Expected: FAIL — `s["confidence"]` is `None`, not an int.

- [ ] **Step 3: Implement** — in `src/decisions/transfers.py`:

Add the import near the top (after the existing imports):
```python
from src.decisions import confidence as confidence_mod
```
Replace the suggestions-building block (the `suggestions = [ ... for pr in pairs ]` comprehension and the `return`) with:
```python
    pairs = suggest_transfers(squad_players, all_players, bank)
    staleness = confidence_mod.hours_since_refresh(conn)
    suggestions = []
    for i, pr in enumerate(pairs):
        gap = pr["ep_delta_5gw"] - pairs[i + 1]["ep_delta_5gw"] if i + 1 < len(pairs) else None
        conf = confidence_mod.score(staleness_hours=staleness,
                                    statuses=[pr["in"]["status"], pr["out"]["status"]], gap=gap)
        suggestions.append(
            {"out": {k: pr["out"][k] for k in ("player_id", "web_name", "price")},
             "in":  {k: pr["in"][k] for k in ("player_id", "web_name", "price")},
             "ep_delta_5gw": pr["ep_delta_5gw"], "hit_cost": pr["hit_cost"], "confidence": conf})
    return {"suggestions": suggestions, "empty_reason": None if suggestions else EMPTY_REASON}
```
(`pr["in"]`/`pr["out"]` are the full player dicts from `suggest_transfers`; they carry `status` because `get_transfer_suggestions` selects it: `SELECT id, web_name, position, team_id, price, status FROM players`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_transfers.py -v`
Expected: all transfer tests pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 186 passed.

- [ ] **Step 6: Commit**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "feat: get_transfer_suggestions fills per-suggestion confidence"
```

---

## Self-Review

**Spec coverage:**
- Formula (75 − staleness − status − proximity, clamped) → Task 1 `confidence.score`.
- Staleness from `bootstrap-static` cache → Task 1 `hours_since_refresh`.
- Status mapping `a`/`d`/rest, worst-of → Task 1 `_status_penalty` + tests; documented in `decision-engine.md` (Task 1 step 5).
- Captain confidence (captain+vice status, top-2 gap, field added) → Task 2.
- Transfer per-suggestion confidence (in/out status, gap-to-next) → Task 3.
- Doc updates → Task 1 (`decision-engine.md`, B4) + Task 2 (`api-contract.md`).
- Edge cases (no cache row, single pick/suggestion, unknown status, clamp) → Task 1 tests + reader `gap=None`/empty handling.

**Placeholder scan:** none. Every step is complete with full code, commands, and expected counts (186 after Task 1; unchanged through Tasks 2–3 since they modify existing reader tests rather than adding new ones). The `test_score_clamps_at_zero` expectation is `75−30−30−25 → clamp 0`.

**Type consistency:** `confidence.score(*, staleness_hours, statuses, gap)` and `hours_since_refresh(conn, resource=...)` defined in Task 1, imported as `confidence_mod` and called identically in Tasks 2 and 3. The captain return gains `confidence`; the transfer suggestion's existing `confidence` field is filled (key unchanged). Captain inputs (`picks[0]["xp"]`, `picks[0]["player_id"]`, `vice_player_id`) and transfer inputs (`pr["in"]["status"]`, `pr["ep_delta_5gw"]`) match the readers' actual data.
```
