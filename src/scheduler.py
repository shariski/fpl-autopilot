import logging
import os
import requests
from . import config
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


def refresh_and_recompute(cfg=None, conn=None, client=None, understat_client=None, key=None):
    """Public refresh + analytics recompute + healthcheck. With key, also authed my-team snapshot.

    Public path always runs. Authed step is best-effort: failures are logged but do not crash the
    public refresh — the older authed row (or only the public row) stays as fallback.
    """
    from .cli import refresh  # lazy import: avoids a cycle (cli.serve imports this module)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        refresh(cfg=cfg, conn=conn, client=client, understat_client=understat_client)
        fdr.compute_and_store(conn)
        xp.compute_and_store(conn)
        if config.ai_enabled(cfg):
            try:
                from src.ai import jobs as ai_jobs
                from src.ai.provider import OllamaProvider
                provider = OllamaProvider(
                    host=config.ai_ollama_host(cfg),
                    model=config.ai_ollama_model(cfg),
                    timeout_seconds=config.ai_timeout_seconds(cfg),
                )
                ai_jobs.generate_ai_reasoning_job(
                    conn, panes=["captain", "transfer", "chip"], provider=provider,
                    model_id=config.ai_ollama_model(cfg))
            except Exception:
                log.exception("ai.generate_job_failed")
        _ping_healthcheck()
        if key is not None:
            _refresh_authed_my_team(conn, key)
    finally:
        if owns:
            conn.close()


def _refresh_authed_my_team(conn, key):
    """Best-effort: fetch /api/my-team and snapshot it. Never raises."""
    from .auth import session as auth_session
    from .execution import executor
    from .data import repository
    from . import config as cfg_mod
    try:
        next_gw = _next_gw_id(conn)
        if next_gw is None:
            return
        session = auth_session.ensure_session(conn, key)
        payload = executor.fetch_my_team_authed(session, cfg_mod.team_id())
        repository.snapshot_my_team_authed(conn, next_gw, payload)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.warning("authed my-team snapshot failed: %s", exc)


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


def _maybe_load_key():
    if not (config.unattended_enabled() or config.telegram_interactive_enabled()
            or config.deadguard_enabled()):
        return None
    from .auth import master
    return master.get_master_key()


def build_scheduler(scheduler=None, key=None):
    """Register the cron jobs and return the (un-started) scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    scheduler = scheduler or BackgroundScheduler(timezone="UTC")
    scheduler.add_job(refresh_and_recompute, CronTrigger(day_of_week="tue", hour=3, minute=0),
                      id="weekly_refresh", replace_existing=True, kwargs={"key": key})
    scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                      id="hourly_refresh", replace_existing=True, kwargs={"key": key})
    if key is not None:
        scheduler.add_job(lambda: auto_execute_job(key), CronTrigger(minute="*/15"),
                          id="auto_execute", replace_existing=True)
    if key is not None and config.telegram_interactive_enabled():
        from .interface import telegram_interactive
        scheduler.add_job(lambda: telegram_interactive.poll_once(key),
                          CronTrigger(second="*/20"), id="telegram_poll", replace_existing=True)
    if key is not None and config.deadguard_enabled():
        from .interface import deadguard
        scheduler.add_job(lambda: deadguard.run_deadguard_job(key),
                          CronTrigger(minute="*/5"), id="deadguard_job", replace_existing=True)
    return scheduler


def run_scheduler_blocking():
    """Run the cadence headless (blocks)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    build_scheduler(BlockingScheduler(timezone="UTC"), key=_maybe_load_key()).start()


def _default_route(conn, key):
    from .execution import router
    return router.route_gameweek(conn, key, live=True)


def auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None):
    from datetime import datetime, timezone, timedelta
    from .interface import telegram
    from .auth.session import SessionExpired
    from .execution import override
    cfg = cfg or load_config()
    if not config.unattended_enabled(cfg):
        return None
    hours = config.unattended_hours_before(cfg)
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        if override.is_frozen(conn):
            log.info("auto_execute_job skipped: frozen")
            return None
        row = conn.execute(
            "SELECT id, deadline_utc, last_system_action_at FROM gameweeks WHERE is_next=1"
        ).fetchone()
        if not row or not row["deadline_utc"] or row["last_system_action_at"]:
            return None
        deadline = datetime.fromisoformat(row["deadline_utc"])
        now = now or datetime.now(timezone.utc)
        if not (now <= deadline <= now + timedelta(hours=hours)):
            return None
        mode = config.mode(cfg)
        try:
            plan = (route_fn or _default_route)(conn, key)
        except SessionExpired:
            froze = override.maybe_auto_freeze(conn)
            try:
                telegram.notify(conn, kind="alert", decision_type="auth", mode=mode,
                                summary="FPL session expired — re-run init-fpl. No changes were made.")
                if froze:
                    telegram.notify(conn, kind="alert", decision_type="override", mode="override",
                                    summary="Auto-execution FROZEN — 2 consecutive auth failures. "
                                            "Re-run init-fpl, then unfreeze.")
            except Exception:
                log.exception("telegram auth/freeze alert failed")
            raise
        if any(p["route"] == "execute" for p in plan):
            conn.execute("UPDATE gameweeks SET last_system_action_at=? WHERE id=?",
                         (now.isoformat(), row["id"]))
            conn.commit()
        try:
            from .interface import telegram_interactive
            if telegram_interactive.is_enabled(cfg):
                telegram_interactive.notify_plan(conn, plan, gw=row["id"], mode=mode)
            else:
                telegram.notify_plan(conn, plan, mode=mode)
        except Exception:
            log.exception("telegram notify_plan failed after execution")
        if config.mode(cfg) == "auto" and any(p["route"] == "execute" for p in plan):
            try:
                from .interface import telegram_interactive
                if telegram_interactive.is_enabled(cfg):
                    telegram.send_message(
                        "🛑 Tap to freeze further auto-execution.",
                        buttons=[[{"text": "🛑 Freeze", "callback_data": "f:1"}]])
            except Exception:
                log.exception("telegram freeze-button send failed")
        return plan
    finally:
        if owns:
            conn.close()
