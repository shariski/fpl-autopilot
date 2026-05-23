# Mode Router Implementation Plan — Phase 2.3b

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each gameweek decision to *execute* (via the 2.2 executors) or *notify-and-wait* (a pending `activity_log` row) per the current mode + confidence — dry-run-first, `--live` to act.

**Architecture:** A pure `router.route(...)` policy + a `route_gameweek` orchestration that reads captain/transfer decisions (with confidence), routes them, and either calls `run_lineup`/`run_transfer` (auto-approved per-decision confirm) or logs a pending row. A `route-gameweek` CLI (dry-run default, `--live` + one upfront confirm, `--mode` override). Small config accessors.

**Tech Stack:** Python 3.11+, `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-mode-router-design.md`

**Baseline:** suite is green at 187 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/config.py` | Modify | `mode()`, `confidence_floor()` accessors |
| `src/execution/router.py` | Create | pure `route()` + `route_gameweek()` |
| `src/cli.py` | Modify | `_route_gameweek_cli` + `route-gameweek` subcommand |
| `docs/decision-engine.md` | Modify | note the universal confidence gate (changelog) |
| `tests/test_config.py` | Create | accessor tests |
| `tests/test_router.py` | Create | `route` table + `route_gameweek` tests |
| `tests/test_cli_route.py` | Create | CLI tests |

Reused: `src/decisions/captain.get_captain_picks`, `src/decisions/transfers.get_transfer_suggestions` (both return `confidence`; suggestions also `ep_delta_5gw`, `hit_cost`), `src/execution/lineup.run_lineup`, `src/execution/transfer.run_transfer`, `src/execution/executor.ExecutorError`, `src/data/repository.log_activity`, `src/auth/session.SessionError`, `src/auth/master`.

---

### Task 1: config accessors

**Files:** Modify `src/config.py`; Test `tests/test_config.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_config.py`:

```python
from src import config


def test_mode_from_config():
    assert config.mode({"mode": {"current": "auto"}}) == "auto"
    assert config.mode({}) == "manual"  # default


def test_confidence_floor_from_config():
    assert config.confidence_floor({"thresholds": {"confidence_floor": 65}}) == 65
    assert config.confidence_floor({}) == 70  # default
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'src.config' has no attribute 'mode'`.

- [ ] **Step 3: Implement** — append to `src/config.py`:

```python
def mode(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("mode", {}).get("current", "manual")


def confidence_floor(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("thresholds", {}).get("confidence_floor", 70)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 189 passed (187 + 2).

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: config mode + confidence_floor accessors"
```

---

### Task 2: pure `route` policy

**Files:** Create `src/execution/router.py`; Test `tests/test_router.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_router.py`:

```python
from src.execution import router


def test_route_manual_always_notify():
    assert router.route("manual", "captain", confidence=90) == "notify"
    assert router.route("manual", "transfer", confidence=90, ep_delta=10.0) == "notify"


def test_route_auto_confidence_gate():
    assert router.route("auto", "captain", confidence=80, floor=70) == "execute"
    assert router.route("auto", "captain", confidence=60, floor=70) == "notify"
    assert router.route("auto", "transfer", confidence=80, ep_delta=1.0, floor=70) == "execute"


def test_route_hybrid_captain_conf_gated():
    assert router.route("hybrid", "captain", confidence=80, floor=70) == "execute"
    assert router.route("hybrid", "captain", confidence=60, floor=70) == "notify"  # universal gate


def test_route_hybrid_transfer_threshold():
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=5.0, is_hit=False, floor=70) == "execute"
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=2.0, is_hit=False, floor=70) == "notify"
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=10.0, is_hit=True, floor=70) == "notify"


def test_route_none_confidence_notifies():
    assert router.route("auto", "captain", confidence=None, floor=70) == "notify"


def test_route_chip_or_unknown_notify():
    assert router.route("hybrid", "chip", confidence=99) == "notify"
    assert router.route("weird-mode", "captain", confidence=99) == "notify"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.execution.router'`.

- [ ] **Step 3: Implement** — create `src/execution/router.py`:

