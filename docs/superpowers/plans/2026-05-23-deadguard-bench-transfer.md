# Deadguard Bench-Order + Transfer-if-Flagged Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the deadguard H-30 trigger (2.5a captain/vice) to also optimize the bench order (so FPL's native auto-sub picks the best replacement) and make a single guarded free transfer to replace a flagged-out squad player.

**Architecture:** A new `decisions/bench.py` ranks the three outfield bench slots by xP; `build_lineup_payload`/`run_lineup` gain an opt-in `optimize_bench` so captain/vice + bench order go in one atomic FPL write; `deadguard._run_trigger` adds that flag and a best-effort, fully-guarded transfer-if-flagged step (reusing the existing transfer engine). Only captain/vice + bench reorder + at most one free transfer — no chips/hits/multi/formation changes (B8).

**Tech Stack:** Python 3.11+, sqlite, `pytest` + `monkeypatch`. Run tests with `.venv/bin/pytest`.

**Branch:** `feat/deadguard-bench-transfer` (already created; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-05-23-deadguard-bench-transfer-design.md` · **Product doc:** `docs/deadguard.md`

**Conventions (binding):**
- TDD; never `git add -A` (explicit paths). Baseline before this plan: **309 passing**.
- B8: ≤1 transfer, free only, targeted at the flagged player; bench reorder only touches positions 13/14/15; no chips/hits/multi.
- The agent never runs live (R3); tests use fakes/`monkeypatch`, no network.
- `db` is the in-memory fixture in `tests/conftest.py`.

---

### Task 1: `decisions/bench.py` — `rank_bench`

**Files:**
- Create: `src/decisions/bench.py`
- Test: `tests/test_bench.py` (create)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_bench.py`:

```python
from src.decisions import bench


def _seed(db):
    db.execute("INSERT INTO gameweeks (id, finished) VALUES (30, 0)")
    for e in (13, 14, 15):
        db.execute("INSERT INTO players (id, web_name) VALUES (?, ?)", (e, f"P{e}"))
    db.commit()


def _picks():
    return [{"element": e, "position": e} for e in range(1, 16)]


def test_rank_bench_orders_by_xp(db):
    _seed(db)
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (13,30,'v1',3.0,80)")
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (14,30,'v1',5.0,80)")
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (15,30,'v1',1.0,80)")
    db.commit()
    assert bench.rank_bench(db, _picks()) == [14, 13, 15]


def test_rank_bench_missing_xp_sorts_last(db):
    _seed(db)
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (13,30,'v1',2.0,80)")
    db.commit()
    out = bench.rank_bench(db, _picks())
    assert out[0] == 13 and set(out) == {13, 14, 15}


def test_rank_bench_only_bench_positions(db):
    _seed(db)
    out = bench.rank_bench(db, _picks())
    assert set(out) == {13, 14, 15}      # positions 1-12 ignored
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_bench.py -q` → `ModuleNotFoundError: No module named 'src.decisions.bench'`.

- [ ] **Step 3: Implement.** Create `src/decisions/bench.py`:

```python
from src.analytics.xp import MODEL_VERSION
from src.decisions.transfers import _next_gw


def rank_bench(conn, current_picks):
    """Element ids currently at bench positions 13/14/15, ordered by next-GW xP (desc),
    xMinutes as the rotation-risk tiebreaker. Missing xP -> 0 (sorts last). The sub-GK
    (position 12) is fixed and not reordered."""
    gw = _next_gw(conn)
    bench = [p["element"] for p in current_picks if p["position"] in (13, 14, 15)]

    def _key(element):
        if gw is None:
            return (0.0, 0.0)
        row = conn.execute(
            "SELECT xp, xminutes FROM xp WHERE player_id=? AND gw=? AND model_version=?",
            (element, gw, MODEL_VERSION)).fetchone()
        return (row["xp"], row["xminutes"]) if row else (0.0, 0.0)

    return sorted(bench, key=_key, reverse=True)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_bench.py -q` → 3 passed.

- [ ] **Step 5: Commit.**
```bash
git add src/decisions/bench.py tests/test_bench.py
git commit -m "feat: decisions.bench.rank_bench (xP-ordered outfield bench) (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `executor.build_lineup_payload` — optional `bench_order`

**Files:**
- Modify: `src/execution/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_executor.py`:

```python
def _full_picks():
    return [{"element": e, "position": e, "selling_price": 50,
             "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]


def test_build_lineup_payload_reorders_bench():
    from src.execution import executor
    payload = executor.build_lineup_payload(_full_picks(), captain_id=1, vice_id=2,
                                            bench_order=[15, 13, 14])
    pos = {p["element"]: p["position"] for p in payload["picks"]}
    assert pos[15] == 13 and pos[13] == 14 and pos[14] == 15   # reassigned in given order
    assert pos[1] == 1 and pos[11] == 11 and pos[12] == 12     # starters + sub-GK unchanged
    caps = {p["element"]: p["is_captain"] for p in payload["picks"]}
    vices = {p["element"]: p["is_vice_captain"] for p in payload["picks"]}
    assert caps[1] is True and vices[2] is True


def test_build_lineup_payload_bad_bench_order_raises():
    import pytest
    from src.execution import executor
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_full_picks(), 1, 2, bench_order=[13, 14, 99])  # 99 not on bench


def test_build_lineup_payload_none_unchanged():
    from src.execution import executor
    payload = executor.build_lineup_payload(_full_picks(), 1, 2)
    pos = {p["element"]: p["position"] for p in payload["picks"]}
    assert pos[13] == 13 and pos[14] == 14 and pos[15] == 15
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_executor.py -q` → `TypeError: build_lineup_payload() got an unexpected keyword argument 'bench_order'`.

- [ ] **Step 3: Implement.** In `src/execution/executor.py`, the current function is:
```python
def build_lineup_payload(current_picks, captain_id, vice_id):
    if captain_id == vice_id:
        raise ExecutorError("captain and vice must be different players")
    elements = {p["element"] for p in current_picks}
    if captain_id not in elements:
        raise ExecutorError(f"captain {captain_id} not in current squad")
    if vice_id not in elements:
        raise ExecutorError(f"vice {vice_id} not in current squad")
    picks = [
        {"element": p["element"], "position": p["position"],
         "is_captain": p["element"] == captain_id,
         "is_vice_captain": p["element"] == vice_id}
        for p in current_picks
    ]
    return {"chip": None, "picks": picks}
```
Replace it with (adds the optional `bench_order` + a position override map):
```python
def build_lineup_payload(current_picks, captain_id, vice_id, bench_order=None):
    if captain_id == vice_id:
        raise ExecutorError("captain and vice must be different players")
    elements = {p["element"] for p in current_picks}
    if captain_id not in elements:
        raise ExecutorError(f"captain {captain_id} not in current squad")
    if vice_id not in elements:
        raise ExecutorError(f"vice {vice_id} not in current squad")
    pos_override = {}
    if bench_order is not None:
        current_bench = {p["element"] for p in current_picks if p["position"] in (13, 14, 15)}
        if set(bench_order) != current_bench:
            raise ExecutorError("bench_order must be exactly the current bench (positions 13-15)")
        pos_override = {element: 13 + i for i, element in enumerate(bench_order)}
    picks = [
        {"element": p["element"], "position": pos_override.get(p["element"], p["position"]),
         "is_captain": p["element"] == captain_id,
         "is_vice_captain": p["element"] == vice_id}
        for p in current_picks
    ]
    return {"chip": None, "picks": picks}
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_executor.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/execution/executor.py tests/test_executor.py
git commit -m "feat: build_lineup_payload optional bench_order reorder (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `run_lineup` — `optimize_bench` flag

**Files:**
- Modify: `src/execution/lineup.py`
- Test: `tests/test_lineup.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_lineup.py`:

```python
class _Resp:
    def __init__(self, payload=None, status=200):
        self._p = payload if payload is not None else {}
        self.status_code = status

    def json(self):
        return self._p


class _Sess:
    def __init__(self):
        self.posted = {}

    def get(self, url, timeout=None):
        return _Resp({"picks": [{"element": e, "position": e, "selling_price": 50,
                                 "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp({})


def _ranker(conn):
    return {"picks": [{"player_id": 1, "web_name": "C"}, {"player_id": 2, "web_name": "V"}],
            "vice_player_id": 2, "confidence": 80}


def test_run_lineup_optimize_bench_reorders(db, monkeypatch):
    from src.execution import lineup as lineup_mod
    from src.decisions import bench as bench_mod
    monkeypatch.setattr(bench_mod, "rank_bench", lambda conn, picks: [15, 14, 13])
    sess = _Sess()
    lineup_mod.run_lineup(db, b"k", live=True, confirm_fn=lambda d: True,
                          session=sess, ranker=_ranker, optimize_bench=True)
    pos = {p["element"]: p["position"] for p in sess.posted["json"]["picks"]}
    assert pos[15] == 13 and pos[14] == 14 and pos[13] == 15


def test_run_lineup_default_does_not_reorder(db, monkeypatch):
    from src.execution import lineup as lineup_mod
    from src.decisions import bench as bench_mod
    called = []
    monkeypatch.setattr(bench_mod, "rank_bench", lambda conn, picks: called.append(1) or [15, 14, 13])
    sess = _Sess()
    lineup_mod.run_lineup(db, b"k", live=True, confirm_fn=lambda d: True,
                          session=sess, ranker=_ranker)   # optimize_bench defaults False
    pos = {p["element"]: p["position"] for p in sess.posted["json"]["picks"]}
    assert pos[13] == 13 and pos[14] == 14 and pos[15] == 15
    assert called == []                                   # rank_bench not invoked
```

(`run_lineup` reads `config.team_id()` for the URL — `config.yaml` provides it; the fake session ignores the URL. `apply_lineup` POSTs the payload via `session.post(url, json=payload)`, which `_Sess` captures.)

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_lineup.py -q` → `test_run_lineup_optimize_bench_reorders` fails (`TypeError: run_lineup() got an unexpected keyword argument 'optimize_bench'`).

- [ ] **Step 3: Implement.** In `src/execution/lineup.py`, add the import near the top (with the other `from ..` imports):
```python
from ..decisions import bench as bench_mod
```
Then change `run_lineup`'s signature and the payload line. The current head of the function is:
```python
def run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)
    caps = (ranker or captain_mod.get_captain_picks)(conn)
    if not caps["picks"]:
        raise executor.ExecutorError("no captain pick available (no data?)")
    captain_id = caps["picks"][0]["player_id"]
    vice_id = caps["vice_player_id"]
    payload = executor.build_lineup_payload(current, captain_id, vice_id)
```
Change to:
```python
def run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None, optimize_bench=False):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)
    caps = (ranker or captain_mod.get_captain_picks)(conn)
    if not caps["picks"]:
        raise executor.ExecutorError("no captain pick available (no data?)")
    captain_id = caps["picks"][0]["player_id"]
    vice_id = caps["vice_player_id"]
    bench_order = bench_mod.rank_bench(conn, current) if optimize_bench else None
    payload = executor.build_lineup_payload(current, captain_id, vice_id, bench_order=bench_order)
