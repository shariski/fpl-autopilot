# Scheduler (Phase 1, APScheduler) — Design Spec

- **Date:** 2026-05-23
- **Status:** Approved for planning
- **Scope:** Phase-1 scheduled jobs — keep data + analytics fresh automatically so the system never falls behind. Phase-2 jobs (notifications, deadline H-windows, deadguard) are out of scope.
- **Slice goal:** `fpl-autopilot serve` runs the API *and* a background scheduler that periodically refreshes FPL/Understat data and recomputes FDR + xP; a standalone `fpl-autopilot scheduler` runs the cadence headless.

This is the slice that delivers the product's animating goal — the system maintains itself even when the user isn't looking.

---

## 1. Context

`architecture.md` (Scheduling) lists a rich schedule, but most is Phase 2 (notifications, H-48/H-24/H-2 deadline jobs, deadguard H-120/H-30). For Phase 1 the only job is the **data + analytics refresh cadence**: re-fetch FPL/Understat (cache-aware) and recompute FDR + xP. All the building blocks exist (`cli.refresh`, `analytics.fdr.compute_and_store`, `analytics.xp.compute_and_store`).

## 2. Decisions locked

| Decision | Choice |
|---|---|
| Library | APScheduler (in-process), per `architecture.md` |
| Phase-1 jobs | one job (`refresh_and_recompute`) on two cron triggers: weekly post-settle (Tue 03:00 UTC) + hourly (cache-aware → cheap) |
| Job store | **in-memory (default)**, NOT persistent SQLite — jobs are statically code-defined, so persistence buys nothing yet; avoids a SQLAlchemy dep. Persistent store deferred to Phase 2 dynamic jobs. Recorded in `architecture.md` (B13). |
| Runtime | `BackgroundScheduler` started by `fpl-autopilot serve` (single process: API + scheduler); plus a standalone `fpl-autopilot scheduler` (`BlockingScheduler`) |
| Healthcheck | after a successful job, GET `HEALTHCHECK_URL` if set (so a missed run is externally detectable); skipped if unset |
| Phase-2 jobs | deferred (no notif/deadline/deadguard jobs) |

## 3. Scope

### In scope
- `src/scheduler.py`: `refresh_and_recompute(...)`, `_ping_healthcheck()`, `build_scheduler(scheduler=None)`, `run_scheduler_blocking()`.
- `src/cli.py`: `serve` starts the background scheduler (with a `--no-scheduler` opt-out); new `scheduler` subcommand runs the blocking scheduler.
- `pyproject.toml`/`requirements.txt`: add `APScheduler`.
- `architecture.md`: note the in-memory-store v1 choice + Phase-1 job set (B13).
- Tests: `build_scheduler` registers the expected jobs/triggers (introspect, don't run); `refresh_and_recompute` calls refresh + fdr + xp with injected fakes and degrades gracefully.

### Out of scope (deferred)
- Notification, deadline-relative (H-48/H-24/H-2), and deadguard jobs (Phase 2).
- Persistent SQLAlchemy job store (until dynamic/one-off jobs exist).
- Missed-job catch-up / coalescing tuning beyond APScheduler defaults.

## 4. Components

### `src/scheduler.py`
```
refresh_and_recompute(cfg=None, conn=None, client=None, understat_client=None) -> None
    # lazy `from .cli import refresh` (avoids any import cycle)
    # refresh(cfg=cfg, conn=conn, client=..., understat_client=...)  (cache-aware; refresh self-degrades on Understat failure, R2)
    # fdr.compute_and_store(conn); xp.compute_and_store(conn)
    # _ping_healthcheck()
    # owns/closes conn only if it created it

_ping_healthcheck() -> None
    # GET os.getenv("HEALTHCHECK_URL") with a timeout if set; swallow+log failures

build_scheduler(scheduler=None) -> scheduler
    # scheduler = scheduler or BackgroundScheduler(timezone="UTC")
    # add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0), id="weekly_refresh", replace_existing=True)
    # add_job(refresh_and_recompute, CronTrigger(minute=0), id="hourly_refresh", replace_existing=True)
    # return scheduler  (NOT started — caller starts)

run_scheduler_blocking() -> None
    # build_scheduler(BlockingScheduler(timezone="UTC")).start()   # blocks
```

### `src/cli.py`
- `serve(host, port, scheduler=True)`: if `scheduler`, `s = build_scheduler(); s.start()` before `uvicorn.run(...)`, and `s.shutdown(wait=False)` in a `finally`. `--no-scheduler` sets it False.
- `scheduler` subcommand → `run_scheduler_blocking()`.

## 5. architecture.md update (B13)
In the Scheduling section, add a Phase-1 note: the implemented Phase-1 scheduler runs only `refresh_and_recompute` (FPL/Understat refresh + FDR/xP recompute) on a weekly (Tue 03:00 UTC) + hourly cadence, using an **in-memory** APScheduler job store (jobs are code-defined and re-registered on start, so a persistent store adds nothing yet; the persistent SQLite job store from the original design is deferred to Phase 2 when dynamic deadline jobs are added). Started in-process by `serve` or standalone via `fpl-autopilot scheduler`.

## 6. Testing
- `test_build_scheduler_registers_jobs`: `build_scheduler()` returns a scheduler whose `get_jobs()` has ids `{"weekly_refresh", "hourly_refresh"}`, both `CronTrigger`s; the job func is `refresh_and_recompute`. (Do not start the scheduler.)
- `test_refresh_and_recompute_runs_pipeline`: with an in-memory DB + fake FPL/Understat clients (reuse the `FakeClient`/`FakeUnderstatClient` patterns), seeded so there's an upcoming GW; assert it populates players + fdr + xp rows (refresh → fdr → xp ran). 
- `test_refresh_and_recompute_understat_failure_degrades`: a raising Understat client → FPL data still refreshed, no exception escapes (R2).
- `test_ping_healthcheck_noop_without_url` / `test_ping_healthcheck_gets_url`: with `HEALTHCHECK_URL` unset → no call; set (monkeypatched requests) → GET issued; a failing GET is swallowed.
- `serve --help` lists `--no-scheduler`; top-level `--help` lists `scheduler`.

## 7. Definition of done
1. `pytest` green incl. scheduler tests.
2. `fpl-autopilot serve` starts the API with the background scheduler running (no error); `--no-scheduler` disables it; `fpl-autopilot scheduler` starts the blocking cadence.
3. `architecture.md` updated (B13).

## 8. Notes
- End-of-season (2026-05-23): the hourly/weekly jobs run but mostly no-op (cache fresh, no new fixtures) — correct; the cadence matters next season.
- The refresh orchestrator currently lives in `cli.refresh`; the scheduler reuses it (lazy import). If a dedicated service layer emerges later, move it — not needed for v1.
- Single-worker uvicorn assumed (no `--reload`/multiple workers), so the BackgroundScheduler shares the one process.