```python
from src import config
from src.decisions import captain, transfers
from src.execution import lineup, transfer as transfer_exec
from src.data import repository

HYBRID_TRANSFER_EP_FLOOR = 4.0


def _auto_approve(diff=None):
    return True


def route(mode, decision_type, *, confidence, ep_delta=None, is_hit=False, floor=70):
    if mode == "manual":
        return "notify"
    if mode == "auto":
        eligible = True
    elif mode == "hybrid":
        if decision_type == "captain":
            eligible = True
        elif decision_type == "transfer":
            eligible = (not is_hit) and ((ep_delta or 0) >= HYBRID_TRANSFER_EP_FLOOR)
        else:
            eligible = False
    else:
        eligible = False
    if not eligible:
        return "notify"
    if confidence is None or confidence < floor:
        return "notify"
    return "execute"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 196 passed (189 + 7).

- [ ] **Step 6: Commit**

```bash
git add src/execution/router.py tests/test_router.py
git commit -m "feat: router.route pure policy (mode + confidence -> execute/notify)"
```

---

### Task 3: `route_gameweek` orchestration

**Files:** Modify `src/execution/router.py`, `tests/test_router.py`, `docs/decision-engine.md`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_router.py`:

```python
class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, current, post_status=200):
        self._current = current
        self._post_status = post_status
        self.posted = None
        self.got = False

    def get(self, url, timeout=None):
        self.got = True
        return _Resp(200, {"picks": self._current})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def _current():
    return [{"element": e, "position": e, "selling_price": 50 + e,
             "is_captain": e == 1, "is_vice_captain": e == 2} for e in range(1, 16)]


def _ranker(conf=82):
    def f(conn):
        return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                          {"player_id": 6, "web_name": "Vc", "xp": 6.0}],
                "vice_player_id": 6, "confidence": conf}
    return f


def _suggester(conf=80, ep=5.0):
    def f(conn):
        return {"suggestions": [{"out": {"player_id": 7, "web_name": "O", "price": 5.4},
                                 "in": {"player_id": 99, "web_name": "I", "price": 6.0},
                                 "ep_delta_5gw": ep, "hit_cost": 0, "confidence": conf}],
                "empty_reason": None}
    return f


def test_route_gameweek_auto_executes(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="auto",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 5.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes == {"captain": "execute", "transfer": "execute"}
    assert sess.got                              # executors read the current team
    assert sess.posted is None                   # dry-run: nothing submitted
    assert db.execute("SELECT COUNT(*) c FROM activity_log WHERE executed=1").fetchone()["c"] == 0


def test_route_gameweek_manual_notifies(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    assert all(p["route"] == "notify" for p in plan)
    assert not sess.got and sess.posted is None  # executors NOT called
    rows = db.execute("SELECT action_taken FROM activity_log").fetchall()
    assert len(rows) == 2 and all(r["action_taken"].startswith("pending") for r in rows)


def test_route_gameweek_hybrid_mixed(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="hybrid",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 2.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes == {"captain": "execute", "transfer": "notify"}  # transfer EP<4 -> notify


def test_route_gameweek_low_conf_captain_gated(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="hybrid",
                                 session=sess, ranker=_ranker(60), suggester=_suggester(80, 5.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes["captain"] == "notify"  # 60 < 70 universal gate
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_router.py -k route_gameweek -v`
Expected: FAIL — `AttributeError: module 'src.execution.router' has no attribute 'route_gameweek'`.

- [ ] **Step 3: Implement** — append to `src/execution/router.py`:

