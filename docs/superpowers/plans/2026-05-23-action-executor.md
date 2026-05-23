# Action Executor (captain/vice write) Implementation Plan — Phase 2.2a

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** A dry-run-first executor that reads the current team, sets captain & vice from the ranker, and writes it back via `POST /api/my-team/{entry}/` — live execution gated behind `--live` + a typed confirmation.

**Architecture:** A new `src/execution/` layer: `executor.py` (pure write mechanism — payload builder, current-team read, apply) and `lineup.py` (`run_lineup` orchestration that wires the authed session + captain ranker + executor + activity logging). An `execute-lineup` CLI invokes it. `repository.log_activity` is the first `activity_log` writer.

**Tech Stack:** Python 3.11+, `requests` (via the `ensure_session` Bearer session), raw `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-23-action-executor-design.md`

**Baseline:** suite is green at 151 tests. Run from repo root with `.venv/bin/pytest`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/execution/__init__.py` | Create | package marker |
| `src/execution/executor.py` | Create | `ExecResult`, `ExecutorError`, `MY_TEAM_URL`, `build_lineup_payload`, `fetch_current_picks`, `apply_lineup` |
| `src/execution/lineup.py` | Create | `run_lineup` orchestration |
| `src/data/repository.py` | Modify | `log_activity` |
| `src/cli.py` | Modify | `_execute_lineup_cli` + `execute-lineup` subcommand |
| `pyproject.toml` | Modify | add `src.execution` to packages |
| `tests/test_executor.py`, `tests/test_lineup.py`, `tests/test_cli_execute_lineup.py` | Create | tests |
| `tests/test_repository.py` | Modify | `log_activity` test |

Reused: `src/auth/session.ensure_session` + `SessionError`, `src/decisions/captain.get_captain_picks` (returns `{"picks":[{player_id, web_name, xp, ...}], "vice_player_id": <id>}`), `src/config.team_id`, `src/data/db.connect`/`init_db`, `src/auth/master`.

---

### Task 1: `executor.py` — payload builder + types

**Files:** Create `src/execution/__init__.py`, `src/execution/executor.py`; Modify `pyproject.toml`; Test `tests/test_executor.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_executor.py`:

```python
import pytest
from src.execution import executor


def _picks():
    return [
        {"element": 1, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False},
        {"element": 2, "position": 2, "multiplier": 1, "is_captain": False, "is_vice_captain": True},
        {"element": 3, "position": 3, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
    ]


def test_build_lineup_payload_sets_flags_and_preserves():
    out = executor.build_lineup_payload(_picks(), captain_id=2, vice_id=3)
    assert out["chip"] is None
    by_el = {p["element"]: p for p in out["picks"]}
    assert by_el[2]["is_captain"] and not by_el[2]["is_vice_captain"]
    assert by_el[3]["is_vice_captain"] and not by_el[3]["is_captain"]
    assert not by_el[1]["is_captain"] and not by_el[1]["is_vice_captain"]
    assert [p["position"] for p in out["picks"]] == [1, 2, 3]
    assert set(out["picks"][0]) == {"element", "position", "is_captain", "is_vice_captain"}


def test_build_lineup_payload_captain_equals_vice():
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_picks(), captain_id=2, vice_id=2)


def test_build_lineup_payload_captain_not_in_squad():
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_picks(), captain_id=99, vice_id=3)
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.execution'`.

- [x] **Step 3: Implement**

Create `src/execution/__init__.py` (empty file). Create `src/execution/executor.py`:

