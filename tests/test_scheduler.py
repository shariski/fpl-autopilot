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
