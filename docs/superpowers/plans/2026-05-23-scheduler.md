# Scheduler (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an in-process APScheduler that periodically refreshes FPL/Understat data and recomputes FDR + xP, started by `fpl-autopilot serve` (or standalone `fpl-autopilot scheduler`).

**Architecture:** `src/scheduler.py` exposes one job `refresh_and_recompute` (reuses `cli.refresh` + `fdr/xp.compute_and_store`) and `build_scheduler` (registers weekly + hourly cron jobs on a `BackgroundScheduler`, returns it un-started). `serve` starts it in-process; a `scheduler` command runs it blocking. In-memory job store (jobs are code-defined).

**Tech Stack:** Python 3.11+, APScheduler, `requests`, `pytest`. `.venv` exists; `src/` is the package.

**Spec:** `docs/superpowers/specs/2026-05-23-scheduler-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` / `requirements.txt` | add `APScheduler`. |
| `docs/architecture.md` | Phase-1 scheduler note (in-memory store) (B13). |
| `src/scheduler.py` | `refresh_and_recompute`, `_ping_healthcheck`, `build_scheduler`, `run_scheduler_blocking`. |
| `src/cli.py` | `serve` starts scheduler (+`--no-scheduler`); `scheduler` subcommand. |
| `tests/test_scheduler.py` | job registration, pipeline order, healthcheck, serve wiring. |

---

## Task 1: Deps + architecture.md note

**Files:** Modify `pyproject.toml`, `requirements.txt`, `docs/architecture.md`

- [ ] **Step 1: Add APScheduler to `pyproject.toml`.** Change the dependencies line to:

```toml
dependencies = ["requests", "pydantic>=2", "pyyaml", "fastapi", "uvicorn", "APScheduler"]
```

- [ ] **Step 2: Append `APScheduler` to `requirements.txt`.**

- [ ] **Step 3: Add the Phase-1 note to `docs/architecture.md`.** Find the `## Scheduling` heading. Immediately AFTER the closing ``` of the schedule code block under it (i.e., after the `H-30 minutes (Phase 2): ... run deadguard.` block fence), insert:

```markdown
**Phase 1 implementation (2026-05-23):** the implemented scheduler runs only `scheduler.refresh_and_recompute` (FPL/Understat refresh + FDR/xP recompute) on two cron triggers — weekly (Tue 03:00 UTC, post-settle) and hourly (cache-aware, cheap). It uses APScheduler's **in-memory** job store: jobs are code-defined and re-registered on every start, so the persistent SQLite job store from the design above is deferred to Phase 2 (when dynamic deadline/deadguard jobs are added). Started in-process by `fpl-autopilot serve`, or standalone via `fpl-autopilot scheduler`. The deadline-relative (H-48/H-24/H-2) and deadguard (H-120/H-30) jobs are Phase 2.
```

- [ ] **Step 4: Reinstall + verify**

```bash
.venv/bin/pip install -e ".[dev]" -q
.venv/bin/python -c "import apscheduler; print('apscheduler', apscheduler.__version__)"
.venv/bin/pytest -q 2>&1 | tail -1
```
Expected: prints an apscheduler version; suite still 106 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt docs/architecture.md
git commit -m "chore: add APScheduler dep + Phase-1 scheduler note (B13)"
```

---

## Task 2: scheduler module

**Files:** Create `src/scheduler.py`; Test `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_scheduler.py`