```
(Leave the rest of `run_lineup` — diff, confirm gate, `apply_lineup`, logging — unchanged.)

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_lineup.py -q` → all pass (existing run_lineup tests unaffected — `optimize_bench` defaults False).

- [ ] **Step 5: Commit.**
```bash
git add src/execution/lineup.py tests/test_lineup.py
git commit -m "feat: run_lineup optimize_bench flag (captain/vice + bench in one write) (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Deadguard scope config accessors

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_config.py`:

```python
def test_deadguard_scope_accessors():
    cfg = {"deadguard": {"scope": {"transfer_if_flagged": False, "min_ep_delta_for_transfer": 4.0,
                                   "confidence_floor": 80}}}
    assert config.deadguard_transfer_if_flagged(cfg) is False
    assert config.deadguard_min_ep_delta(cfg) == 4.0
    assert config.deadguard_confidence_floor(cfg) == 80
    # defaults when the block/keys are absent (explicit empty dict must NOT fall back to config.yaml)
    assert config.deadguard_transfer_if_flagged({}) is True
    assert config.deadguard_min_ep_delta({}) == 3.0
    assert config.deadguard_confidence_floor({}) == 75
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_config.py::test_deadguard_scope_accessors -q` → `AttributeError`.

- [ ] **Step 3: Implement.** Append to `src/config.py` (uses the `cfg is not None` pattern — an explicit `{}` must use the empty dict, not fall through to `config.yaml`, which ships these enabled):

