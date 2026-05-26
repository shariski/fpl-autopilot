# Authed Read-Model Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the authed FPL `/api/my-team/{entry}/` endpoint into the dashboard read-model so the squad shown is the upcoming-GW squad (not last-finished snapshot), and surface `free_transfers` to the transfer executor + deadguard so live writes never take a silent `-4` hit.

**Architecture:** The scheduler (which already loads the master key in unattended mode) and a new `refresh-my-team` CLI fetch the authed payload and write a richer `my_team` row at `gw=next_gw`. Every reader already does `ORDER BY gw DESC LIMIT 1` — the authed row wins automatically, with the public row as graceful fallback. Web layer never sees the key (preserves the 2.5c-3 invariant). The transfer executor gains a preflight; deadguard refuses transfers when `free_transfers=0` per B8.

**Tech Stack:** Python 3.11+, SQLite, FastAPI (untouched), APScheduler, `requests`-shaped sessions injected as fixtures. Tests: pytest with the existing `_FakeSession`/`_Resp` patterns and the `db` fixture from `tests/conftest.py`.

**Spec:** `docs/superpowers/specs/2026-05-26-authed-read-model-wiring-design.md` (commit `d51b286`).

---

## Setup

Run once before Task 1.

- [ ] **Create the slice branch** off the current `main`.

```bash
cd /Users/shariski/Work/fpl-autopilot
git checkout main
git pull --ff-only
git checkout -b feat/authed-read-model-wiring
```

- [ ] **Confirm baseline tests pass.**

```bash
.venv/bin/pytest -q
```

Expected: `404 passed` (current main baseline per `HANDOFF.md`). If anything is red, stop and surface it — don't start the slice on top of a broken tree.

---

## Task 1: `fetch_my_team_authed` in executor

The simplest building block — a pure read that hits the authed endpoint and returns parsed JSON.

**Files:**
- Modify: `src/execution/executor.py` (add new function after `fetch_current_picks`)
- Test: `tests/test_executor.py` (add new test cases)

- [ ] **Step 1.1: Write the failing tests.**

Add these tests at the bottom of `tests/test_executor.py`:

```python
def test_fetch_my_team_authed_returns_full_payload():
    """Returns the full JSON dict, not just picks (unlike fetch_current_picks)."""
    payload = {
        "picks": [{"element": 1, "position": 1, "selling_price": 50,
                   "purchase_price": 50, "is_captain": True, "is_vice_captain": False,
                   "multiplier": 2}],
        "transfers": {"bank": 5, "value": 1003, "limit": 1, "cost": 4, "status": "cost", "made": 0},
        "chips": [{"name": "wildcard", "status_for_entry": "available"}],
    }

    class _Resp:
        status_code = 200
        def json(self): return payload

    class _Sess:
        def __init__(self): self.url = None
        def get(self, url, timeout=None):
            self.url = url
            return _Resp()

    sess = _Sess()
    result = executor.fetch_my_team_authed(sess, 12345)
    assert result == payload
    assert sess.url == "https://fantasy.premierleague.com/api/my-team/12345/"


def test_fetch_my_team_authed_raises_on_non_200():
    """Non-200 raises ExecutorError with the status code, matching fetch_current_picks shape."""
    class _Resp:
        status_code = 401
        def json(self): return {}

    class _Sess:
        def get(self, url, timeout=None): return _Resp()

    with pytest.raises(executor.ExecutorError) as exc_info:
        executor.fetch_my_team_authed(_Sess(), 12345)
    assert "401" in str(exc_info.value)
```

If `tests/test_executor.py` doesn't already `import pytest`, add `import pytest` at the top. If it already imports `executor`, leave that import as is.

- [ ] **Step 1.2: Run the new tests to verify they fail.**

```bash
.venv/bin/pytest tests/test_executor.py::test_fetch_my_team_authed_returns_full_payload tests/test_executor.py::test_fetch_my_team_authed_raises_on_non_200 -v
```

Expected: both FAIL with `AttributeError: module 'src.execution.executor' has no attribute 'fetch_my_team_authed'`.

- [ ] **Step 1.3: Implement the function.**

In `src/execution/executor.py`, add this function immediately after `fetch_current_picks` (around line 51, after the existing helper):

```python
def fetch_my_team_authed(session, entry_id):
    """GET /api/my-team/{entry}/ — returns the full authed payload (picks + transfers + chips).

    Unlike fetch_current_picks (which returns just .picks), this returns the whole dict so the
    caller can extract transfers.limit (free_transfers), bank, team value, and chips. Auth-only;
    requires a healthy session.
    """
    resp = session.get(MY_TEAM_URL.format(entry=entry_id), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise ExecutorError(f"could not read authed my-team (HTTP {resp.status_code})")
    return resp.json()
```

- [ ] **Step 1.4: Run the new tests to verify they pass.**

```bash
.venv/bin/pytest tests/test_executor.py -v
```

Expected: all tests in `test_executor.py` PASS (the two new ones plus the existing).

- [ ] **Step 1.5: Commit.**

```bash
git add src/execution/executor.py tests/test_executor.py
git commit -m "$(cat <<'EOF'
feat: fetch_my_team_authed returns full /api/my-team payload

Building block for the authed read-model wiring. Existing
fetch_current_picks returns only .picks; the snapshot needs the whole
payload (transfers.limit -> free_transfers, transfers.bank, etc.).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `snapshot_my_team_authed` in repository

Pure write — extracts picks + bank + team_value + free_transfers + chips from the authed payload and INSERT OR REPLACEs into `my_team`. B6: raises loudly on schema drift.

**Files:**
- Modify: `src/data/repository.py` (add new function after `snapshot_my_team`, ~line 95)
- Test: `tests/test_repository.py` (add new test cases)

- [ ] **Step 2.1: Read existing `snapshot_my_team` for shape conventions.**

```bash
sed -n '79,100p' src/data/repository.py
```

Expected output: the existing `snapshot_my_team(conn, gw, picks)` function. Note the column list and `INSERT OR REPLACE` pattern — you'll mirror it.

- [ ] **Step 2.2: Write the failing tests.**

Add at the bottom of `tests/test_repository.py`:

```python
import json


def test_snapshot_my_team_authed_extracts_all_fields(db):
    payload = {
        "picks": [{"element": e, "position": e, "is_captain": e == 1,
                   "is_vice_captain": e == 2, "selling_price": 50,
                   "purchase_price": 50, "multiplier": 1} for e in range(1, 16)],
        "transfers": {"bank": 23, "value": 1004, "limit": 2, "cost": 4, "status": "cost", "made": 0},
        "chips": [{"name": "wildcard", "status_for_entry": "available"},
                  {"name": "bboost", "status_for_entry": "played", "played_by_entry": [38]}],
    }
    repository.snapshot_my_team_authed(db, 38, payload)
    row = db.execute(
        "SELECT picks_json, bank, team_value, free_transfers, chips_used_json FROM my_team WHERE gw=38"
    ).fetchone()
    assert row is not None
    picks = json.loads(row["picks_json"])
    assert len(picks) == 15 and picks[0]["element"] == 1
    assert row["bank"] == 2.3       # /10 to convert tenths to whole units (existing convention)
    assert row["team_value"] == 100.4
    assert row["free_transfers"] == 2
    # chips_used_json should be the raw chips list (caller decides format downstream)
    assert json.loads(row["chips_used_json"]) == payload["chips"]