```python
def route_gameweek(conn, key, *, live=False, mode=None, session=None, ranker=None, suggester=None):
    mode = mode or config.mode()
    floor = config.confidence_floor()
    caps = (ranker or captain.get_captain_picks)(conn)
    plan = []
    if caps["picks"]:
        r = route(mode, "captain", confidence=caps["confidence"], floor=floor)
        plan.append({"decision": "captain", "route": r, "confidence": caps["confidence"]})
        if r == "execute":
            lineup.run_lineup(conn, key, live=live, confirm_fn=_auto_approve,
                              session=session, ranker=ranker)
        else:
            repository.log_activity(conn, decision_type="lineup", mode=mode,
                                    action_taken=f"pending: captain {caps['picks'][0]['web_name']}",
                                    inputs={"confidence": caps["confidence"], "pick": caps["picks"][0]},
                                    executed=False)
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    if sugg["suggestions"]:
        top = sugg["suggestions"][0]
        r = route(mode, "transfer", confidence=top["confidence"], ep_delta=top["ep_delta_5gw"],
                  is_hit=top["hit_cost"] < 0, floor=floor)
        plan.append({"decision": "transfer", "route": r, "confidence": top["confidence"]})
        if r == "execute":
            transfer_exec.run_transfer(conn, key, rank=1, live=live, confirm_fn=_auto_approve,
                                       session=session, suggester=suggester)
        else:
            repository.log_activity(conn, decision_type="transfer", mode=mode,
                                    action_taken=f"pending: OUT {top['out']['web_name']} IN {top['in']['web_name']}",
                                    inputs={"confidence": top["confidence"], "suggestion": top},
                                    executed=False)
    return plan
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: 11 passed (7 + 4).

- [ ] **Step 5: Document the universal gate in `decision-engine.md`** (B4) — under the mode-routing section add:

```markdown
**Universal confidence gate (v0.8, 2026-05-23):** the confidence floor applies to *every*
auto-route, not just Auto mode. In Hybrid, a captain/bench or qualifying-transfer decision whose
`confidence < floor` falls back to notify-and-wait (rather than auto-executing). Manual mode always
notifies regardless of confidence.
```
Add a one-line changelog entry.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 200 passed (196 + 4).

- [ ] **Step 7: Commit**

```bash
git add src/execution/router.py tests/test_router.py docs/decision-engine.md
git commit -m "feat: route_gameweek orchestration; document universal confidence gate"
```

---

### Task 4: `route-gameweek` CLI

**Files:** Modify `src/cli.py`; Test `tests/test_cli_route.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_cli_route.py`:

```python
from src import cli
from src.auth import master


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, current, post_status=200):
        self._current = current
        self._post_status = post_status
        self.posted = None

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._current})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def _current():
    return [{"element": e, "position": e, "selling_price": 50 + e,
             "is_captain": e == 1, "is_vice_captain": e == 2} for e in range(1, 16)]


def _ranker(conn):
    return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                      {"player_id": 6, "web_name": "Vc", "xp": 6.0}],
            "vice_player_id": 6, "confidence": 82}


def _suggester(conn):
    return {"suggestions": [{"out": {"player_id": 7, "web_name": "O", "price": 5.4},
                             "in": {"player_id": 99, "web_name": "I", "price": 6.0},
                             "ep_delta_5gw": 5.0, "hit_cost": 0, "confidence": 80}],
            "empty_reason": None}


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_route_gameweek_cli_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current())
    cli._route_gameweek_cli(conn=db, salt_path=s, verify_path=v, live=False, mode="auto",
                            session=sess, ranker=_ranker, suggester=_suggester)
    out = capsys.readouterr().out
    assert "captain" in out and "EXECUTE" in out
    assert sess.posted is None


def test_route_gameweek_cli_live_confirmed(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current(), post_status=200)
    cli._route_gameweek_cli(conn=db, salt_path=s, verify_path=v, live=True, mode="auto",
                            session=sess, ranker=_ranker, suggester=_suggester,
                            confirm_fn=lambda: True)
    assert sess.posted is not None  # at least one execute-route submitted
    assert db.execute("SELECT COUNT(*) c FROM activity_log WHERE executed=1").fetchone()["c"] >= 1


