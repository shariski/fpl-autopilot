import logging
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


def test_maybe_load_key_disabled_returns_none(monkeypatch):
    # unattended.enabled: false AND telegram.interactive: false AND deadguard.enabled: false
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: False)
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


# ---------------------------------------------------------------------------
# Task 8 (2.4b): telegram interactive wiring tests
# ---------------------------------------------------------------------------

def test_maybe_load_key_loads_when_interactive(monkeypatch):
    monkeypatch.setattr(scheduler.config, "unattended_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: True)
    import src.auth.master as master
    monkeypatch.setattr(master, "get_master_key", lambda: b"k")
    assert scheduler._maybe_load_key() == b"k"


def test_build_scheduler_registers_telegram_poll_when_interactive(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: True)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "telegram_poll" in {j.id for j in sched.get_jobs()}


def test_build_scheduler_no_telegram_poll_when_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "telegram_poll" not in {j.id for j in sched.get_jobs()}


def test_auto_execute_uses_interactive_notify_when_enabled(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    from src.interface import telegram_interactive as ti
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    got = {}
    monkeypatch.setattr(ti, "notify_plan", lambda conn, plan, **k: got.update(k, n=len(plan)))
    plan = [{"decision": "captain", "route": "notify", "confidence": 50,
             "summary": "Captain pending: X", "executed": False,
             "identity": {"captain_id": 5, "vice_id": 6}}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)
    assert got["n"] == 1 and got["gw"] == 1 and got["mode"] == "manual"


def test_maybe_load_key_loads_when_deadguard(monkeypatch):
    monkeypatch.setattr(scheduler.config, "unattended_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: True)
    import src.auth.master as master
    monkeypatch.setattr(master, "get_master_key", lambda: b"k")
    assert scheduler._maybe_load_key() == b"k"


def test_build_scheduler_registers_deadguard_job_when_enabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: True)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "deadguard_job" in {j.id for j in sched.get_jobs()}


def test_build_scheduler_no_deadguard_job_when_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "deadguard_enabled", lambda *a, **k: False)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "deadguard_job" not in {j.id for j in sched.get_jobs()}


# ---------------------------------------------------------------------------
# Task 5 (2.7): freeze checkpoint + B7 auto-freeze wiring
# ---------------------------------------------------------------------------

def test_auto_execute_skips_when_frozen(db):
    from src.execution import override
    _seed_gw(db, _NOW + timedelta(hours=1))
    override.freeze(db, reason="test", source="user")
    called = []
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW,
                               route_fn=lambda c, k: called.append(1), cfg=_CFG)
    assert called == []                                    # route never invoked
    assert db.execute(
        "SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None


def test_auto_execute_session_expired_auto_freezes_at_threshold(db, monkeypatch):
    from src.auth.session import SessionExpired
    from src.execution import override
    from src.data import repository
    _seed_gw(db, _NOW + timedelta(hours=1))
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)              # already at 2 (this run would be the 2nd consecutive)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert override.is_frozen(db) is True
    assert any("FROZEN" in t for t in sent)               # the freeze alert went out


def test_auto_execute_session_expired_no_freeze_below_threshold(db, monkeypatch):
    from src.auth.session import SessionExpired
    from src.execution import override
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    monkeypatch.setattr(tg, "send_message", lambda text, **k: True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert override.is_frozen(db) is False                 # only 1st failure (counter 0 in test) -> no freeze


def test_auto_execute_auto_mode_sends_freeze_button(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    from src.interface import telegram_interactive as ti
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    monkeypatch.setattr(ti, "notify_plan", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append((text, k.get("buttons"))) or True)
    cfg = {"mode": {"current": "auto"}, "unattended": {"enabled": True, "hours_before_deadline": 2}}
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=cfg)
    assert any(btns == [[{"text": "🛑 Freeze", "callback_data": "f:1"}]] for _, btns in sent)


def test_auto_execute_manual_mode_no_freeze_button(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(k.get("buttons")) or True)
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)  # mode=manual
    assert all(b != [[{"text": "🛑 Freeze", "callback_data": "f:1"}]] for b in sent)


def test_serve_defaults_to_localhost():
    import inspect
    import src.cli as cli
    assert inspect.signature(cli.serve).parameters["host"].default == "127.0.0.1"


def test_refresh_and_recompute_invokes_ai_job_when_enabled(monkeypatch):
    """ai.enabled=True -> generate_ai_reasoning_job is called after recompute."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-05-20T11:00:00Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    calls = {"refresh": 0, "fdr": 0, "xp": 0, "ai": 0}
    monkeypatch.setattr("src.cli.refresh", lambda **kw: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr("src.analytics.fdr.compute_and_store",
                        lambda c: calls.__setitem__("fdr", calls["fdr"] + 1))
    monkeypatch.setattr("src.analytics.xp.compute_and_store",
                        lambda c: calls.__setitem__("xp", calls["xp"] + 1))
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda *a, **kw: calls.__setitem__("ai", calls["ai"] + 1) or {"captain": "ok"})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert calls == {"refresh": 1, "fdr": 1, "xp": 1, "ai": 1}


def test_refresh_and_recompute_skips_ai_when_disabled(monkeypatch):
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": False}}

    called = {"ai": 0}
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda *a, **kw: called.__setitem__("ai", called["ai"] + 1) or {})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert called["ai"] == 0


def test_refresh_and_recompute_swallows_ai_exception(monkeypatch, caplog):
    """An exception in the AI job is logged but never blocks the recompute cycle."""
    import logging
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)

    def _boom(*a, **kw):
        raise RuntimeError("ollama is down")
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job", _boom)

    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        scheduler.refresh_and_recompute(cfg=cfg, conn=conn)   # must NOT raise
    assert any("ai" in r.message.lower() or "ai.generate_job_failed" in r.message
               for r in caplog.records)


# ---------------------------------------------------------------------------
# Task 3 (authed-read-model-wiring): refresh_and_recompute(key=...) tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Task 4 (feat/authed-read-model-wiring): build_scheduler threads key into jobs
# ---------------------------------------------------------------------------

def test_build_scheduler_passes_key_to_refresh_jobs():
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


def test_refresh_and_recompute_invokes_ai_with_both_panes(monkeypatch):
    """ai.enabled=True calls generate_ai_reasoning_job with panes=['captain', 'transfer', 'chip']."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    captured_panes = []
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda c, **kw: captured_panes.append(kw["panes"]) or {})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert captured_panes == [["captain", "transfer", "chip"]]


def test_refresh_and_recompute_invokes_ai_with_three_panes(monkeypatch):
    """ai.enabled=True calls generate_ai_reasoning_job with panes=['captain', 'transfer', 'chip']."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    captured_panes = []
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda c, **kw: captured_panes.append(kw["panes"]) or {})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert captured_panes == [["captain", "transfer", "chip"]]