def test_snapshot_my_team_authed_idempotent(db):
    payload = {
        "picks": [{"element": 1, "position": 1, "is_captain": True, "is_vice_captain": False,
                   "selling_price": 50, "purchase_price": 50, "multiplier": 2}],
        "transfers": {"bank": 0, "value": 1000, "limit": 1, "cost": 0, "status": "cost", "made": 0},
        "chips": [],
    }
    repository.snapshot_my_team_authed(db, 5, payload)
    repository.snapshot_my_team_authed(db, 5, payload)
    rows = db.execute("SELECT COUNT(*) c FROM my_team WHERE gw=5").fetchone()
    assert rows["c"] == 1  # INSERT OR REPLACE


def test_snapshot_my_team_authed_raises_on_missing_transfers(db):
    payload = {"picks": [], "chips": []}  # transfers key absent
    with pytest.raises(KeyError):
        repository.snapshot_my_team_authed(db, 7, payload)


def test_snapshot_my_team_authed_raises_on_missing_limit(db):
    payload = {"picks": [], "transfers": {"bank": 0, "value": 1000}, "chips": []}  # no limit
    with pytest.raises(KeyError):
        repository.snapshot_my_team_authed(db, 7, payload)


def test_snapshot_my_team_authed_chips_null_when_absent(db):
    payload = {
        "picks": [],
        "transfers": {"bank": 0, "value": 1000, "limit": 1},
    }  # chips key absent — allowed, stored as NULL
    repository.snapshot_my_team_authed(db, 9, payload)
    row = db.execute("SELECT chips_used_json FROM my_team WHERE gw=9").fetchone()
    assert row["chips_used_json"] is None
```

If `tests/test_repository.py` doesn't already `import pytest`, add `import pytest` at the top.

- [ ] **Step 2.3: Run the new tests to verify they fail.**

```bash
.venv/bin/pytest tests/test_repository.py -v -k "authed"
```

Expected: all five new tests FAIL with `AttributeError: module 'src.data.repository' has no attribute 'snapshot_my_team_authed'`.

- [ ] **Step 2.4: Implement the function.**

In `src/data/repository.py`, add this function immediately after `snapshot_my_team`:

```python
def snapshot_my_team_authed(conn, gw, payload):
    """Write an authed /api/my-team payload to my_team. Includes free_transfers (transfers.limit).

    Stored under gw=next_gw (the upcoming GW that this team is FOR), so that readers doing
    ORDER BY gw DESC LIMIT 1 prefer the authed row over the public-picks row from the prior GW.

    Raises KeyError on schema drift (missing transfers / missing limit) per B6.
    """
    picks = payload["picks"]
    transfers = payload["transfers"]  # raises KeyError if absent — B6
    free_transfers = transfers["limit"]  # raises KeyError if absent — B6
    bank = transfers.get("bank", 0) / 10.0
    team_value = transfers.get("value", 0) / 10.0
    chips = payload.get("chips")
    chips_json = json.dumps(chips) if chips is not None else None
    conn.execute(
        """INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers,
                                chips_used_json, snapshot_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gw) DO UPDATE SET picks_json=excluded.picks_json, bank=excluded.bank,
             team_value=excluded.team_value, free_transfers=excluded.free_transfers,
             chips_used_json=excluded.chips_used_json, snapshot_at=excluded.snapshot_at""",
        (gw, json.dumps(picks), bank, team_value, free_transfers, chips_json, _now()),
    )
    conn.commit()
```

If `repository.py` doesn't already import `json` at the top, add `import json`. Check with `grep "^import json" src/data/repository.py` first.

- [ ] **Step 2.5: Run the new tests to verify they pass.**

```bash
.venv/bin/pytest tests/test_repository.py -v
```

Expected: all tests in `test_repository.py` PASS.

- [ ] **Step 2.6: Commit.**

```bash
git add src/data/repository.py tests/test_repository.py
git commit -m "$(cat <<'EOF'
feat: snapshot_my_team_authed writes authed payload to my_team

INSERT OR REPLACE under gw=next_gw, so readers' ORDER BY gw DESC LIMIT 1
prefers it over the public-picks row at gw=last_finished. Schema-asserts
on transfers.limit per B6 — fails loudly on FPL drift instead of silently
storing NULL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Scheduler calls the authed path when a key is provided

Extend `refresh_and_recompute` to accept `key=None`. When provided, run the authed snapshot step after the existing public refresh. Authed failures are logged but don't crash the public refresh.

**Files:**
- Modify: `src/scheduler.py` (extend `refresh_and_recompute`)
- Test: `tests/test_scheduler.py` (add new test cases)

- [ ] **Step 3.1: Read the existing `refresh_and_recompute` signature.**

```bash
sed -n '22,40p' src/scheduler.py
```

Expected: the current `refresh_and_recompute(cfg=None, conn=None, client=None, understat_client=None)` body. Note the `cli.refresh` / `fdr.compute_and_store` / `xp.compute_and_store` / `_ping_healthcheck` order.

- [ ] **Step 3.2: Write the failing tests.**

Add at the bottom of `tests/test_scheduler.py`:

