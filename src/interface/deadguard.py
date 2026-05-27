import logging
import os
from datetime import datetime, timezone

from src import config
from src.config import load_config, db_path
from src.data import repository
from src.data.db import connect, init_db
from src.analytics import xp
from src.decisions import captain
from src.decisions import transfers
from src.decisions import bench
from src.execution import lineup
from src.execution import transfer as transfer_exec
from src.execution import executor
from src.execution import override
from src.interface import telegram
from src.auth.session import SessionExpired, ensure_session

log = logging.getLogger(__name__)

RESOLVED = ("USER_ACTED", "SYSTEM_ACTED", "DEADGUARD_EXECUTED", "DEADGUARD_SKIPPED")


def evaluate(now, *, deadline, state, last_system_action_at, user_acted,
             warned, triggered, warn_min, trigger_min,
             reeval_enabled=False, lockout_min=15):
    """Return a directive: 'system_acted' | 'user_acted' | 'warn' | 'trigger'
    | 'reeval' | 'lockout' | 'noop'. Pure: no I/O, deterministic for frozen inputs (B11)."""
    if state == "DEADGUARD_EXECUTED":
        if not reeval_enabled:
            return "noop"
        mins = (deadline - now).total_seconds() / 60
        if mins <= 0:
            return "noop"
        return "lockout" if mins <= lockout_min else "reeval"
    if state in RESOLVED:
        return "noop"
    if last_system_action_at:
        return "system_acted"
    if user_acted:
        return "user_acted"
    mins = (deadline - now).total_seconds() / 60
    if mins <= 0:
        return "noop"
    if mins <= trigger_min:
        return "noop" if triggered else "trigger"
    if mins <= warn_min:
        return "noop" if warned else "warn"
    return "noop"


def user_acted(conn, gw):
    g = conn.execute("SELECT last_user_action_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
    if g and g["last_user_action_at"]:
        return True
    n = conn.execute(
        "SELECT COUNT(*) c FROM pending_decisions WHERE gw=? AND status IN ('confirmed','rejected')",
        (gw,)).fetchone()["c"]
    return n > 0


def send_warning(conn, gw, *, mins):
    text = (f"⏳ Deadguard will set your captain when ~{mins} min remain before the deadline, "
            f"unless you act.\nTap to keep your team as-is.")
    buttons = [[{"text": "✅ Keep as is", "callback_data": f"k:{gw}"}],
               [{"text": "🛑 Freeze", "callback_data": "f:1"}]]
    telegram.send_message(text, buttons=buttons)


def handle_keep(conn, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    _, _, gw_s = cq.get("data", "").partition(":")
    if gw_s.isdigit():
        repository.touch_user_action(conn, int(gw_s))
    telegram.answer_callback_query(cq["id"], text="Kept as is ✅", session=session)


def _run_trigger(conn, key, gw, cfg):
    repository.set_gameweek_state(conn, gw, "DEADGUARD_ACTIVE")
    caps = captain.get_captain_picks(conn)
    if not caps["picks"]:
        repository.set_gameweek_state(conn, gw, "DEADGUARD_SKIPPED")
        repository.mark_deadguard_triggered(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="skipped: no captain pick available", executed=False)
        _notify(conn, "info", "Deadguard ran — no safe action (no data). Team unchanged.")
        return
    # 1. lineup: captain/vice + bench order, one atomic write
    try:
        result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True, optimize_bench=True)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Deadguard: FPL session expired — re-run init-fpl. No changes made.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return
    except Exception as e:
        _notify(conn, "alert", f"Deadguard failed: {type(e).__name__}")
        return
    if not getattr(result, "ok", False):
        _notify(conn, "alert", "Deadguard: lineup submission did not complete — will retry.")
        return                                          # not marked -> retryable next tick
    # 2. lineup succeeded -> lock once-per-GW (idempotent re-set of the same lineup is harmless)
    name = caps["picks"][0]["web_name"]
    try:
        repository.mark_deadguard_triggered(conn, gw)
        repository.set_gameweek_state(conn, gw, "DEADGUARD_EXECUTED")
    except Exception:
        log.exception("deadguard post-execution bookkeeping failed (lineup was already set)")
    # 3. transfer-if-flagged (best-effort; never undoes the lineup, never retried)
    transfer_note = "no transfer"
    transfer_applied = False
    try:
        rank = _pick_flagged_transfer(conn, cfg)
        if rank is not None:
            tr = transfer_exec.run_transfer(conn, key, rank=rank, live=True, confirm_fn=lambda d: True)
            if getattr(tr, "ok", False):
                body = tr.request["body"]["transfers"][0]
                transfer_note = "transfer applied"
                transfer_applied = True
                repository.set_deadguard_transfer(conn, gw, body["element_out"], body["element_in"])
            else:
                transfer_note = "transfer failed"
                _notify(conn, "alert", "Deadguard: flagged-player transfer did not complete.")
    except Exception as e:
        transfer_note = f"transfer failed ({type(e).__name__})"
        log.exception("deadguard transfer step failed")
        _notify(conn, "alert", f"Deadguard transfer failed: {type(e).__name__}")
    try:
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"captain {name}; bench optimized; {transfer_note}",
                                inputs={"pick": caps["picks"][0]}, executed=True)
    except Exception:
        log.exception("deadguard summary log failed (lineup and transfer already applied)")
    # Build outcome + AI prose (best-effort; never blocks the notification)
    template_summary = f"Deadguard: captain {name}, bench optimized, {transfer_note}."
    summary = template_summary
    try:
        if config.ai_enabled(cfg):
            transfer_info = None
            if transfer_applied:
                out_row = conn.execute(
                    "SELECT web_name FROM players WHERE id=?",
                    (body["element_out"],)).fetchone()
                in_row = conn.execute(
                    "SELECT web_name FROM players WHERE id=?",
                    (body["element_in"],)).fetchone()
                if out_row is not None and in_row is not None:
                    transfer_info = {"out_name": out_row["web_name"],
                                     "in_name": in_row["web_name"]}
            vice_name = (caps["picks"][1]["web_name"]
                         if len(caps["picks"]) > 1 else None)
            outcome = {
                "captain_name": name,
                "vice_name": vice_name,
                "transfer": transfer_info,
                "gw": gw,
            }
            from src.ai import reasoning as ai_reasoning, provider as ai_provider
            ollama = ai_provider.OllamaProvider(
                host=config.ai_ollama_host(cfg),
                model=config.ai_ollama_model(cfg),
                timeout_seconds=config.ai_timeout_seconds(cfg),
            )
            if ai_reasoning.generate_deadguard_summary(
                    conn, gw=gw, outcome=outcome,
                    provider=ollama, model_id=config.ai_ollama_model(cfg)):
                prose, src = ai_reasoning.render_deadguard_summary(conn, gw, outcome)
                if src == "ai" and prose:
                    summary = prose
    except Exception:
        log.exception("ai.deadguard.generation_failed")
        summary = template_summary

    _notify(conn, "executed", summary)
    if transfer_applied:
        try:
            telegram.send_message("↩️ Undo the transfer? Free before the deadline.",
                                  buttons=[[{"text": "↩️ Undo", "callback_data": f"z:{gw}"}]])
        except Exception:
            log.exception("deadguard undo-button send failed")