```python
from dataclasses import dataclass

MY_TEAM_URL = "https://fantasy.premierleague.com/api/my-team/{entry}/"
TIMEOUT = 10


class ExecutorError(Exception):
    """Invalid lineup payload or a failed team read."""


@dataclass
class ExecResult:
    dry_run: bool
    request: dict       # {"method", "url", "body"} — the exact (would-be) request
    status: int | None  # HTTP status for live; None for dry-run
    ok: bool


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

In `pyproject.toml`, change the packages line to add `src.execution`:
```toml
packages = ["src", "src.data", "src.analytics", "src.decisions", "src.interface", "src.auth", "src.execution"]
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_executor.py -v`
Expected: 3 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 154 passed (151 + 3).

- [x] **Step 6: Commit**

```bash
git add src/execution/__init__.py src/execution/executor.py pyproject.toml tests/test_executor.py
git commit -m "feat: execution.executor payload builder + types"
```

---

### Task 2: `executor.py` — current-team read + apply

**Files:** Modify `src/execution/executor.py`; Test `tests/test_executor.py`

- [x] **Step 1: Write the failing tests** — append to `tests/test_executor.py`:

```python
class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, me=None, me_status=200, post_status=200):
        self._me = me
        self._me_status = me_status
        self._post_status = post_status
        self.posted = None

    def get(self, url, timeout=None):
        return _Resp(self._me_status, self._me)

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def test_fetch_current_picks_ok():
    sess = _FakeSession(me={"picks": _picks()})
    assert executor.fetch_current_picks(sess, 3122849) == _picks()


def test_fetch_current_picks_non_200():
    sess = _FakeSession(me_status=403)
    with pytest.raises(executor.ExecutorError):
        executor.fetch_current_picks(sess, 3122849)


def test_apply_lineup_dry_run_sends_nothing():
    sess = _FakeSession()
    res = executor.apply_lineup(sess, 3122849, {"chip": None, "picks": []}, dry_run=True)
    assert res.dry_run and res.ok and res.status is None
    assert res.request["method"] == "POST"
    assert "my-team/3122849" in res.request["url"]
    assert sess.posted is None


def test_apply_lineup_live_posts():
    sess = _FakeSession(post_status=200)
    payload = {"chip": None, "picks": []}
    res = executor.apply_lineup(sess, 3122849, payload, dry_run=False)
    assert not res.dry_run and res.ok and res.status == 200
    assert sess.posted["json"] == payload
    assert "my-team/3122849" in sess.posted["url"]


def test_apply_lineup_live_non_200():
    sess = _FakeSession(post_status=403)
    res = executor.apply_lineup(sess, 3122849, {"chip": None, "picks": []}, dry_run=False)
    assert not res.ok and res.status == 403
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_executor.py -k "fetch or apply" -v`
Expected: FAIL — `AttributeError: module 'src.execution.executor' has no attribute 'fetch_current_picks'`.

- [x] **Step 3: Implement** — append to `src/execution/executor.py`:

```python
def fetch_current_picks(session, entry_id):
    resp = session.get(MY_TEAM_URL.format(entry=entry_id), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise ExecutorError(f"could not read current team (HTTP {resp.status_code})")
    return resp.json().get("picks", [])


def apply_lineup(session, entry_id, payload, *, dry_run):
    url = MY_TEAM_URL.format(entry=entry_id)
    request = {"method": "POST", "url": url, "body": payload}
    if dry_run:
        return ExecResult(dry_run=True, request=request, status=None, ok=True)
    resp = session.post(url, json=payload, timeout=TIMEOUT)
    return ExecResult(dry_run=False, request=request, status=resp.status_code, ok=resp.status_code == 200)
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_executor.py -v`
Expected: 8 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 159 passed (154 + 5).

- [x] **Step 6: Commit**

```bash
git add src/execution/executor.py tests/test_executor.py
git commit -m "feat: executor fetch_current_picks + apply_lineup (dry-run/live)"
```

---

### Task 3: `repository.log_activity`

**Files:** Modify `src/data/repository.py`; Test `tests/test_repository.py`

- [x] **Step 1: Write the failing test** — append to `tests/test_repository.py`:

```python
def test_log_activity_roundtrip(db):
    import json as _json
    from src.data import repository
    repository.log_activity(db, decision_type="lineup", mode="manual",
                            action_taken="captain=5, vice=6",
                            inputs={"xp": 7.1}, executed=True,
                            exec_outcome={"status": 200}, gw=38)
    row = db.execute("SELECT * FROM activity_log").fetchone()
    assert row["decision_type"] == "lineup"
    assert row["mode"] == "manual"
    assert row["executed"] == 1
    assert row["gw"] == 38
    assert _json.loads(row["inputs_json"])["xp"] == 7.1
    assert _json.loads(row["exec_outcome_json"])["status"] == 200
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_repository.py::test_log_activity_roundtrip -v`
Expected: FAIL — `AttributeError: module 'src.data.repository' has no attribute 'log_activity'`.

- [x] **Step 3: Implement** — `src/data/repository.py` already has `import json` and `_now()`. Add at the end of the file:

```python
def log_activity(conn, *, decision_type, mode, action_taken, inputs=None,
                 executed=False, exec_outcome=None, gw=None, alternatives=None):
    conn.execute(
        "INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, "
        "inputs_json, alternatives_json, executed, exec_outcome_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), gw, mode, decision_type, action_taken,
         json.dumps(inputs) if inputs is not None else None,
         json.dumps(alternatives) if alternatives is not None else None,
         executed,
         json.dumps(exec_outcome) if exec_outcome is not None else None),
    )
    conn.commit()
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_repository.py::test_log_activity_roundtrip -v`
Expected: PASS.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 160 passed (159 + 1).

- [x] **Step 6: Commit**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "feat: repository.log_activity (activity_log writer)"
```