```python
def _deadguard_scope(cfg):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("scope", {})


def deadguard_transfer_if_flagged(cfg=None):
    return bool(_deadguard_scope(cfg).get("transfer_if_flagged", True))


def deadguard_min_ep_delta(cfg=None):
    return _deadguard_scope(cfg).get("min_ep_delta_for_transfer", 3.0)


def deadguard_confidence_floor(cfg=None):
    return _deadguard_scope(cfg).get("confidence_floor", 75)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_config.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/config.py tests/test_config.py
git commit -m "feat: deadguard.scope config accessors (transfer_if_flagged/min_ep/conf) (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `deadguard._pick_flagged_transfer` + `_player_status`

**Files:**
- Modify: `src/interface/deadguard.py`
- Test: `tests/test_deadguard.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_deadguard.py`:

```python
_SUGG = {"suggestions": [{
    "out": {"player_id": 7, "web_name": "Out"}, "in": {"player_id": 99, "web_name": "In"},
    "ep_delta_5gw": 5.0, "hit_cost": 0, "confidence": 80}], "empty_reason": None}


def _seed_player_status(db, pid, status):
    db.execute("INSERT INTO players (id, web_name, status) VALUES (?, ?, ?)", (pid, f"P{pid}", status))
    db.commit()


def test_pick_flagged_transfer_returns_rank_when_qualifying(db, monkeypatch):
    _seed_player_status(db, 7, "i")   # flagged out
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    assert deadguard._pick_flagged_transfer(db, _CFG) == 1