```python
from apscheduler.triggers.cron import CronTrigger
from src import scheduler
from src.data.db import connect, init_db


def test_build_scheduler_registers_jobs():
    sched = scheduler.build_scheduler()  # not started
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"weekly_refresh", "hourly_refresh"}
    for j in jobs.values():
        assert j.func is scheduler.refresh_and_recompute
        assert isinstance(j.trigger, CronTrigger)


def test_refresh_and_recompute_pipeline_order(monkeypatch):
    import src.cli as cli
    calls = []
    monkeypatch.setattr(cli, "refresh", lambda **kw: calls.append("refresh"))
    monkeypatch.setattr(scheduler.fdr, "compute_and_store", lambda conn: calls.append("fdr"))
    monkeypatch.setattr(scheduler.xp, "compute_and_store", lambda conn: calls.append("xp"))
    monkeypatch.setattr(scheduler, "_ping_healthcheck", lambda: calls.append("ping"))
    conn = connect(":memory:")
    init_db(conn)
    scheduler.refresh_and_recompute(cfg={"storage": {"db_path": ":memory:"}}, conn=conn)
    assert calls == ["refresh", "fdr", "xp", "ping"]
    conn.close()


def test_ping_healthcheck_noop_without_url(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    called = []
    monkeypatch.setattr(scheduler.requests, "get", lambda *a, **k: called.append(1))
    scheduler._ping_healthcheck()
    assert called == []


def test_ping_healthcheck_calls_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "http://hc.example/ping")
    got = []
    monkeypatch.setattr(scheduler.requests, "get", lambda url, timeout=None: got.append(url))
    scheduler._ping_healthcheck()
    assert got == ["http://hc.example/ping"]
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: `ModuleNotFoundError: No module named 'src.scheduler'`.

- [ ] **Step 3: Write `src/scheduler.py`**

```python
import logging
import os
import requests
from .config import load_config, db_path
from .data.db import connect, init_db
from .analytics import fdr, xp

log = logging.getLogger(__name__)


def _ping_healthcheck():
    url = os.getenv("HEALTHCHECK_URL")
    if not url:
        return
    try:
        requests.get(url, timeout=10)
    except requests.RequestException:
        log.warning("healthcheck ping failed")


def refresh_and_recompute(cfg=None, conn=None, client=None, understat_client=None):
    """The Phase-1 scheduled job: refresh data (cache-aware) then recompute FDR + xP."""
    from .cli import refresh  # lazy import: avoids a cycle (cli.serve imports this module)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        refresh(cfg=cfg, conn=conn, client=client, understat_client=understat_client)
        fdr.compute_and_store(conn)
        xp.compute_and_store(conn)
        _ping_healthcheck()
    finally:
        if owns:
            conn.close()


def build_scheduler(scheduler=None):
    """Register the Phase-1 cron jobs and return the (un-started) scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    scheduler = scheduler or BackgroundScheduler(timezone="UTC")
    scheduler.add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0),
                      id="weekly_refresh", replace_existing=True)
    scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                      id="hourly_refresh", replace_existing=True)
    return scheduler


def run_scheduler_blocking():
    """Run the cadence headless (blocks)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    build_scheduler(BlockingScheduler(timezone="UTC")).start()
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (was 106; now 110).

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: Phase-1 scheduler (refresh_and_recompute job + build_scheduler)"
```

---

## Task 3: CLI integration (serve starts scheduler + scheduler command)

**Files:** Modify `src/cli.py`; Test `tests/test_scheduler.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_scheduler.py`

```python
def test_serve_starts_scheduler(monkeypatch):
    import src.cli as cli
    events = []

    class FakeSched:
        def start(self):
            events.append("start")

        def shutdown(self, wait=False):
            events.append("shutdown")

    monkeypatch.setattr("src.scheduler.build_scheduler", lambda: FakeSched())
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: events.append("uvicorn"))
    cli.serve(port=0, scheduler=True)
    assert events == ["start", "uvicorn", "shutdown"]


def test_serve_no_scheduler(monkeypatch):
    import src.cli as cli
    built = []
    monkeypatch.setattr("src.scheduler.build_scheduler", lambda: built.append(1))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    cli.serve(port=0, scheduler=False)
    assert built == []
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: FAIL — `cli.serve` has no `scheduler` kwarg (TypeError).

- [ ] **Step 3: Update `serve` in `src/cli.py`.** Replace the existing `serve` function:

```python
def serve(host="0.0.0.0", port=None):
    import os
    import uvicorn
    port = port or int(os.getenv("PORT", "8000"))
    uvicorn.run("src.interface.api:app", host=host, port=port)
```
with:
```python
def serve(host="0.0.0.0", port=None, scheduler=True):
    import os
    import uvicorn
    port = port or int(os.getenv("PORT", "8000"))
    sched = None
    if scheduler:
        from .scheduler import build_scheduler
        sched = build_scheduler()
        sched.start()
    try:
        uvicorn.run("src.interface.api:app", host=host, port=port)
    finally:
        if sched is not None:
            sched.shutdown(wait=False)
```

- [ ] **Step 4: Register `--no-scheduler` and the `scheduler` subcommand in `main`.** In `main`, the serve subparser block currently is:

```python
    p_serve = sub.add_parser("serve", help="run the FastAPI server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=None)
```
Add a line after it:
```python
    p_serve.add_argument("--no-scheduler", action="store_true", help="run the API without the background scheduler")
    sub.add_parser("scheduler", help="run the background refresh scheduler (blocking)")
```
And in the dispatch, the serve branch currently is:
```python
    elif args.command == "serve":
        serve(host=args.host, port=args.port)
```
Replace it with:
```python
    elif args.command == "serve":
        serve(host=args.host, port=args.port, scheduler=not args.no_scheduler)
    elif args.command == "scheduler":
        from .scheduler import run_scheduler_blocking
        run_scheduler_blocking()
```

- [ ] **Step 5: Run to verify PASS**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: 6 passed.

- [ ] **Step 6: Verify `--help` + whole suite**

```bash
.venv/bin/fpl-autopilot --help
.venv/bin/fpl-autopilot serve --help
.venv/bin/pytest -q
```
Expected: top-level help lists `refresh`, `serve`, `scheduler`; `serve --help` shows `--no-scheduler`; suite green (112). Do NOT run `serve`/`scheduler` here (they block) — Task 4 does the live launch.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_scheduler.py
git commit -m "feat: serve starts background scheduler (+--no-scheduler); scheduler command"
```

---

## Task 4: Live check (definition of done)

- [ ] **Step 1: Launch `serve` (with scheduler) in the background and confirm both are up**

```bash
.venv/bin/fpl-autopilot serve --port 8143 &
SERVER_PID=$!
sleep 4
echo "--- API up? ---"; curl -s -o /dev/null -w "status %{http_code}\n" localhost:8143/api/status
echo "--- scheduler started in logs? ---"; jobs -l
kill $SERVER_PID 2>/dev/null
```
Expected: `status 200` (API serving), process ran without error (the BackgroundScheduler started alongside; APScheduler logs "Adding job" / "Scheduler started"). At end-of-season the scheduled jobs are harmless no-ops.

- [ ] **Step 2: Confirm `--no-scheduler` runs the API alone**

```bash
.venv/bin/fpl-autopilot serve --port 8144 --no-scheduler &
PID=$!; sleep 3; curl -s -o /dev/null -w "no-sched status %{http_code}\n" localhost:8144/api/status; kill $PID 2>/dev/null
```
Expected: `status 200`.

- [ ] **Step 3: Mark complete**

```bash
git commit --allow-empty -m "chore: scheduler slice complete and smoke-tested"
```

---

## Self-Review notes (author)

- **Spec coverage:** deps + architecture.md note (T1, B13); `refresh_and_recompute` + `build_scheduler` + `run_scheduler_blocking` + `_ping_healthcheck` (T2); serve integration + `--no-scheduler` + `scheduler` command (T3); live check (T4). Phase-2 jobs + persistent store deferred per spec §3.
- **Placeholder scan:** none — all code + tests concrete.
- **Type/name consistency:** `refresh_and_recompute(cfg, conn, client, understat_client)` and `build_scheduler(scheduler=None)` used identically in tests + cli; `serve(host, port, scheduler=True)` matches the test calls; lazy imports (`from .cli import refresh`, `from .scheduler import build_scheduler`) avoid the cli↔scheduler cycle; reuses `cli.refresh`/`fdr.compute_and_store`/`xp.compute_and_store` with their real signatures.
```
