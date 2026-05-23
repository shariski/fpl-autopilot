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
                      id="weekly_refresh", replace_existing=True)
    scheduler.add_job(refresh_and_recompute, CronTrigger(minute=0),
                      id="hourly_refresh", replace_existing=True)
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
        return plan
    finally:
        if owns:
            conn.close()