```python
def test_refresh_and_recompute_runs_authed_snapshot_when_key_provided(monkeypatch):
    """When key is provided, after public refresh + recompute, the authed path runs."""
    import src.cli as cli
    from src.data import repository
    from src import config as cfg_mod
    from src.auth import session as auth_session
    from src.execution import executor

    monkeypatch.setattr(cli, "refresh", lambda **kw: None)
    monkeypatch.setattr(scheduler.fdr, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler.xp, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler, "_ping_healthcheck", lambda: None)
    monkeypatch.setattr(cfg_mod, "team_id", lambda: 12345)

    fake_session = object()
    monkeypatch.setattr(auth_session, "ensure_session", lambda conn, key: fake_session)

    captured_payload = {"picks": [], "transfers": {"bank": 0, "value": 1000, "limit": 1}, "chips": []}
    monkeypatch.setattr(executor, "fetch_my_team_authed",
                        lambda sess, entry: captured_payload if sess is fake_session and entry == 12345 else None)

    snapshots = []
    monkeypatch.setattr(repository, "snapshot_my_team_authed",
                        lambda conn, gw, payload: snapshots.append((gw, payload)))

    conn = connect(":memory:")
    init_db(conn)
    # Seed a gameweek so next_gw resolves
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    conn.commit()

    scheduler.refresh_and_recompute(cfg={"storage": {"db_path": ":memory:"}}, conn=conn, key=b"unused-key")
    assert snapshots == [(38, captured_payload)]
    conn.close()


def test_refresh_and_recompute_skips_authed_when_key_none(monkeypatch):
    """key=None (the existing public-only path) does NOT touch ensure_session or authed snapshot."""
    import src.cli as cli
    from src.data import repository
    from src.auth import session as auth_session

    monkeypatch.setattr(cli, "refresh", lambda **kw: None)
    monkeypatch.setattr(scheduler.fdr, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler.xp, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler, "_ping_healthcheck", lambda: None)

    called = []
    monkeypatch.setattr(auth_session, "ensure_session", lambda *a, **k: called.append("session") or object())
    monkeypatch.setattr(repository, "snapshot_my_team_authed",
                        lambda *a, **k: called.append("snapshot"))

    conn = connect(":memory:")
    init_db(conn)
    scheduler.refresh_and_recompute(cfg={"storage": {"db_path": ":memory:"}}, conn=conn)  # no key
    assert called == []
    conn.close()


def test_refresh_and_recompute_swallows_authed_failure(monkeypatch):
    """If the authed step raises, the public refresh + recompute still complete; no exception escapes."""
    import src.cli as cli
    from src.auth import session as auth_session
    from src.execution import executor
    from src import config as cfg_mod

    monkeypatch.setattr(cli, "refresh", lambda **kw: None)
    monkeypatch.setattr(scheduler.fdr, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler.xp, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler, "_ping_healthcheck", lambda: None)
    monkeypatch.setattr(cfg_mod, "team_id", lambda: 12345)
    monkeypatch.setattr(auth_session, "ensure_session", lambda *a, **k: object())

    def _boom(sess, entry):
        raise executor.ExecutorError("HTTP 503")
    monkeypatch.setattr(executor, "fetch_my_team_authed", _boom)

    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    conn.commit()

    # MUST NOT raise
    scheduler.refresh_and_recompute(cfg={"storage": {"db_path": ":memory:"}}, conn=conn, key=b"unused")
    conn.close()


def test_refresh_and_recompute_uses_next_gw_not_current(monkeypatch):
    """The authed snapshot is stored under is_next gameweek's id, never the current/finished one."""
    import src.cli as cli
    from src.data import repository
    from src.auth import session as auth_session
    from src.execution import executor
    from src import config as cfg_mod

    monkeypatch.setattr(cli, "refresh", lambda **kw: None)
    monkeypatch.setattr(scheduler.fdr, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler.xp, "compute_and_store", lambda conn: None)
    monkeypatch.setattr(scheduler, "_ping_healthcheck", lambda: None)
    monkeypatch.setattr(cfg_mod, "team_id", lambda: 12345)
    monkeypatch.setattr(auth_session, "ensure_session", lambda *a, **k: object())
    monkeypatch.setattr(executor, "fetch_my_team_authed",
                        lambda sess, entry: {"picks": [], "transfers": {"bank": 0, "value": 1000, "limit": 1}, "chips": []})

    captured = []
    monkeypatch.setattr(repository, "snapshot_my_team_authed",
                        lambda conn, gw, payload: captured.append(gw))

    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) VALUES (37, '2026-05-23T17:30:00Z', 1, 0, 0)")
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) VALUES (38, '2026-05-30T17:30:00Z', 0, 1, 0)")
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) VALUES (39, '2026-06-06T17:30:00Z', 0, 0, 1)")
    conn.commit()

    scheduler.refresh_and_recompute(cfg={"storage": {"db_path": ":memory:"}}, conn=conn, key=b"unused")
    assert captured == [39]  # is_next wins
    conn.close()
```

- [ ] **Step 3.3: Run the new tests to verify they fail.**

```bash
.venv/bin/pytest tests/test_scheduler.py -v -k "authed"
```

Expected: all four FAIL because `refresh_and_recompute` does not yet accept `key`, and even if it did, the authed branch doesn't exist.

- [ ] **Step 3.4: Update `refresh_and_recompute`.**

In `src/scheduler.py`, replace the existing `refresh_and_recompute` function with:

```python
def refresh_and_recompute(cfg=None, conn=None, client=None, understat_client=None, key=None):
    """Public refresh + analytics recompute + healthcheck. With key, also authed my-team snapshot.

    Public path always runs. Authed step is best-effort: failures are logged but do not crash the
    public refresh — the older authed row (or only the public row) stays as fallback.
    """
    from src.cli import refresh
    refresh(cfg=cfg, conn=conn, client=client, understat_client=understat_client, full=False)
    fdr.compute_and_store(conn)
    xp.compute_and_store(conn)
    _ping_healthcheck()
    if key is not None:
        _refresh_authed_my_team(conn, key)


def _refresh_authed_my_team(conn, key):
    """Best-effort: fetch /api/my-team and snapshot it. Never raises."""
    from src.auth import session as auth_session
    from src.execution import executor
    from src.data import repository
    from src import config as cfg_mod
    try:
        next_gw = _next_gw_id(conn)
        if next_gw is None:
            return
        session = auth_session.ensure_session(conn, key)
        payload = executor.fetch_my_team_authed(session, cfg_mod.team_id())
        repository.snapshot_my_team_authed(conn, next_gw, payload)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        import logging
        logging.getLogger(__name__).warning("authed my-team snapshot failed: %s", exc)


def _next_gw_id(conn):
    row = conn.execute(
        "SELECT id FROM gameweeks WHERE is_next=1 LIMIT 1"
    ).fetchone()
    if row is not None:
        return row["id"]
    row = conn.execute(
        "SELECT MIN(id) AS id FROM gameweeks WHERE finished=0"
    ).fetchone()
    return row["id"] if row else None
```

Confirm that `import` order at the top of `scheduler.py` is unchanged — the new helpers do lazy imports to avoid widening the module's import surface.

- [ ] **Step 3.5: Run the new tests.**

```bash
.venv/bin/pytest tests/test_scheduler.py -v
```

Expected: all existing scheduler tests still PASS plus the four new ones.

- [ ] **Step 3.6: Commit.**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat: refresh_and_recompute(key=...) runs authed my-team snapshot

When the master key is loaded (unattended mode), the scheduler also
fetches /api/my-team and snapshots it under gw=next_gw. Authed failures
are caught + logged so the public refresh always completes; readers fall
back to the (older) authed row or to the public row gracefully.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `build_scheduler` threads the key into the job

The job partial in `build_scheduler` currently doesn't receive a `key`. Thread it so unattended mode actually calls the authed path.

**Files:**
- Modify: `src/scheduler.py` (the `build_scheduler` function)
- Test: `tests/test_scheduler.py` (add a test)

- [ ] **Step 4.1: Read the current `build_scheduler`.**

```bash
sed -n '47,75p' src/scheduler.py
```

Expected: the function adds `weekly_refresh` and `hourly_refresh` jobs pointing at `refresh_and_recompute`. Note whether `key` is currently passed.

- [ ] **Step 4.2: Write the failing test.**

Add at the bottom of `tests/test_scheduler.py`:

```python
def test_build_scheduler_passes_key_to_refresh_jobs(monkeypatch):
    """Both refresh jobs should receive key as a kwarg so the authed branch runs unattended."""
    sched = scheduler.build_scheduler(key=b"my-key")
    jobs = {j.id: j for j in sched.get_jobs()}
    for jid in ("weekly_refresh", "hourly_refresh"):
        # APScheduler stores kwargs on the job; this is the canonical place to read them
        assert jobs[jid].kwargs.get("key") == b"my-key", f"{jid} did not receive key kwarg"


def test_build_scheduler_no_key_means_no_key_kwarg():
    """When build_scheduler is called without a key, jobs run the public-only path."""
    sched = scheduler.build_scheduler()  # default key=None
    jobs = {j.id: j for j in sched.get_jobs()}
    for jid in ("weekly_refresh", "hourly_refresh"):
        # Either no kwarg at all, or key=None — both are fine
        assert jobs[jid].kwargs.get("key") is None
```

- [ ] **Step 4.3: Run the new tests to verify they fail.**

```bash
.venv/bin/pytest tests/test_scheduler.py::test_build_scheduler_passes_key_to_refresh_jobs -v
```

Expected: FAIL — `kwargs.get("key")` returns `None` even when `b"my-key"` was passed.

- [ ] **Step 4.4: Update `build_scheduler`.**

In `src/scheduler.py`, modify `build_scheduler` so the two `add_job` calls pass `key` as a kwarg. The exact diff depends on the current code; the change is to update both `add_job(...)` calls to include `kwargs={"key": key}`.

For example, where the current code reads:
```python
scheduler.add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0),
                  id="weekly_refresh")
scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                  id="hourly_refresh")
```
Change to:
```python
scheduler.add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0),
                  id="weekly_refresh", kwargs={"key": key})
scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                  id="hourly_refresh", kwargs={"key": key})
```

If the function signature is already `build_scheduler(scheduler=None, key=None)`, leave it. If not, add `key=None`.

- [ ] **Step 4.5: Run the tests.**

```bash
.venv/bin/pytest tests/test_scheduler.py -v
```

Expected: all tests PASS.

- [ ] **Step 4.6: Commit.**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat: build_scheduler threads key into the refresh jobs

So unattended mode (the only path that loads the master key today) runs
the authed my-team snapshot too. Without this, the new key param on
refresh_and_recompute would never be exercised in production.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `refresh-my-team` CLI command

User-facing entry to trigger the authed snapshot once (without starting the full daemon). Prompts for master password.

**Files:**
- Modify: `src/cli.py` (add new command)
- Test: `tests/test_cli_refresh.py` (add new test cases — same file as the existing `refresh` tests)

- [ ] **Step 5.1: Examine the existing `refresh` command for the CLI pattern.**

```bash
grep -n "^def refresh\|^def init_fpl\|click.command\|argh" src/cli.py | head -20
```

Expected: spots the click/argh decorator style used. Use the same.

- [ ] **Step 5.2: Examine an existing `master.unlock` flow.**

```bash
grep -n "master.unlock\|master\.load" src/cli.py | head -10
```

Expected: an existing command (e.g. `init_fpl` or `execute_lineup`) calls `master.unlock(getpass.getpass(...))` or similar. Mirror that pattern.

- [ ] **Step 5.3: Write the failing tests.**

Add at the bottom of `tests/test_cli_refresh.py`:

```python
def test_refresh_my_team_writes_authed_row(db, monkeypatch, capsys):
    """refresh-my-team unlocks the master key, calls authed snapshot, prints summary."""
    import src.cli as cli
    from src.auth import master, session as auth_session
    from src.execution import executor
    from src import config as cfg_mod

    monkeypatch.setattr(master, "unlock", lambda pw: b"key")
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "pw")
    monkeypatch.setattr(auth_session, "ensure_session", lambda conn, key: object())
    monkeypatch.setattr(cfg_mod, "team_id", lambda: 12345)
    monkeypatch.setattr(executor, "fetch_my_team_authed",
                        lambda sess, entry: {"picks": [{"element": 1, "position": 1,
                                                        "is_captain": True, "is_vice_captain": False,
                                                        "selling_price": 50, "purchase_price": 50, "multiplier": 2}],
                                              "transfers": {"bank": 0, "value": 1000, "limit": 1},
                                              "chips": []})

    # Seed next_gw
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.commit()

    cli.refresh_my_team(conn=db)
    row = db.execute("SELECT free_transfers FROM my_team WHERE gw=38").fetchone()
    assert row is not None and row["free_transfers"] == 1

    out = capsys.readouterr().out
    assert "GW38" in out and "FT=1" in out


def test_refresh_my_team_surfaces_session_expired(db, monkeypatch, capsys):
    """If ensure_session raises, the command surfaces the error and exits non-zero."""
    import src.cli as cli
    from src.auth import master, session as auth_session

    monkeypatch.setattr(master, "unlock", lambda pw: b"key")
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "pw")

    class SessionExpired(Exception):
        pass
    monkeypatch.setattr(auth_session, "ensure_session",
                        lambda *a, **k: (_ for _ in ()).throw(SessionExpired("token bad")))

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.commit()

    with pytest.raises(SystemExit) as exc_info:
        cli.refresh_my_team(conn=db)
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "session" in err.lower() or "token" in err.lower()
```

- [ ] **Step 5.4: Run the failing tests.**

```bash
.venv/bin/pytest tests/test_cli_refresh.py -v -k "my_team"
```

Expected: both FAIL — `refresh_my_team` not defined.

- [ ] **Step 5.5: Add the `refresh_my_team` CLI command.**

In `src/cli.py`, add the new command. Match the existing pattern in the file (click/argh decorators, parameter conventions). The function body should be approximately:

```python
def refresh_my_team(*, conn=None):
    """Fetch /api/my-team (authed) once and snapshot it. Prompts for master password.

    Use this when not running the daemon but you want the dashboard / executor to see the
    upcoming-GW squad and real free_transfers.
    """
    import getpass, sys
    from src.auth import master, session as auth_session
    from src.execution import executor
    from src.data import repository
    from src.scheduler import _next_gw_id
    from src import config as cfg_mod

    if conn is None:
        from src.data.db import connect
        conn = connect(config.db_path())

    try:
        key = master.unlock(getpass.getpass("Master password: "))
    except Exception as exc:
        print(f"could not unlock master key: {exc}", file=sys.stderr)
        raise SystemExit(2)

    next_gw = _next_gw_id(conn)
    if next_gw is None:
        print("no upcoming gameweek — run `refresh` first", file=sys.stderr)
        raise SystemExit(1)

    try:
        sess = auth_session.ensure_session(conn, key)
        payload = executor.fetch_my_team_authed(sess, cfg_mod.team_id())
    except Exception as exc:
        print(f"authed my-team fetch failed (session/network): {exc}", file=sys.stderr)
        raise SystemExit(1)

    repository.snapshot_my_team_authed(conn, next_gw, payload)
    ft = payload.get("transfers", {}).get("limit")
    print(f"my_team OK (authed, GW{next_gw}, FT={ft})")
```