---

### Task 4: `lineup.run_lineup` orchestration

**Files:** Create `src/execution/lineup.py`; Test `tests/test_lineup.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_lineup.py`:

```python
from src.execution import lineup


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
    return [{"element": e, "position": e, "is_captain": e == 1, "is_vice_captain": e == 2}
            for e in range(1, 16)]


def _ranker(conn):
    return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                      {"player_id": 6, "web_name": "Vice", "xp": 6.0}],
            "vice_player_id": 6}


def test_run_lineup_dry_run(db):
    sess = _FakeSession(_current())
    res = lineup.run_lineup(db, key=b"unused", live=False, session=sess, ranker=_ranker)
    assert res.dry_run and sess.posted is None
    row = db.execute("SELECT executed, decision_type FROM activity_log").fetchone()
    assert row["executed"] == 0 and row["decision_type"] == "lineup"


def test_run_lineup_live_confirmed(db):
    sess = _FakeSession(_current(), post_status=200)
    res = lineup.run_lineup(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                            session=sess, ranker=_ranker)
    assert not res.dry_run and res.ok and sess.posted is not None
    by_el = {p["element"]: p for p in sess.posted["json"]["picks"]}
    assert by_el[5]["is_captain"] and by_el[6]["is_vice_captain"]
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_run_lineup_live_aborted(db):
    sess = _FakeSession(_current())
    res = lineup.run_lineup(db, key=b"unused", live=True, confirm_fn=lambda d: False,
                            session=sess, ranker=_ranker)
    assert sess.posted is None
    row = db.execute("SELECT action_taken, executed FROM activity_log").fetchone()
    assert row["action_taken"] == "aborted" and row["executed"] == 0
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_lineup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.execution.lineup'`.

- [x] **Step 3: Implement** — create `src/execution/lineup.py`:

```python
from src import config
from src.auth import session as auth_session
from src.decisions import captain as captain_mod
from src.execution import executor
from src.data import repository


def _format_diff(current, captain_id, vice_id):
    cur_c = next((p["element"] for p in current if p.get("is_captain")), None)
    cur_v = next((p["element"] for p in current if p.get("is_vice_captain")), None)
    return f"captain {cur_c}->{captain_id}, vice {cur_v}->{vice_id}"


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
    diff = _format_diff(current, captain_id, vice_id)
    inputs = {"captain": caps["picks"][0], "vice_player_id": vice_id,
              "alternatives": caps["picks"][1:]}
    url = executor.MY_TEAM_URL.format(entry=entry)

    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="lineup", mode="manual",
                                action_taken="aborted", inputs=inputs, executed=False,
                                exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)

    result = executor.apply_lineup(session, entry, payload, dry_run=not live)
    action = f"captain={captain_id}, vice={vice_id}" if live else "dry-run"
    repository.log_activity(conn, decision_type="lineup", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
```