def test_route_gameweek_cli_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    cli._route_gameweek_cli(conn=db, salt_path=s, verify_path=v, live=False, mode="auto",
                            session=_FakeSession(_current()), ranker=_ranker, suggester=_suggester)
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_route.py -v`
Expected: FAIL — `AttributeError: module 'src.cli' has no attribute '_route_gameweek_cli'`.

- [ ] **Step 3: Add the CLI function** — in `src/cli.py`, add immediately after `_execute_transfer_cli`:

```python
def _route_gameweek_cli(conn=None, salt_path=None, verify_path=None, live=False, mode=None,
                        session=None, ranker=None, suggester=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import router as router_mod
    from .execution import executor as executor_mod
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if live:
        if confirm_fn is None:
            def confirm_fn():
                return input("Execute the auto-routed decisions live on your FPL team? Type 'yes': ").strip().lower() == "yes"
        if not confirm_fn():
            print("Aborted — nothing executed.")
            return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        plan = router_mod.route_gameweek(conn, key, live=live, mode=mode,
                                         session=session, ranker=ranker, suggester=suggester)
    except (executor_mod.ExecutorError, SessionError) as exc:
        print(f"Could not route: {exc}")
        if owns_conn:
            conn.close()
        return
    label = "LIVE" if live else "DRY-RUN"
    print(f"Mode-router plan ({label}):")
    for p in plan:
        print(f"  {p['decision']}: {p['route'].upper()} (confidence {p['confidence']})")
    if owns_conn:
        conn.close()
```

- [ ] **Step 4: Register the subcommand** — in `main()`, after the `execute-transfer` subparser block:
```python
    p_xfer = sub.add_parser("execute-transfer", help="make one free transfer from the suggestions (dry-run unless --live)")
    p_xfer.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
    p_xfer.add_argument("--rank", type=int, default=1, help="which suggestion to execute (1-based; default 1)")
```
add:
```python
    p_route = sub.add_parser("route-gameweek", help="route captain + transfer per mode/confidence (dry-run unless --live)")
    p_route.add_argument("--live", action="store_true", help="execute the auto-routed decisions (requires typed confirmation)")
    p_route.add_argument("--mode", choices=["auto", "manual", "hybrid"], default=None,
                         help="override config mode for this run")
```
Then after the `execute-transfer` dispatch branch:
```python
    elif args.command == "execute-transfer":
        _execute_transfer_cli(live=args.live, rank=args.rank)
```
add:
```python
    elif args.command == "route-gameweek":
        _route_gameweek_cli(live=args.live, mode=args.mode)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_route.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full suite + CLI help**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: 203 passed; `--help` lists `route-gameweek`. Do NOT run the real `route-gameweek --live`.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_route.py
git commit -m "feat: route-gameweek CLI (dry-run default, --live + --mode)"
```

---

## Self-Review

**Spec coverage:**
- Pure routing policy (manual/auto/hybrid, universal confidence gate, hybrid EP≥4, chips notify) → Task 2 `route`.
- Orchestration (route captain + top transfer, execute via run_lineup/run_transfer with auto-approve, notify → pending log) → Task 3 `route_gameweek`.
- Dry-run default, `--live` + upfront confirm, `--mode` override → Task 4 CLI.
- Config accessors → Task 1.
- Universal-gate doc → Task 3 step 5 (B4).
- Notify = pending `activity_log` row → Task 3 (`log_activity ... action_taken="pending: ..."`, executed=False).
- Agent never runs `--live`; executor errors surface cleanly → Task 4 (`try/except`, upfront confirm).

**Placeholder scan:** none — every code step complete; run steps have commands + expected counts (189 → 196 → 200 → 203).

**Type consistency:** `route(mode, decision_type, *, confidence, ep_delta=None, is_hit=False, floor=70)` defined Task 2, called in Task 3. `route_gameweek(conn, key, *, live, mode, session, ranker, suggester)` defined Task 3, called identically by Task 4. Executor calls match real signatures: `run_lineup(conn, key, *, live, confirm_fn, session, ranker)`, `run_transfer(conn, key, *, rank, live, confirm_fn, session, suggester)`. `config.mode()`/`config.confidence_floor()` from Task 1 used in Task 3. Reader contracts (`caps["picks"][0]["web_name"]`, `caps["confidence"]`, `top["ep_delta_5gw"]`, `top["hit_cost"]`, `top["confidence"]`) match `get_captain_picks`/`get_transfer_suggestions`. Notify rows use `decision_type` `"lineup"`/`"transfer"` (consistent with the executors), a deliberate refinement of the spec's `"captain"`.