Register it with whatever CLI decorator/entrypoint the file uses (mirror the registration pattern used for `refresh`). If `cli.py` already imports `config`, leave that; otherwise the top of the file already has it per existing commands.

- [ ] **Step 5.6: Run the new tests.**

```bash
.venv/bin/pytest tests/test_cli_refresh.py -v
```

Expected: all PASS.

- [ ] **Step 5.7: Commit.**

```bash
git add src/cli.py tests/test_cli_refresh.py
git commit -m "$(cat <<'EOF'
feat: refresh-my-team CLI for one-shot authed snapshot

Prompts for master password, unlocks the key, fetches /api/my-team, and
writes the row. Use when running the dashboard interactively without the
full daemon. Daemon already does this on every hourly refresh.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `_latest_squad` returns 3-tuple; `get_transfer_suggestions` propagates `free_transfers`

Make the decision layer surface the authed `free_transfers` so downstream callers can preflight on it. Hit math stays unchanged (non-goal per spec).

**Files:**
- Modify: `src/decisions/transfers.py`
- Test: `tests/test_transfers.py`

- [ ] **Step 6.1: Read the existing `_latest_squad` and `get_transfer_suggestions`.**

```bash
sed -n '120,170p' src/decisions/transfers.py
```

Expected: matches the spec — `_latest_squad` returns `(ids, bank)`, `get_transfer_suggestions` returns `{"suggestions": [...], "empty_reason": ...}`.

- [ ] **Step 6.2: Write the failing tests.**

Add to `tests/test_transfers.py`:

```python
def test_latest_squad_returns_free_transfers(db, load):
    """When the latest my_team row has free_transfers, _latest_squad returns it."""
    from src.decisions import transfers
    # Seed gameweeks
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[{\"element\": 1}, {\"element\": 2}]', 0.5, 2, 't')")
    db.commit()
    ids, bank, ft = transfers._latest_squad(db)
    assert ids == [1, 2]
    assert bank == 0.5
    assert ft == 2


def test_latest_squad_returns_none_free_transfers_when_null(db):
    """Public-only row has NULL free_transfers; _latest_squad returns None for that field."""
    from src.decisions import transfers
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (37, '[{\"element\": 1}]', 0.0, NULL, 't')")
    db.commit()
    ids, bank, ft = transfers._latest_squad(db)
    assert ft is None


def test_get_transfer_suggestions_includes_free_transfers(db, load):
    """The top-level dict from get_transfer_suggestions carries free_transfers through."""
    from src.decisions import transfers
    # Reuse the existing _seed_for_reader fixture-builder used elsewhere in this file; otherwise inline
    # the minimum: one player, one xp row, one gameweek, one my_team row with ft.
    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[]', 0.0, 3, 't')")
    db.commit()
    result = transfers.get_transfer_suggestions(db)
    assert "free_transfers" in result
    assert result["free_transfers"] == 3
```

If the file already has a helper that seeds `gameweeks`+`my_team` (see existing `_seed_for_reader` around line 54), prefer using it and just pass `free_transfers=3`.

- [ ] **Step 6.3: Run the failing tests.**

```bash
.venv/bin/pytest tests/test_transfers.py -v -k "free_transfers"
```

Expected: all three FAIL.

- [ ] **Step 6.4: Update `_latest_squad`.**

In `src/decisions/transfers.py`, replace `_latest_squad` with:

```python
def _latest_squad(conn):
    """Latest my_team snapshot -> (element_ids, bank, free_transfers), or None when no snapshot.

    free_transfers is int when an authed row exists, None when only a public-picks row is present.
    """
    row = conn.execute(
        "SELECT picks_json, bank, free_transfers FROM my_team ORDER BY gw DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ids = [pick["element"] for pick in json.loads(row["picks_json"])]
    bank = row["bank"] if row["bank"] is not None else 0.0
    ft = row["free_transfers"]  # int | None
    return ids, bank, ft
```

- [ ] **Step 6.5: Update `get_transfer_suggestions` to unpack the new tuple + propagate FT.**

Replace the body that currently reads:
```python
squad_ids, bank = squad
```
with:
```python
squad_ids, bank, free_transfers = squad
```

And replace the final `return` line:
```python
return {"suggestions": suggestions, "empty_reason": None if suggestions else EMPTY_REASON}
```
with:
```python
return {"suggestions": suggestions,
        "empty_reason": None if suggestions else EMPTY_REASON,
        "free_transfers": free_transfers}
```

Also update the early-return branch (`if next_gw is None or squad is None`) to include the key:
```python
return {"suggestions": [], "empty_reason": EMPTY_REASON, "free_transfers": None}
```

- [ ] **Step 6.6: Run the tests.**

```bash
.venv/bin/pytest tests/test_transfers.py -v
```

Expected: all tests PASS, including the three new ones AND all existing transfers tests (verify the unpacking change didn't break anything).

- [ ] **Step 6.7: Commit.**

```bash
git add src/decisions/transfers.py tests/test_transfers.py
git commit -m "$(cat <<'EOF'
feat: _latest_squad returns free_transfers; suggestions propagate it

Hit math (suggest_transfers, hit_cost) is unchanged for this slice — see
spec non-goals. This is just the wire so downstream callers (run_transfer
preflight, dashboard read-model) can see the real FT count.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `run_transfer` preflight refuses live transfer when `free_transfers=0`

Wire the safety net. Dry-run is never blocked. Live without `allow_hit` is blocked when `free_transfers=0`. `free_transfers=None` is treated as "unknown" — warn and proceed (we can't second-guess when the snapshot is missing).

**Files:**
- Modify: `src/execution/transfer.py` (`run_transfer` only — `run_undo_transfer` is unchanged because undo is reversal, not consumption)
- Test: `tests/test_transfer.py`

- [ ] **Step 7.1: Re-read the current `run_transfer`.**

```bash
sed -n '8,49p' src/execution/transfer.py
```

Expected: matches what the spec shows. Note the existing `live and (confirm_fn is None or not confirm_fn(diff))` branch — your preflight sits BEFORE that.

- [ ] **Step 7.2: Write the failing tests.**

Add to `tests/test_transfer.py`:

```python
def test_run_transfer_refuses_live_when_free_transfers_zero_and_no_allow_hit(db, monkeypatch):
    """Live + free_transfers=0 + allow_hit=False -> refused, no POST, logged."""
    from src.execution import transfer

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[]', 0.0, 0, 't')")
    db.commit()

    posted = []
    class _Sess:
        def get(self, url, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda s: {"picks": [{"element": 1, "selling_price": 50}]}})()
        def post(self, *a, **kw):
            posted.append(1)
            return type("R", (), {"status_code": 200})()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "A", "price": 5.0, "status": "a"},
                                  "in":  {"player_id": 2, "web_name": "B", "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 1.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None, "free_transfers": 0}

    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=_Sess(), suggester=_suggester)
    assert posted == []  # never reached the POST
    assert res.ok is False
    row = db.execute("SELECT action_taken FROM activity_log").fetchone()
    assert "refused" in row["action_taken"].lower()


def test_run_transfer_allows_when_allow_hit_true(db, monkeypatch):
    """Same setup but allow_hit=True -> proceeds to live POST."""
    from src.execution import transfer

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[]', 0.0, 0, 't')")
    db.commit()

    posted = []
    class _Sess:
        def get(self, url, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda s: {"picks": [{"element": 1, "selling_price": 50}]}})()
        def post(self, *a, **kw):
            posted.append(1)
            return type("R", (), {"status_code": 200})()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "A", "price": 5.0, "status": "a"},
                                  "in":  {"player_id": 2, "web_name": "B", "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 1.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None, "free_transfers": 0}

    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=_Sess(), suggester=_suggester, allow_hit=True)
    assert posted == [1]
    assert res.ok is True


def test_run_transfer_dry_run_never_blocked_by_preflight(db):
    """Even with free_transfers=0, dry-run runs to completion (observational)."""
    from src.execution import transfer

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[]', 0.0, 0, 't')")
    db.commit()

    class _Sess:
        def get(self, url, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda s: {"picks": [{"element": 1, "selling_price": 50}]}})()
        def post(self, *a, **kw):
            raise AssertionError("dry-run must not POST")

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "A", "price": 5.0, "status": "a"},
                                  "in":  {"player_id": 2, "web_name": "B", "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 1.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None, "free_transfers": 0}

    res = transfer.run_transfer(db, key=b"unused", live=False, session=_Sess(), suggester=_suggester)
    assert res.dry_run is True  # reached the executor's dry-run branch normally


def test_run_transfer_proceeds_when_ft_positive(db):
    """free_transfers=1 -> proceeds without needing allow_hit."""
    from src.execution import transfer

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[]', 0.0, 1, 't')")
    db.commit()

    posted = []
    class _Sess:
        def get(self, url, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda s: {"picks": [{"element": 1, "selling_price": 50}]}})()
        def post(self, *a, **kw):
            posted.append(1)
            return type("R", (), {"status_code": 200})()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "A", "price": 5.0, "status": "a"},
                                  "in":  {"player_id": 2, "web_name": "B", "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 1.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None, "free_transfers": 1}

    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=_Sess(), suggester=_suggester)
    assert posted == [1]
    assert res.ok is True


def test_run_transfer_proceeds_when_ft_unknown(db):
    """free_transfers=None (no authed snapshot yet) -> proceeds with warning logged."""
    from src.execution import transfer

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    # Public-only my_team row (no authed snapshot) — free_transfers NULL.
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (37, '[]', 0.0, NULL, 't')")
    db.commit()

    posted = []
    class _Sess:
        def get(self, url, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda s: {"picks": [{"element": 1, "selling_price": 50}]}})()
        def post(self, *a, **kw):
            posted.append(1)
            return type("R", (), {"status_code": 200})()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "A", "price": 5.0, "status": "a"},
                                  "in":  {"player_id": 2, "web_name": "B", "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 1.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None, "free_transfers": None}

    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=_Sess(), suggester=_suggester)
    assert posted == [1]
    assert res.ok is True
    # Activity log entry should record that FT was unknown so the user can audit.
    row = db.execute("SELECT inputs FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
    import json as _json
    assert _json.loads(row["inputs"]).get("free_transfers") is None
```

- [ ] **Step 7.3: Run the failing tests.**

```bash
.venv/bin/pytest tests/test_transfer.py -v
```

Expected: the new tests FAIL (no preflight yet); existing tests still PASS.

- [ ] **Step 7.4: Add the preflight + `allow_hit` parameter to `run_transfer`.**

In `src/execution/transfer.py`, change the `run_transfer` signature and add the preflight block. Replace the full function:

```python
def run_transfer(conn, key, *, rank=1, live=False, confirm_fn=None, session=None,
                 suggester=None, allow_hit=False):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    suggestions = sugg["suggestions"]
    free_transfers = sugg.get("free_transfers")
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
              "alternatives": [s for i, s in enumerate(suggestions) if i != rank - 1],
              "free_transfers": free_transfers}
    url = executor.TRANSFERS_URL.format(entry=entry)

    # Preflight: refuse live -4 hits unless explicitly opted in. Dry-run is observational, never blocked.
    if live and free_transfers == 0 and not allow_hit:
        repository.log_activity(conn, decision_type="transfer", mode="manual",
                                action_taken="refused: would cost -4 hit (free_transfers=0)",
                                inputs=inputs, executed=False,
                                exec_outcome={"diff": diff, "free_transfers": 0})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload,
                                            "note": "refused: would cost -4 hit"},
                                   status=None, ok=False)

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

- [ ] **Step 7.5: Run the tests.**

```bash
.venv/bin/pytest tests/test_transfer.py -v
```

Expected: all PASS (new and existing).

- [ ] **Step 7.6: Commit.**

```bash
git add src/execution/transfer.py tests/test_transfer.py
git commit -m "$(cat <<'EOF'
feat: run_transfer preflight refuses silent -4 hits

When free_transfers=0 and the user did not pass --allow-hit, refuse a
live transfer with a clear activity_log entry. Dry-run is never blocked
(observational). free_transfers=None (no authed snapshot yet) proceeds
with a warning — we can't second-guess when data is missing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Deadguard `_pick_flagged_transfer` refuses when `free_transfers=0`

B8: deadguard never takes a hit. Today the function already gates `≤1` per its own conf threshold — but it doesn't know the real FT count. Now it will.

**Files:**
- Modify: `src/interface/deadguard.py` (`_pick_flagged_transfer`)
- Test: `tests/test_deadguard.py`

- [ ] **Step 8.1: Confirm the current `_pick_flagged_transfer` shape.**

The function lives at `src/interface/deadguard.py:227`. Current signature: `_pick_flagged_transfer(conn, cfg)`. It returns an **int (1-based rank into `sugg["suggestions"]`)** or `None`. Verified with:

```bash
sed -n '227,240p' src/interface/deadguard.py
```

The caller pattern is `rank = _pick_flagged_transfer(conn, cfg)` at line 119. Our preflight must keep the same return contract — int rank or `None`. We will add an optional `suggester=None` kwarg following the dependency-injection style used by `run_lineup`/`run_transfer`, so tests can stub the suggestions.

- [ ] **Step 8.2: Write the failing tests.**

Add at the bottom of `tests/test_deadguard.py`:

```python
def test_pick_flagged_transfer_refuses_when_ft_zero(db):
    """B8: deadguard never takes a hit. Even a strong suggestion is dropped if FT=0."""
    from src.interface import deadguard

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    # Seed the flagged player so _player_status returns "i" (injured)
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (1, 'Flagged', 3, 1, 5.0, 'i')")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[{\"element\": 1}]', 0.0, 0, 't')")
    db.commit()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "Flagged", "price": 5.0, "status": "i"},
                                  "in":  {"player_id": 2, "web_name": "Star",    "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 4.0, "hit_cost": 0, "confidence": 85}],
                "empty_reason": None, "free_transfers": 0}

    cfg = {"deadguard": {"transfer_if_flagged": True, "min_ep_delta": 3.0, "confidence_floor": 75}}
    result = deadguard._pick_flagged_transfer(db, cfg, suggester=_suggester)
    assert result is None  # would have been rank=1 but for FT=0


def test_pick_flagged_transfer_refuses_when_ft_unknown(db):
    """free_transfers=None (no authed snapshot) -> deadguard refuses (safer than guessing)."""
    from src.interface import deadguard

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (1, 'Flagged', 3, 1, 5.0, 'i')")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (37, '[{\"element\": 1}]', 0.0, NULL, 't')")  # public-only row
    db.commit()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "Flagged", "price": 5.0, "status": "i"},
                                  "in":  {"player_id": 2, "web_name": "Star",    "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 4.0, "hit_cost": 0, "confidence": 85}],
                "empty_reason": None, "free_transfers": None}

    cfg = {"deadguard": {"transfer_if_flagged": True, "min_ep_delta": 3.0, "confidence_floor": 75}}
    result = deadguard._pick_flagged_transfer(db, cfg, suggester=_suggester)
    assert result is None


def test_pick_flagged_transfer_still_returns_rank_when_ft_positive(db):
    """Happy path preserved: returns int rank 1 when FT >= 1 and the suggestion passes the existing gates."""
    from src.interface import deadguard

    db.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
               "VALUES (38, '2026-05-30T17:30:00Z', 0, 0, 1)")
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (1, 'Flagged', 3, 1, 5.0, 'i')")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, free_transfers, snapshot_at) "
               "VALUES (38, '[{\"element\": 1}]', 0.0, 1, 't')")
    db.commit()

    def _suggester(conn):
        return {"suggestions": [{"out": {"player_id": 1, "web_name": "Flagged", "price": 5.0, "status": "i"},
                                  "in":  {"player_id": 2, "web_name": "Star",    "price": 5.0, "status": "a"},
                                  "ep_delta_5gw": 4.0, "hit_cost": 0, "confidence": 85}],
                "empty_reason": None, "free_transfers": 1}

    cfg = {"deadguard": {"transfer_if_flagged": True, "min_ep_delta": 3.0, "confidence_floor": 75}}
    result = deadguard._pick_flagged_transfer(db, cfg, suggester=_suggester)
    assert result == 1  # int rank, not a dict
```

Note the test imports `deadguard` and calls `_player_status` indirectly — make sure the `players` row exists with `status='i'` or the existing `_player_status` will not return `'i'`. The `cfg` arg is the dict-shaped config that the production `config.deadguard_*` accessors read. If those accessors do dotted lookups (e.g. `cfg["deadguard"]["transfer_if_flagged"]`), the test cfg shape is correct.

- [ ] **Step 8.3: Run the failing tests.**

```bash
.venv/bin/pytest tests/test_deadguard.py -v -k "pick_flagged_transfer"
```

Expected: the new tests FAIL with `TypeError: _pick_flagged_transfer() got an unexpected keyword argument 'suggester'` (the kwarg doesn't exist yet) and/or wrong return values.

- [ ] **Step 8.4: Update `_pick_flagged_transfer`.**

Replace the existing function body with the suggester-injection + preflight version:

```python
def _pick_flagged_transfer(conn, cfg, *, suggester=None):
    """1-based rank of the first transfer suggestion that replaces a FLAGGED squad player with a
    free, high-EP upgrade, or None. Guards (all required): OUT status not in ('a','d'); hit_cost>=0
    (free); ep_delta_5gw >= min_ep; confidence >= floor. Additionally B8: refuses when
    free_transfers is 0 or unknown (None) — deadguard never takes a hit.
    """
    if not config.deadguard_transfer_if_flagged(cfg):
        return None
    min_ep = config.deadguard_min_ep_delta(cfg)
    floor = config.deadguard_confidence_floor(cfg)
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    free_transfers = sugg.get("free_transfers")
    if not isinstance(free_transfers, int) or free_transfers < 1:
        return None  # B8: refuse on FT=0 and unknown (None); safer default
    for i, s in enumerate(sugg["suggestions"], start=1):
        if (_player_status(conn, s["out"]["player_id"]) not in ("a", "d")
                and s["hit_cost"] >= 0 and s["ep_delta_5gw"] >= min_ep and s["confidence"] >= floor):
            return i
    return None
```

The caller at `src/interface/deadguard.py:119` (`rank = _pick_flagged_transfer(conn, cfg)`) is unchanged — the new `suggester` kwarg is keyword-only and defaults to the production function.

- [ ] **Step 8.5: Update `docs/deadguard.md`.**

Add a one-line note in the `_pick_flagged_transfer` rules section: "Refuses when `free_transfers == 0` or unknown (no authed snapshot)."

- [ ] **Step 8.6: Run the tests.**

```bash
.venv/bin/pytest tests/test_deadguard.py -v
```

Expected: all PASS (new and existing).

- [ ] **Step 8.7: Commit.**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py docs/deadguard.md
git commit -m "$(cat <<'EOF'
feat: deadguard _pick_flagged_transfer refuses when FT=0 / unknown

B8: deadguard never takes a hit. Now wired with the real free_transfers
count from the authed my-team snapshot. Unknown FT (no authed row yet)
also refuses — safer than guessing in the safety-net layer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Dashboard `get_squad` returns the authed row when present (verification + docs)

`get_squad` already does `ORDER BY gw DESC LIMIT 1`, so this is mostly a verification task plus the doc update.

**Files:**
- Modify (verify only): `src/interface/queries.py`
- Test: `tests/test_queries.py` (add new test)
- Modify: `docs/api-contract.md`, `docs/runbook.md`

- [ ] **Step 9.1: Confirm the current `get_squad` reads `free_transfers`.**

```bash
sed -n '56,90p' src/interface/queries.py
```

Expected: the SELECT clause already includes `free_transfers`. If not, add it (column already exists in the schema).

- [ ] **Step 9.2: Write the failing test.**

Add to `tests/test_queries.py` (create the file if it doesn't exist):

```python
import json
from src.interface import queries


def test_get_squad_prefers_authed_row_over_public(db):
    """Two my_team rows present (public at gw=37, authed at gw=38) — get_squad returns the authed."""
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (37, '[{\"element\": 1}]', 0.0, 100.0, NULL, 't')")
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (38, '[{\"element\": 2}]', 0.5, 100.5, 2, 't')")
    db.commit()
    # Seed minimal players so the JOIN in get_squad doesn't return empty
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (2, 'Star', 3, 1, 5.0, 'a')")
    db.commit()

    result = queries.get_squad(db)
    assert result["gw"] == 38
    assert result["free_transfers"] == 2
    assert any(p["player_id"] == 2 for p in result["players"])


def test_get_squad_falls_back_to_public_when_only_public(db):
    """Only a public row present -> get_squad returns it with free_transfers=None."""
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers, snapshot_at) "
               "VALUES (37, '[{\"element\": 1}]', 0.0, 100.0, NULL, 't')")
    db.execute("INSERT INTO players (id, web_name, position, team_id, price, status) "
               "VALUES (1, 'Solo', 3, 1, 5.0, 'a')")
    db.commit()

    result = queries.get_squad(db)
    assert result["gw"] == 37
    assert result["free_transfers"] is None
```

If `tests/test_queries.py` already exists, add the tests at the bottom and reuse its existing imports.

- [ ] **Step 9.3: Run the tests.**

```bash
.venv/bin/pytest tests/test_queries.py -v
```

Expected: PASS (since the SELECT shape already works). If they fail because `get_squad` doesn't return `free_transfers`, add it to the function's return dict — the column is already SELECTed.

- [ ] **Step 9.4: Update `docs/api-contract.md`.**

Find the section documenting `GET /api/status` and `GET /api/squad` (or wherever the response shape is documented per `HANDOFF.md`'s mention of `api-contract.md`). Update the example payload so `free_transfers` is `2` (not `null`), and add a one-line note: "`free_transfers` is non-null when the authed my-team snapshot has been taken (unattended mode or `refresh-my-team` CLI). Null means only public-picks data is available."

- [ ] **Step 9.5: Update `docs/runbook.md`.**

Find the operator-commands section. Add a row for `refresh-my-team`:
- Command: `fpl-autopilot refresh-my-team`
- What it does: "One-shot authed `/api/my-team` snapshot. Updates the dashboard's view to the upcoming-GW squad and writes real `free_transfers` for the transfer executor's preflight."
- When to use: "Run before opening the dashboard if not in unattended mode. The daemon does this automatically every hour."

- [ ] **Step 9.6: Run the FULL test suite to confirm green.**

```bash
.venv/bin/pytest -q
```

Expected: more than the baseline 404 (the new tests add roughly 18–22 cases). All green.

- [ ] **Step 9.7: Frontend tests stay green (no change there, but verify nothing leaked).**

```bash
cd frontend && npm test
```

Expected: 50 passed (the baseline from `HANDOFF.md`). Then `cd ..`.

- [ ] **Step 9.8: Commit docs + verification test.**

```bash
git add docs/api-contract.md docs/runbook.md tests/test_queries.py src/interface/queries.py
git commit -m "$(cat <<'EOF'
docs+test: api-contract/runbook reflect non-null free_transfers; verify get_squad

get_squad already ORDER BY gw DESC LIMIT 1, so the authed row at
gw=next_gw naturally wins over the public row at gw=last_finished. Tests
verify both the preference and the fallback. api-contract.md and
runbook.md updated to mention refresh-my-team and the new shape.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final task: full-suite sanity, HANDOFF.md update, ready-for-review

- [ ] **Step F.1: Confirm full pytest + frontend tests are green.**

```bash
.venv/bin/pytest -q && (cd frontend && npm test --silent)
```

Expected: all green.

- [ ] **Step F.2: Run a manual smoke (optional but recommended).**

If you have a live FPL session set up locally:
```bash
.venv/bin/python -m src.cli refresh
.venv/bin/python -m src.cli refresh-my-team
sqlite3 data/fpl.db "SELECT gw, free_transfers FROM my_team ORDER BY gw DESC LIMIT 3;"
```

Expected: two rows — `gw=last_finished free_transfers=NULL` and `gw=next_gw free_transfers={1 or 2}`.

- [ ] **Step F.3: Update `HANDOFF.md` to mark findings #1 and #4 as resolved.**

Open `docs/superpowers/HANDOFF.md` and edit the "Findings / backlog" section: prefix #1 and #4 with `~~RESOLVED~~` (or remove them entirely if you prefer; project pattern from past commits is to keep the strike-through for audit trail). Add a short paragraph after that block referring to this slice.

- [ ] **Step F.4: Commit the HANDOFF update.**

```bash
git add docs/superpowers/HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: handoff — authed read-model wiring done; findings #1 + #4 resolved

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step F.5: Final review hook.**

Per project convention (`HANDOFF.md` working-conventions section), security/exec slices get a final Opus review. This slice touches the executor preflight and the scheduler — that qualifies. Dispatch a fresh Opus review subagent with:

- The spec (`docs/superpowers/specs/2026-05-26-authed-read-model-wiring-design.md`)
- The diff (`git diff main...feat/authed-read-model-wiring`)
- Ask for: B-rule audit (B2/B4/B6/B7/B8/B11), silent-failure check on the new authed branch in scheduler, and confirmation that the preflight cannot be bypassed by a malformed `suggester` response.

Apply review fixes if any, commit them, then proceed to merge.

- [ ] **Step F.6: Merge to main (locally; push only when the user asks).**

```bash
git checkout main
git merge --no-ff feat/authed-read-model-wiring -m "Merge feat/authed-read-model-wiring"
git log --oneline -5
```

Stop and report back. The user controls `git push`.

---

## Spec coverage matrix

| Spec requirement | Task |
|---|---|
| `executor.fetch_my_team_authed` | Task 1 |
| `repository.snapshot_my_team_authed` | Task 2 |
| Scheduler runs authed path with `key`, fails gracefully | Task 3 |
| `build_scheduler` threads `key` | Task 4 |
| `refresh-my-team` CLI | Task 5 |
| `_latest_squad` 3-tuple + `get_transfer_suggestions` propagation | Task 6 |
| `run_transfer` preflight + `allow_hit` | Task 7 |
| Deadguard `_pick_flagged_transfer` refusal | Task 8 |
| `get_squad` returns authed row + docs | Task 9 |
| `docs/api-contract.md` updated | Task 9 |
| `docs/runbook.md` updated | Task 9 |
| `docs/deadguard.md` updated | Task 8 |
| No `decision-engine.md` change (B4) | Verified: no task touches it |
| No schema migration | Verified: no task adds/alters columns |

## Files matrix

| File | Modified in |
|---|---|
| `src/execution/executor.py` | Task 1 |
| `src/data/repository.py` | Task 2 |
| `src/scheduler.py` | Tasks 3, 4 |
| `src/cli.py` | Task 5 |
| `src/decisions/transfers.py` | Task 6 |
| `src/execution/transfer.py` | Task 7 |
| `src/interface/deadguard.py` | Task 8 |
| `src/interface/queries.py` | Task 9 (verify; only if missing `free_transfers` already) |
| `tests/test_executor.py` | Task 1 |
| `tests/test_repository.py` | Task 2 |
| `tests/test_scheduler.py` | Tasks 3, 4 |
| `tests/test_cli_refresh.py` | Task 5 |
| `tests/test_transfers.py` | Task 6 |
| `tests/test_transfer.py` | Task 7 |
| `tests/test_deadguard.py` | Task 8 |
| `tests/test_queries.py` | Task 9 |
| `docs/deadguard.md` | Task 8 |
| `docs/api-contract.md` | Task 9 |
| `docs/runbook.md` | Task 9 |
| `docs/superpowers/HANDOFF.md` | Final |