- [x] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_lineup.py -v`
Expected: 3 passed.

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 163 passed (160 + 3).

- [x] **Step 6: Commit**

```bash
git add src/execution/lineup.py tests/test_lineup.py
git commit -m "feat: lineup.run_lineup orchestration (read/rank/build/apply/log)"
```

---

### Task 5: `execute-lineup` CLI

**Files:** Modify `src/cli.py`; Test `tests/test_cli_execute_lineup.py`

- [x] **Step 1: Write the failing tests** — create `tests/test_cli_execute_lineup.py`:

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
    return [{"element": e, "position": e, "is_captain": e == 1, "is_vice_captain": e == 2}
            for e in range(1, 16)]


def _ranker(conn):
    return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                      {"player_id": 6, "web_name": "Vice", "xp": 6.0}],
            "vice_player_id": 6}


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_execute_lineup_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current())
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=False,
                            session=sess, ranker=_ranker)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 0


def test_execute_lineup_live_confirmed(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current(), post_status=200)
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=True,
                            session=sess, ranker=_ranker, confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert "Submitted" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_execute_lineup_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=False,
                            session=_FakeSession(_current()), ranker=_ranker)
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_execute_lineup.py -v`
Expected: FAIL — `AttributeError: module 'src.cli' has no attribute '_execute_lineup_cli'`.

- [x] **Step 3: Add the CLI function** — in `src/cli.py`, add this function immediately after `_auth_status_cli` (before `serve`). `cfg_db_path`, `connect`, `init_db` are already imported at the top:

```python
def _execute_lineup_cli(conn=None, salt_path=None, verify_path=None, live=False,
                        session=None, ranker=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import lineup as lineup_mod
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
            print(f"Planned change: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        result = lineup_mod.run_lineup(conn, key, live=live, confirm_fn=confirm_fn,
                                       session=session, ranker=ranker)
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

- [x] **Step 4: Register the subcommand** — in `main()`, after the `auth-status` subparser line:
```python
    sub.add_parser("auth-status", help="show stored FPL session state (no secrets)")
```
add:
```python
    p_exec = sub.add_parser("execute-lineup", help="set captain & vice from the ranker (dry-run unless --live)")
    p_exec.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
```
Then after the `auth-status` dispatch branch:
```python
    elif args.command == "auth-status":
        _auth_status_cli()
```
add:
```python
    elif args.command == "execute-lineup":
        _execute_lineup_cli(live=args.live)
```

- [x] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_execute_lineup.py -v`
Expected: 3 passed.

- [x] **Step 6: Run the full suite + CLI help**

```bash
.venv/bin/pytest -q
.venv/bin/fpl-autopilot --help
```
Expected: 166 passed; `--help` lists `execute-lineup`. Do NOT run the real `execute-lineup --live`.

- [x] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_execute_lineup.py
git commit -m "feat: execute-lineup CLI (dry-run default, --live + typed confirm)"
```

---

## Self-Review

**Spec coverage:**
- Captain/vice payload, bench preserved → Task 1 `build_lineup_payload` (only flips two flags, preserves order/positions).
- Write path `POST /my-team/{entry}/`, dry-run vs live → Task 2 `apply_lineup`.
- Read current team → Task 2 `fetch_current_picks`.
- Orchestration (read→rank→build→confirm→apply→log), captain=`picks[0].player_id`/vice=`vice_player_id` → Task 4 `run_lineup`.
- `--live` + typed confirm; dry-run default; abort path → Task 4 (`confirm_fn`) + Task 5 (CLI prompt + `--live`).
- `activity_log` writer, executed flag, no token → Task 3 `log_activity`; Task 4 logs `request` (no token — token lives only on session headers).
- Master key per-invocation; `SessionError`/`ExecutorError` clean messages → Task 5.
- Tests fixtures-only, no live calls → all tests inject `_FakeSession` + `ranker`/`confirm_fn`.

**Placeholder scan:** none — every code step complete; every run step has a command + expected count (154→159→160→163→166).

**Type consistency:** `ExecResult(dry_run, request, status, ok)` and `ExecutorError` defined in Task 1, used in Tasks 2/4/5. `build_lineup_payload(current_picks, captain_id, vice_id)`, `fetch_current_picks(session, entry_id)`, `apply_lineup(session, entry_id, payload, *, dry_run)` consistent across Tasks 2/4. `run_lineup(conn, key, *, live, confirm_fn, session, ranker)` defined Task 4, called identically by Task 5. `log_activity(conn, *, decision_type, mode, action_taken, inputs, executed, exec_outcome, gw, alternatives)` defined Task 3, called in Task 4. Captain-ranker contract (`picks[0].player_id`, `vice_player_id`) matches `get_captain_picks`.
