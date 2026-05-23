from datetime import datetime, timezone, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from src import scheduler
from src.data.db import connect, init_db
from src.interface import telegram as tg


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

    monkeypatch.setattr("src.scheduler.build_scheduler", lambda **kw: FakeSched())
    monkeypatch.setattr("src.scheduler._maybe_load_key", lambda: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: events.append("uvicorn"))
    cli.serve(port=0, scheduler=True)
    assert events == ["start", "uvicorn", "shutdown"]


def test_serve_no_scheduler(monkeypatch):
    import src.cli as cli
    built = []
    monkeypatch.setattr("src.scheduler.build_scheduler", lambda **kw: built.append(1))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    cli.serve(port=0, scheduler=False)
    assert built == []


# ---------------------------------------------------------------------------
# auto_execute_job tests
# ---------------------------------------------------------------------------

_CFG = {"unattended": {"enabled": True, "hours_before_deadline": 2}}
_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _seed_gw(db, deadline, last_action=None):
    db.execute(
        "INSERT INTO gameweeks (id, deadline_utc, is_next, last_system_action_at) VALUES (1, ?, 1, ?)",
        (deadline.isoformat(), last_action))
    db.commit()


def test_auto_execute_in_window_executes(db):
    _seed_gw(db, _NOW + timedelta(hours=1))  # within 2h

    def route_fn(conn, key):
        route_fn.called_with = key
        return [{"decision": "captain", "route": "execute", "confidence": 80}]

    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=route_fn, cfg=_CFG)
    assert route_fn.called_with == b"key"
    row = db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()
    assert row["last_system_action_at"] is not None


def test_auto_execute_out_of_window_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=10))  # >2h away
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert not called
    assert db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_already_acted_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=1), last_action="2026-05-23T10:00:00+00:00")
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert not called


def test_auto_execute_manual_notify_not_marked(db):
    _seed_gw(db, _NOW + timedelta(hours=1))
    scheduler.auto_execute_job(
        b"key", conn=db, now=_NOW,
        route_fn=lambda c, k: [{"decision": "captain", "route": "notify", "confidence": 50}], cfg=_CFG)
    assert db.execute("SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_disabled_skips(db):
    _seed_gw(db, _NOW + timedelta(hours=1))
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: called.append(1),
                               cfg={"unattended": {"enabled": False}})
    assert not called


# ---------------------------------------------------------------------------
# build_scheduler key wiring + _maybe_load_key tests
# ---------------------------------------------------------------------------

def test_build_scheduler_no_key_no_autoexec():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=None)
    ids = {j.id for j in sched.get_jobs()}
    assert "auto_execute" not in ids
    assert "weekly_refresh" in ids and "hourly_refresh" in ids


def test_build_scheduler_with_key_adds_autoexec():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "auto_execute" in {j.id for j in sched.get_jobs()}


def test_maybe_load_key_disabled_returns_none():
    # config.yaml ships with unattended.enabled: false
    assert scheduler._maybe_load_key() is None


def test_auto_execute_notifies_plan(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True},
            {"decision": "transfer", "route": "notify", "confidence": 50,
             "summary": "Transfer pending: OUT A IN B", "executed": False}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)
    assert any(t.startswith("✅ Executed") for t in sent)
    assert any(t.startswith("📊 Decision pending") for t in sent)


def test_auto_execute_session_expired_alerts_and_raises(db, monkeypatch):
    from src.auth.session import SessionExpired
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert any(t.startswith("❌ Autopilot blocked") for t in sent)
    assert db.execute(
        "SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_notify_failure_does_not_break_execution(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")

    def boom_send(text, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(tg, "send_message", boom_send)
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True}]
    result = scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                                        route_fn=lambda c, k: plan, cfg=_CFG)
    assert result == plan
    assert db.execute(
        "SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is not None


def test_auto_execute_session_expired_propagates_even_if_alert_send_fails(db, monkeypatch):
    from src.auth.session import SessionExpired
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")

    def boom_send(text, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(tg, "send_message", boom_send)

    def boom_route(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom_route, cfg=_CFG)