def test_pick_flagged_transfer_none_when_out_available(db, monkeypatch):
    _seed_player_status(db, 7, "a")   # not flagged
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_on_hit(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "hit_cost": -4}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_below_threshold(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "ep_delta_5gw": 2.0}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_low_confidence(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "confidence": 50}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_when_disabled(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    cfg = {"deadguard": {"scope": {"transfer_if_flagged": False}}}
    assert deadguard._pick_flagged_transfer(db, cfg) is None
```

(`_CFG` already exists in `tests/test_deadguard.py` from 2.5a as `{"deadguard": {"enabled": True, ...}}` — its `scope` is absent, so the accessors use defaults: transfer_if_flagged True, min_ep 3.0, conf 75.)

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `AttributeError: ... has no attribute '_pick_flagged_transfer'` (and `deadguard.transfers` doesn't exist yet).

- [ ] **Step 3: Implement.** In `src/interface/deadguard.py`, add this import (with the existing `from src.decisions import captain` etc.):
```python
from src.decisions import transfers
```
Then append:
```python
def _player_status(conn, player_id):
    row = conn.execute("SELECT status FROM players WHERE id=?", (player_id,)).fetchone()
    return row["status"] if row else None


def _pick_flagged_transfer(conn, cfg):
    """1-based rank of the first transfer suggestion that replaces a FLAGGED squad player with a
    free, high-EP upgrade, or None. Guards (all required): OUT status not in ('a','d'); hit_cost>=0
    (free); ep_delta_5gw >= min_ep; confidence >= floor."""
    if not config.deadguard_transfer_if_flagged(cfg):
        return None
    min_ep = config.deadguard_min_ep_delta(cfg)
    floor = config.deadguard_confidence_floor(cfg)
    sugg = transfers.get_transfer_suggestions(conn)
    for i, s in enumerate(sugg["suggestions"], start=1):
        if (_player_status(conn, s["out"]["player_id"]) not in ("a", "d")
                and s["hit_cost"] >= 0 and s["ep_delta_5gw"] >= min_ep and s["confidence"] >= floor):
            return i
    return None
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard._pick_flagged_transfer (guarded, targeted) (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `_run_trigger` — bench + transfer; `run_deadguard_job` passes `cfg`

**Files:**
- Modify: `src/interface/deadguard.py`
- Test: `tests/test_deadguard.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_deadguard.py`:

```python
def test_trigger_optimizes_bench_and_no_transfer(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    lineup_kwargs = {}
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: lineup_kwargs.update(k) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: None)
    xfers = []
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer", lambda *a, **k: xfers.append(1))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert lineup_kwargs.get("optimize_bench") is True
    assert xfers == []                                    # no qualifying transfer
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"
    assert "executed" in notes


def test_trigger_executes_flagged_transfer(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: 2)
    xfers = []
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer",
                        lambda conn, key, **k: xfers.append(k.get("rank")) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert xfers == [2]                                    # ran the chosen rank, live
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"


def test_trigger_transfer_failure_keeps_lineup(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: 1)

    def boom(conn, key, **k):
        raise RuntimeError("transfer api down")

    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer", boom)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    # lineup succeeded -> still EXECUTED + triggered; transfer failure alerted, not retried
    row = db.execute("SELECT state, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "DEADGUARD_EXECUTED" and row["deadguard_triggered_at"] is not None
    assert "alert" in alerts
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_deadguard.py -q` → `test_trigger_optimizes_bench_and_no_transfer` fails (`run_lineup` called without `optimize_bench`; no transfer step).

- [ ] **Step 3: Implement.** In `src/interface/deadguard.py`:

(a) Add the executor import (with the other imports): `from src.execution import transfer as transfer_exec`.

(b) Change `run_deadguard_job`'s trigger dispatch line from:
```python
        elif directive == "trigger":
            _run_trigger(conn, key, gw)
```
to:
```python
        elif directive == "trigger":
            _run_trigger(conn, key, gw, cfg)
```

(c) Replace the whole `_run_trigger` function with:
```python
def _run_trigger(conn, key, gw, cfg):
    repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")
    caps = captain.get_captain_picks(conn)
    if not caps["picks"]:
        repository.set_gameweek_state(conn, gw, "DEADGUARD_SKIPPED")
        repository.mark_deadguard_triggered(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="skipped: no captain pick available", executed=False)
        _notify(conn, "info", "Deadguard ran — no safe action (no data). Team unchanged.")
        return
    # 1. lineup: captain/vice + bench order, one atomic write
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True, optimize_bench=True)
    except SessionExpired:
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        return
    except Exception as e:
        _notify(conn, "alert", f"Deadguard failed: {type(e).__name__}")
        return
    if not getattr(result, "ok", False):
        _notify(conn, "alert", "Deadguard: lineup submission did not complete — will retry.")
        return                                          # not marked -> retryable next tick
    # 2. lineup succeeded -> lock once-per-GW (idempotent re-set of the same lineup is harmless)
    name = caps["picks"][0]["web_name"]
    try:
        repository.mark_deadguard_triggered(conn, gw)
        repository.set_gameweek_state(conn, gw, "DEADGUARD_EXECUTED")
    except Exception:
        log.exception("deadguard post-execution bookkeeping failed (lineup was already set)")
    # 3. transfer-if-flagged (best-effort; never undoes the lineup, never retried)
    transfer_note = "no transfer"
    try:
        rank = _pick_flagged_transfer(conn, cfg)
        if rank is not None:
            tr = transfer_exec.run_transfer(conn, key, rank=rank, live=True, confirm_fn=lambda d: True)
            if getattr(tr, "ok", False):
                transfer_note = "transfer applied"
            else:
                transfer_note = "transfer failed"
                _notify(conn, "alert", "Deadguard: flagged-player transfer did not complete.")
    except Exception as e:
        transfer_note = f"transfer failed ({type(e).__name__})"
        log.exception("deadguard transfer step failed")
        _notify(conn, "alert", f"Deadguard transfer failed: {type(e).__name__}")
    repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                            action_taken=f"captain {name}; bench optimized; {transfer_note}",
                            inputs={"pick": caps["picks"][0]}, executed=True)
    _notify(conn, "executed", f"Deadguard: captain {name}, bench optimized, {transfer_note}.")
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_deadguard.py -q` → all pass. The existing 2.5a trigger tests still pass: `run_lineup` is now called with `optimize_bench=True` (their fake ignores `**k`), and the transfer step is fully wrapped (a bare `get_transfer_suggestions` on their minimal db returns no qualifying transfer → `no transfer`; any error is caught), so state still ends `DEADGUARD_EXECUTED` with an `"executed"` notify.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard trigger optimizes bench + guarded transfer-if-flagged (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: decision-engine changelog + full-suite verification

**Files:**
- Modify: `docs/decision-engine.md`

- [ ] **Step 1: Add the changelog row.** In `docs/decision-engine.md`, append after the `v0.9` row in the Changelog table:
```markdown
| v0.10 | 2026-05-23 | Deadguard 2.5b: bench-order optimization (rank positions 13/14/15 by next-GW xP, xMinutes tiebreaker → FPL native auto-sub); targeted transfer-if-flagged (OUT status not in a/d, free only, ep_delta_5gw ≥ 3.0, confidence ≥ 75, max 1). Captain + transfer engines reused unchanged. |
```

- [ ] **Step 2: Full-suite verification.** Run `.venv/bin/pytest -q`. Expected: all prior 309 tests plus the new 2.5b tests pass (~330 total), zero failures, no network access.

- [ ] **Step 3: Secret-leak check.** Run `grep -n "log\|print" src/decisions/bench.py src/interface/deadguard.py` and confirm no token/chat/URL is logged (deadguard's only logging remains `repository.log_activity` + the fixed-string `log.exception` calls; `bench.py` has no logging).

- [ ] **Step 4: Commit.**
```bash
git add docs/decision-engine.md
git commit -m "docs: decision-engine v0.10 for deadguard bench + transfer-if-flagged (2.5b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done (CLAUDE.md B14)

- [ ] `bench.rank_bench` orders the outfield bench (13/14/15) by next-GW xP; missing xP sorts last.
- [ ] `build_lineup_payload(bench_order=…)` reassigns 13/14/15 only (validates the set); `run_lineup(optimize_bench=True)` puts captain/vice + bench in one write; default off leaves all existing callers unchanged.
- [ ] `_pick_flagged_transfer` returns a rank only when OUT is flagged + free + ep≥3.0 + conf≥75 (else None); honors `transfer_if_flagged`.
- [ ] `_run_trigger`: lineup (captain/vice + bench) first → mark triggered → best-effort guarded transfer; lineup failure retryable; transfer failure leaves the lineup intact + alerts, never retried; one summary notify.
- [ ] Full `pytest -q` green; no token/chat logged; `decision-engine.md` v0.10 added; B8 holds (≤1 free transfer, no chips/hits/multi/formation change); the agent never ran the live daemon.
- [ ] Manual smoke check (out of band, by the user): force a GW into the trigger window with a flagged squad player; confirm the bench order + the single transfer + the summary notification.
