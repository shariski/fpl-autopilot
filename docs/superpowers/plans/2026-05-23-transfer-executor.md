# Transfer Execution Implementation Plan — Phase 2.2b

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Submit a single free transfer (the engine's chosen suggestion) via `POST /api/entry/{entry}/transfers/` — dry-run-first with `--live` + typed confirm, reusing the 2.2a executor.

**Architecture:** Extend `src/execution/executor.py` with the transfers URL, a `build_transfer_payload`, and `apply_transfers` (sharing a new `_post_json` helper with the refactored `apply_lineup`). A new `src/execution/transfer.py` (`run_transfer`) mirrors `lineup.run_lineup`. An `execute-transfer` CLI invokes it. Reuses `fetch_current_picks`, `ExecResult`/`ExecutorError`, `repository.log_activity`, `ensure_session`, and `transfers.get_transfer_suggestions`.

**Tech Stack:** Python 3.11+, `requests` (via the Bearer session), raw `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-transfer-executor-design.md`

**Baseline:** suite is green at 166 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/execution/executor.py` | Modify | add `TRANSFERS_URL`, `_post_json`, `build_transfer_payload`, `apply_transfers`; refactor `apply_lineup` to delegate to `_post_json` |
| `src/execution/transfer.py` | Create | `run_transfer` orchestration |
| `src/cli.py` | Modify | `_execute_transfer_cli` + `execute-transfer` subcommand (`--live`, `--rank`) |
| `tests/test_executor.py` | Modify | `build_transfer_payload` + `apply_transfers` tests |
| `tests/test_transfer.py` | Create | `run_transfer` tests |
| `tests/test_cli_execute_transfer.py` | Create | CLI tests |

Reused: `src/decisions/transfers.get_transfer_suggestions` (`{"suggestions":[{"out":{player_id,web_name,price},"in":{player_id,web_name,price},"ep_delta_5gw","hit_cost":0,"confidence":None}],"empty_reason":...}`) and `transfers._next_gw(conn)`; `src/execution/executor.{fetch_current_picks,ExecResult,ExecutorError,MY_TEAM_URL,TIMEOUT}`; `src/data/repository.log_activity`; `src/auth/session.{ensure_session,SessionError}`; `src/config.team_id`.

---

### Task 1: executor — transfer payload + apply (+ `_post_json` refactor)

**Files:** Modify `src/execution/executor.py`; Test `tests/test_executor.py`

- [x] **Step 1: Write the failing tests** — append to `tests/test_executor.py` (the file already has `_picks`, `_Resp`, `_FakeSession`):

```python
def test_build_transfer_payload_shape():
    out = executor.build_transfer_payload(entry=3122849, event=38, element_out=7,
                                          element_in=99, selling_price=57, purchase_price=60)
    assert out["chip"] is None
    assert out["entry"] == 3122849 and out["event"] == 38
    assert out["transfers"] == [{"element_in": 99, "element_out": 7,
                                 "purchase_price": 60, "selling_price": 57}]


def test_apply_transfers_dry_run_sends_nothing():
    sess = _FakeSession()
    res = executor.apply_transfers(sess, 3122849, {"transfers": []}, dry_run=True)
    assert res.dry_run and res.ok and res.status is None
    assert "entry/3122849/transfers" in res.request["url"]
    assert sess.posted is None


def test_apply_transfers_live_posts():
    sess = _FakeSession(post_status=200)
    payload = {"chip": None, "entry": 3122849, "event": 38, "transfers": []}
    res = executor.apply_transfers(sess, 3122849, payload, dry_run=False)
    assert not res.dry_run and res.ok and res.status == 200
    assert sess.posted["json"] == payload
    assert "entry/3122849/transfers" in sess.posted["url"]


def test_apply_transfers_live_non_200():
    sess = _FakeSession(post_status=400)
    res = executor.apply_transfers(sess, 3122849, {"transfers": []}, dry_run=False)
    assert not res.ok and res.status == 400
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_executor.py -k "transfer" -v`
Expected: FAIL — `AttributeError: module 'src.execution.executor' has no attribute 'build_transfer_payload'`.

- [x] **Step 3: Implement** — in `src/execution/executor.py`:

(a) Add the transfers URL constant right after the `MY_TEAM_URL` line:
```python
TRANSFERS_URL = "https://fantasy.premierleague.com/api/entry/{entry}/transfers/"
```

(b) Replace the ENTIRE existing `apply_lineup` function with a shared helper + the delegating `apply_lineup` + `apply_transfers`:
```python
def _post_json(session, url, payload, *, dry_run):
    request = {"method": "POST", "url": url, "body": payload}
    if dry_run:
        return ExecResult(dry_run=True, request=request, status=None, ok=True)
    resp = session.post(url, json=payload, timeout=TIMEOUT)
    return ExecResult(dry_run=False, request=request, status=resp.status_code, ok=resp.status_code == 200)


def apply_lineup(session, entry_id, payload, *, dry_run):
    return _post_json(session, MY_TEAM_URL.format(entry=entry_id), payload, dry_run=dry_run)


def apply_transfers(session, entry_id, payload, *, dry_run):
    return _post_json(session, TRANSFERS_URL.format(entry=entry_id), payload, dry_run=dry_run)


def build_transfer_payload(*, entry, event, element_out, element_in, selling_price, purchase_price):
    return {"chip": None, "entry": entry, "event": event,
            "transfers": [{"element_in": element_in, "element_out": element_out,
                           "purchase_price": purchase_price, "selling_price": selling_price}]}
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_executor.py -v`
Expected: 12 passed (8 existing — incl. the 3 `apply_lineup` tests still green after the refactor — + 4 new).

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 170 passed (166 + 4).

- [x] **Step 6: Commit**

```bash
git add src/execution/executor.py tests/test_executor.py
git commit -m "feat: executor transfer payload + apply_transfers (_post_json shared)"
```

---

### Task 2: `transfer.run_transfer` orchestration

**Files:** Create `src/execution/transfer.py`; Test `tests/test_transfer.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_transfer.py`:

```python
import pytest
from src.execution import transfer, executor


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
    # element 7 (the OUT player) has selling_price 57; element 8 has 58
    return [{"element": e, "position": e, "selling_price": 50 + e,
             "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]


def _suggester(conn):
    return {"suggestions": [
        {"out": {"player_id": 7, "web_name": "OutA", "price": 5.4},
         "in": {"player_id": 99, "web_name": "InA", "price": 6.0},
         "ep_delta_5gw": 3.1, "hit_cost": 0, "confidence": None},
        {"out": {"player_id": 8, "web_name": "OutB", "price": 5.0},
         "in": {"player_id": 98, "web_name": "InB", "price": 5.5},
         "ep_delta_5gw": 2.0, "hit_cost": 0, "confidence": None},
    ], "empty_reason": None}


def _empty(conn):
    return {"suggestions": [], "empty_reason": "no squad snapshot yet"}


def test_run_transfer_dry_run_uses_live_selling_price(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", live=False, session=sess, suggester=_suggester)
    assert res.dry_run and sess.posted is None
    t = res.request["body"]["transfers"][0]
    assert t["element_out"] == 7 and t["element_in"] == 99
    assert t["selling_price"] == 57          # from /my-team, NOT out.price*10 (54)
    assert t["purchase_price"] == 60         # round(in.price * 10)
    row = db.execute("SELECT executed, decision_type FROM activity_log").fetchone()
    assert row["executed"] == 0 and row["decision_type"] == "transfer"


def test_run_transfer_rank_2(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", rank=2, live=False, session=sess, suggester=_suggester)
    t = res.request["body"]["transfers"][0]
    assert t["element_out"] == 8 and t["element_in"] == 98


def test_run_transfer_live_confirmed(db):
    sess = _FakeSession(_current(), post_status=200)
    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=sess, suggester=_suggester)
    assert not res.dry_run and res.ok and sess.posted is not None
    assert "entry/" in sess.posted["url"] and sess.posted["url"].endswith("/transfers/")
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_run_transfer_live_aborted(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: False,
                                session=sess, suggester=_suggester)
    assert sess.posted is None
    row = db.execute("SELECT action_taken, executed FROM activity_log").fetchone()
    assert row["action_taken"] == "aborted" and row["executed"] == 0


def test_run_transfer_no_suggestions(db):
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", session=_FakeSession(_current()), suggester=_empty)


def test_run_transfer_rank_out_of_range(db):
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", rank=9, session=_FakeSession(_current()),
                              suggester=_suggester)


def test_run_transfer_out_not_in_squad(db):
    sess = _FakeSession([p for p in _current() if p["element"] != 7])  # OUT player missing
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", session=sess, suggester=_suggester)
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_transfer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.execution.transfer'`.

- [x] **Step 3: Implement** — create `src/execution/transfer.py`:

```python
from src import config
from src.auth import session as auth_session
from src.decisions import transfers
from src.execution import executor
from src.data import repository


def run_transfer(conn, key, *, rank=1, live=False, confirm_fn=None, session=None, suggester=None):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    suggestions = sugg["suggestions"]
    if not suggestions:
        raise executor.ExecutorError(sugg.get("empty_reason") or "no transfer suggestion available")
    if not (1 <= rank <= len(suggestions)):
        raise executor.ExecutorError(f"rank {rank} out of range (1..{len(suggestions)})")
    chosen = suggestions[rank - 1]
    element_out = chosen["out"]["player_id"]
    element_in = chosen["in"]["player_id"]
    purchase_price = round(chosen["in"]["price"] * 10)
    current = executor.fetch_current_picks(session, entry)
    selling_price = next((p["selling_price"] for p in current if p["element"] == element_out), None)
    if selling_price is None:
        raise executor.ExecutorError(f"player {element_out} not in current squad")
    event = transfers._next_gw(conn)
    payload = executor.build_transfer_payload(entry=entry, event=event, element_out=element_out,
                                              element_in=element_in, selling_price=selling_price,
                                              purchase_price=purchase_price)
    diff = (f"OUT {chosen['out']['web_name']} -> IN {chosen['in']['web_name']} "
            f"(EP +{chosen['ep_delta_5gw']})")
    inputs = {"chosen": chosen,
              "alternatives": [s for i, s in enumerate(suggestions) if i != rank - 1]}
    url = executor.TRANSFERS_URL.format(entry=entry)

    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual",
                                action_taken="aborted", inputs=inputs, executed=False,
                                exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)

    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    action = f"OUT {element_out} IN {element_in}" if live else "dry-run"
    repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_transfer.py -v`
Expected: 7 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 177 passed (170 + 7).

- [x] **Step 6: Commit**

```bash
git add src/execution/transfer.py tests/test_transfer.py
git commit -m "feat: transfer.run_transfer orchestration (single free transfer)"
```

---

### Task 3: `execute-transfer` CLI

**Files:** Modify `src/cli.py`; Test `tests/test_cli_execute_transfer.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_cli_execute_transfer.py`:

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
             "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]


def _suggester(conn):
    return {"suggestions": [
        {"out": {"player_id": 7, "web_name": "OutA", "price": 5.4},
         "in": {"player_id": 99, "web_name": "InA", "price": 6.0},
         "ep_delta_5gw": 3.1, "hit_cost": 0, "confidence": None},
    ], "empty_reason": None}


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_execute_transfer_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current())
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False,
                              session=sess, suggester=_suggester)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 0


def test_execute_transfer_live_confirmed(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current(), post_status=200)
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=True,
                              session=sess, suggester=_suggester, confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert "Submitted" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_execute_transfer_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False,
                              session=_FakeSession(_current()), suggester=_suggester)
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_execute_transfer.py -v`
Expected: FAIL — `AttributeError: module 'src.cli' has no attribute '_execute_transfer_cli'`.

- [x] **Step 3: Add the CLI function** — in `src/cli.py`, add immediately after `_execute_lineup_cli`:

```python
def _execute_transfer_cli(conn=None, salt_path=None, verify_path=None, live=False, rank=1,
                          session=None, suggester=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import transfer as transfer_mod
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
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned transfer: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        result = transfer_mod.run_transfer(conn, key, rank=rank, live=live, confirm_fn=confirm_fn,
                                           session=session, suggester=suggester)
    except (executor_mod.ExecutorError, SessionError) as exc:
        print(f"Could not execute: {exc}")
        if owns_conn:
            conn.close()
        return
    if live and result.dry_run:
        print("Aborted — nothing submitted.")
    elif result.dry_run:
        print("DRY-RUN — would POST:")
        print(f"  {result.request['method']} {result.request['url']}")
        print(f"  body: {result.request['body']}")
    elif result.ok:
        print(f"Submitted. HTTP {result.status}.")
    else:
        print(f"Submission failed (HTTP {result.status}); nothing changed.")
    if owns_conn:
        conn.close()
```

- [x] **Step 4: Register the subcommand** — in `main()`, after the `execute-lineup` subparser block:
```python
    p_exec = sub.add_parser("execute-lineup", help="set captain & vice from the ranker (dry-run unless --live)")
    p_exec.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
```
add:
```python
    p_xfer = sub.add_parser("execute-transfer", help="make one free transfer from the suggestions (dry-run unless --live)")
    p_xfer.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
    p_xfer.add_argument("--rank", type=int, default=1, help="which suggestion to execute (1-based; default 1)")
```
Then after the `execute-lineup` dispatch branch:
```python
    elif args.command == "execute-lineup":
        _execute_lineup_cli(live=args.live)
```
add:
```python
    elif args.command == "execute-transfer":
        _execute_transfer_cli(live=args.live, rank=args.rank)
```

- [x] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_execute_transfer.py -v`
Expected: 3 passed.

- [x] **Step 6: Run the full suite + CLI help**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: 180 passed; `--help` lists `execute-transfer`. Do NOT run the real `execute-transfer --live`.

- [x] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_execute_transfer.py
git commit -m "feat: execute-transfer CLI (dry-run default, --live + --rank)"
```

---

## Self-Review

**Spec coverage:**
- Transfer payload (`chip null`, one transfer, 4 fields) → Task 1 `build_transfer_payload`.
- Write path `POST entry/{entry}/transfers/`, dry-run/live → Task 1 `apply_transfers` (+ `_post_json` shared with refactored `apply_lineup`).
- Single free transfer, top + `--rank`, selling_price from live `/my-team`, purchase_price = `price×10` → Task 2 `run_transfer`.
- Empty / rank-out-of-range / out-not-in-squad errors → Task 2 tests.
- `--live` + typed confirm, dry-run default, abort path → Task 2 (`confirm_fn`) + Task 3 (CLI prompt + `--live`).
- `activity_log` `decision_type="transfer"`, executed flag, no token → Task 2 logging; `request` carries no token.
- Master key per-invocation; `ExecutorError`/`SessionError` clean messages → Task 3.
- Structurally no hit/chip/multi (chip None, single transfer, engine hit_cost 0) → Task 1 payload + Task 2 single chosen.
- Tests fixtures-only → all inject `_FakeSession` + `suggester`/`confirm_fn`.

**Placeholder scan:** none — every code step complete; run steps have commands + expected counts (170 → 177 → 180). The `apply_lineup` refactor is behavior-preserving; its existing tests are re-run in Task 1 step 4/5.

**Type consistency:** `build_transfer_payload(*, entry, event, element_out, element_in, selling_price, purchase_price)`, `apply_transfers(session, entry_id, payload, *, dry_run)`, `_post_json(session, url, payload, *, dry_run)`, `TRANSFERS_URL` defined in Task 1 and used in Task 2. `run_transfer(conn, key, *, rank, live, confirm_fn, session, suggester)` defined Task 2, called identically in Task 3. Reused `ExecResult`/`ExecutorError`/`fetch_current_picks`/`log_activity` match their 2.2a definitions. Suggestion contract (`out.player_id`, `in.player_id`, `in.price`, `web_name`, `ep_delta_5gw`) matches `get_transfer_suggestions`; `selling_price`/`element` match `fetch_current_picks` output.