def _current_lineup(picks):
    """(captain_id, vice_id, [bench element ids at positions 13/14/15, in order]) from FPL /my-team picks."""
    captain_id = next((p["element"] for p in picks if p.get("is_captain")), None)
    vice_id = next((p["element"] for p in picks if p.get("is_vice_captain")), None)
    benched = [p["element"] for p in sorted(picks, key=lambda p: p["position"]) if p["position"] in (13, 14, 15)]
    return (captain_id, vice_id, benched)


def _run_reevaluate(conn, key, gw, cfg, *, apply):
    # 1. force-fresh FPL availability data so the ranker sees late news (cache-bypassed; FPL only, B6).
    #    Lazy import mirrors scheduler.refresh_and_recompute (cli<->scheduler cycle).
    try:
        from src.cli import refresh
        refresh(cfg=cfg, conn=conn, sources=("fpl",), full=True)
        xp.compute_and_store(conn)
    except Exception:
        log.exception("deadguard re-eval refresh failed")
        return

    # 2. desired vs current lineup
    try:
        session = ensure_session(conn, key)
        current = executor.fetch_current_picks(session, config.team_id(cfg))
        caps = captain.get_captain_picks(conn)
        if not caps["picks"]:
            return
        desired = (caps["picks"][0]["player_id"], caps["vice_player_id"], bench.rank_bench(conn, current))
        cur = _current_lineup(current)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Deadguard re-eval: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return
    except Exception:
        log.exception("deadguard re-eval compare failed")
        return

    if desired == cur:
        return

    name = caps["picks"][0]["web_name"]
    if apply:
        # run_lineup re-fetches current picks + recomputes internally; session is re-used.
        try:
            result = lineup.run_lineup(conn, key, live=True, confirm_fn=lambda d: True,
                                       optimize_bench=True, session=session)
        except Exception as e:
            log.exception("deadguard re-eval apply failed")
            _notify(conn, "alert", f"Deadguard re-eval failed: {type(e).__name__}")
            return
        if getattr(result, "ok", False):
            repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                    action_taken=f"late-news re-eval: captain/bench updated (captain {name})",
                                    inputs={"desired": desired, "previous": cur}, executed=True)
            _notify(conn, "executed",
                    f"Late news: re-set captain {name} + bench. You can change it back before the deadline.")
        else:
            _notify(conn, "alert", "Deadguard re-eval: lineup update did not complete — will retry.")
    else:
        row = conn.execute(
            "SELECT deadguard_reeval_alerted_at FROM gameweeks WHERE id=?", (gw,)).fetchone()
        if row["deadguard_reeval_alerted_at"]:
            return
        repository.mark_deadguard_reeval_alerted(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken="late-news re-eval: missed update (within lockout)",
                                inputs={"desired": desired, "previous": cur}, executed=False)
        _notify(conn, "alert",
                f"Late news: your lineup may need a change (captain {name}), but it's too close to the "
                f"deadline for me to change it safely. You may want to act.")


def _player_status(conn, player_id):
    row = conn.execute("SELECT status FROM players WHERE id=?", (player_id,)).fetchone()
    return row["status"] if row else None


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


def _notify(conn, kind, summary):
    try:
        telegram.notify(conn, kind=kind, decision_type="deadguard", mode="deadguard", summary=summary)
    except Exception:
        log.exception("deadguard notify failed")


def run_undo(conn, key, gw, *, live=True, confirm_fn=None, now=None, session=None):
    target = repository.get_deadguard_transfer(conn, gw)
    if target is None:
        _notify(conn, "info", "Nothing to undo — deadguard made no transfer this gameweek.")
        return None
    row = conn.execute("SELECT deadline_utc, deadguard_transfer_undone_at FROM gameweeks WHERE id=?",
                       (gw,)).fetchone()
    if row["deadguard_transfer_undone_at"]:
        _notify(conn, "info", "Already undone.")
        return None
    now = now or datetime.now(timezone.utc)
    if row["deadline_utc"] and now >= datetime.fromisoformat(row["deadline_utc"]):
        _notify(conn, "info", "Too late to undo — the deadline has passed.")
        return None
    try:
        result = transfer_exec.run_undo_transfer(conn, key, out_id=target["out_id"], in_id=target["in_id"],
                                                 live=live, confirm_fn=confirm_fn, session=session)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Undo: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return None
    except Exception as e:
        log.exception("deadguard undo failed")
        _notify(conn, "alert", f"Undo failed: {type(e).__name__} — the squad may have changed.")
        return None
    if getattr(result, "ok", False) and not getattr(result, "dry_run", False):
        repository.mark_deadguard_transfer_undone(conn, gw)
        repository.touch_user_action(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"undo transfer: restored {target['out_id']}, removed {target['in_id']}",
                                inputs=target, executed=True)
        _notify(conn, "executed", "Reverted deadguard's transfer — sold player restored, free transfer back.")
    elif not getattr(result, "dry_run", False):
        _notify(conn, "alert", "Undo did not complete — the squad may have changed.")
    return result


def run_deadguard_job(key, *, conn=None, now=None, cfg=None):
    cfg = cfg or load_config()
    if not config.deadguard_enabled(cfg):
        return None
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        if override.is_frozen(conn):
            log.info("deadguard skipped: frozen")
            return None
        row = conn.execute(
            "SELECT id, deadline_utc, state, last_system_action_at, deadguard_warned_at, "
            "deadguard_triggered_at FROM gameweeks WHERE is_next=1").fetchone()
        if not row or not row["deadline_utc"]:
            return None
        gw = row["id"]
        now = now or datetime.now(timezone.utc)
        directive = evaluate(
            now, deadline=datetime.fromisoformat(row["deadline_utc"]), state=row["state"],
            last_system_action_at=row["last_system_action_at"], user_acted=user_acted(conn, gw),
            warned=bool(row["deadguard_warned_at"]), triggered=bool(row["deadguard_triggered_at"]),
            warn_min=config.deadguard_warning_minutes(cfg),
            trigger_min=config.deadguard_trigger_minutes(cfg),
            reeval_enabled=config.deadguard_reeval_enabled(cfg),
            lockout_min=config.deadguard_reeval_lockout_minutes(cfg))
        if directive == "system_acted":
            repository.set_gameweek_state(conn, gw, "SYSTEM_ACTED")
        elif directive == "user_acted":
            repository.set_gameweek_state(conn, gw, "USER_ACTED")
        elif directive == "warn":
            send_warning(conn, gw, mins=config.deadguard_trigger_minutes(cfg))
            repository.mark_deadguard_warned(conn, gw)
        elif directive == "trigger":
            _run_trigger(conn, key, gw, cfg)
        elif directive == "reeval":
            _run_reevaluate(conn, key, gw, cfg, apply=True)
        elif directive == "lockout":
            _run_reevaluate(conn, key, gw, cfg, apply=False)
        return directive
    finally:
        if owns:
            conn.close()
