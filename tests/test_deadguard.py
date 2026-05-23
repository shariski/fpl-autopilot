from src.data import repository


def _seed_gw(db, gw=30):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next) VALUES (?, '2026-05-23T18:00:00+00:00', 1)", (gw,))
    db.commit()


def test_deadguard_warned_at_column_exists(db):
    _seed_gw(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(gameweeks)")}
    assert "deadguard_warned_at" in cols


def test_set_gameweek_state(db):
    _seed_gw(db)
    repository.set_gameweek_state(db, 30, "DEADGUARD_ACTIVE")
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_ACTIVE"


def test_mark_deadguard_warned_and_triggered(db):
    _seed_gw(db)
    repository.mark_deadguard_warned(db, 30)
    repository.mark_deadguard_triggered(db, 30)
    row = db.execute("SELECT deadguard_warned_at, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_warned_at"] is not None
    assert row["deadguard_triggered_at"] is not None


def test_touch_user_action_sets_state_and_timestamp(db):
    _seed_gw(db)
    repository.touch_user_action(db, 30)
    row = db.execute("SELECT state, last_user_action_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED"
    assert row["last_user_action_at"] is not None


# ---------------------------------------------------------------------------
# Task 3: Pure evaluate() state machine
# ---------------------------------------------------------------------------
from datetime import datetime, timezone, timedelta
from src.interface import deadguard

_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _ev(deadline_mins, **kw):
    base = dict(deadline=_NOW + timedelta(minutes=deadline_mins), state="PENDING",
                last_system_action_at=None, user_acted=False, warned=False, triggered=False,
                warn_min=120, trigger_min=30)
    base.update(kw)
    return deadguard.evaluate(_NOW, **base)


def test_evaluate_before_warning_window_noop():
    assert _ev(300) == "noop"


def test_evaluate_warning_window_warns_once():
    assert _ev(90) == "warn"
    assert _ev(90, warned=True) == "noop"


def test_evaluate_trigger_window_triggers_once():
    assert _ev(20) == "trigger"
    assert _ev(20, triggered=True) == "noop"


def test_evaluate_past_deadline_noop():
    assert _ev(-5) == "noop"


def test_evaluate_system_acted_takes_precedence():
    assert _ev(20, last_system_action_at="2026-05-23T11:00:00+00:00") == "system_acted"


def test_evaluate_user_acted_takes_precedence():
    assert _ev(20, user_acted=True) == "user_acted"


def test_evaluate_resolved_state_noop():
    for s in ("USER_ACTED", "SYSTEM_ACTED", "DEADGUARD_EXECUTED", "DEADGUARD_SKIPPED"):
        assert _ev(20, state=s) == "noop"


# ---------------------------------------------------------------------------
# Task 4: user_acted, send_warning, handle_keep
# ---------------------------------------------------------------------------
from src.interface import telegram


def _configure_tg(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "T")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "42")


def test_user_acted_false_when_nothing(db):
    _seed_gw(db)
    assert deadguard.user_acted(db, 30) is False


def test_user_acted_true_when_last_user_action_set(db):
    _seed_gw(db)
    repository.touch_user_action(db, 30)
    assert deadguard.user_acted(db, 30) is True


def test_user_acted_true_on_confirmed_pending(db):
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 1, "vice_id": 2}, summary="x")
    repository.set_pending_status(db, pid, "confirmed")
    assert deadguard.user_acted(db, 30) is True


def test_user_acted_false_on_superseded_pending(db):
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 1, "vice_id": 2}, summary="x")
    repository.set_pending_status(db, pid, "superseded")
    assert deadguard.user_acted(db, 30) is False


def test_send_warning_sends_keep_button(db, monkeypatch):
    _configure_tg(monkeypatch)
    sent = {}
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    deadguard.send_warning(db, 30, mins=30)
    assert "Keep" in sent["text"] or "keep" in sent["text"]
    assert sent["buttons"] == [[{"text": "✅ Keep as is", "callback_data": "k:30"}]]


def test_handle_keep_sets_user_acted(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw(db)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    cq = {"id": "cb", "data": "k:30", "message": {"chat": {"id": "42"}}}
    deadguard.handle_keep(db, cq)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "USER_ACTED"


def test_handle_keep_wrong_chat_ignored(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw(db)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    cq = {"id": "cb", "data": "k:30", "message": {"chat": {"id": "999"}}}
    deadguard.handle_keep(db, cq)
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"


# ---------------------------------------------------------------------------
# Task 5: run_deadguard_job + _run_trigger
# ---------------------------------------------------------------------------
import types

_CFG = {"deadguard": {"enabled": True, "warning_window_minutes": 120, "trigger_window_minutes": 30}}


def _seed_gw_dl(db, deadline, gw=30, state="PENDING", last_system=None):
    db.execute("DELETE FROM gameweeks WHERE id=?", (gw,))
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, state, last_system_action_at) "
               "VALUES (?, ?, 1, ?, ?)", (gw, deadline.isoformat(), state, last_system))
    db.commit()


def test_job_warns_in_warning_window(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=90))
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: sent.append(text) or True)
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "warn"
    assert sent and db.execute("SELECT deadguard_warned_at FROM gameweeks WHERE id=30").fetchone()["deadguard_warned_at"] is not None


def test_job_triggers_and_sets_captain(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: called.append(k.get("live")) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "trigger" and called == [True]
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"


def test_job_skips_when_no_pick(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [], "vice_player_id": None, "confidence": 0})
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert called == []
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_SKIPPED"


def test_job_system_acted_suppresses(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), last_system="2026-05-23T11:00:00+00:00")
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert out == "system_acted" and called == []
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "SYSTEM_ACTED"


def test_job_disabled_returns_none(db, monkeypatch):
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    out = deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg={"deadguard": {"enabled": False}})
    assert out is None and called == []


def test_job_session_expired_leaves_retryable(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    from src.auth.session import SessionExpired
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})

    def boom(conn, key, **k):
        raise SessionExpired("expired")

    monkeypatch.setattr(deadguard.lineup, "run_lineup", boom)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    row = db.execute("SELECT state, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_triggered_at"] is None        # NOT marked -> retryable next tick
    assert row["state"] == "DEADGUARD_ACTIVE"
    assert "alert" in alerts


def test_user_acted_false_on_expired_or_failed(db):
    _seed_gw(db)
    for st in ("expired", "failed"):
        pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                                 identity={"captain_id": 1, "vice_id": 2}, summary="x")
        repository.set_pending_status(db, pid, st)
    assert deadguard.user_acted(db, 30) is False


def test_job_generic_exception_leaves_retryable(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})

    def boom(conn, key, **k):
        raise RuntimeError("network")

    monkeypatch.setattr(deadguard.lineup, "run_lineup", boom)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    row = db.execute("SELECT state, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_triggered_at"] is None       # not marked -> retryable next tick
    assert row["state"] == "DEADGUARD_ACTIVE"
    assert "alert" in alerts


def test_evaluate_window_boundaries():
    assert _ev(30) == "trigger"     # mins == trigger_min (inclusive <=)
    assert _ev(120) == "warn"       # mins == warn_min (inclusive <=)


def test_job_trigger_result_not_ok_retryable(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=False, dry_run=False, status=500))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    row = db.execute("SELECT state, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_triggered_at"] is None              # NOT marked -> retryable
    assert row["state"] == "DEADGUARD_ACTIVE"
    assert "alert" in alerts and "executed" not in alerts


def test_handle_keep_non_digit_payload_no_crash(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw(db)
    answered = []
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: answered.append(1) or True)
    cq = {"id": "cb", "data": "k:abc", "message": {"chat": {"id": "42"}}}
    deadguard.handle_keep(db, cq)
    assert answered == [1]
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "PENDING"


def test_job_no_deadline_returns_none(db, monkeypatch):
    db.execute("DELETE FROM gameweeks WHERE id=30")
    db.execute("INSERT INTO gameweeks (id, is_next, state) VALUES (30, 1, 'PENDING')")
    db.commit()
    called = []
    monkeypatch.setattr(deadguard.lineup, "run_lineup", lambda *a, **k: called.append(1))
    assert deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG) is None
    assert called == []


# ---------------------------------------------------------------------------
# Task 5: _pick_flagged_transfer + _player_status
# ---------------------------------------------------------------------------
_SUGG = {"suggestions": [{
    "out": {"player_id": 7, "web_name": "Out"}, "in": {"player_id": 99, "web_name": "In"},
    "ep_delta_5gw": 5.0, "hit_cost": 0, "confidence": 80}], "empty_reason": None}


def _seed_player_status(db, pid, status):
    db.execute("INSERT INTO players (id, web_name, status) VALUES (?, ?, ?)", (pid, f"P{pid}", status))
    db.commit()


def test_pick_flagged_transfer_returns_rank_when_qualifying(db, monkeypatch):
    _seed_player_status(db, 7, "i")   # flagged out
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    assert deadguard._pick_flagged_transfer(db, _CFG) == 1


def test_pick_flagged_transfer_none_when_out_available(db, monkeypatch):
    _seed_player_status(db, 7, "a")   # not flagged
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_on_hit(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "hit_cost": -4}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_below_threshold(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "ep_delta_5gw": 2.0}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_low_confidence(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    sugg = {"suggestions": [{**_SUGG["suggestions"][0], "confidence": 50}], "empty_reason": None}
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: sugg)
    assert deadguard._pick_flagged_transfer(db, _CFG) is None


def test_pick_flagged_transfer_none_when_disabled(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn: _SUGG)
    cfg = {"deadguard": {"scope": {"transfer_if_flagged": False}}}
    assert deadguard._pick_flagged_transfer(db, cfg) is None


# ---------------------------------------------------------------------------
# Task 6: _run_trigger bench + transfer; run_deadguard_job passes cfg
# ---------------------------------------------------------------------------
def test_trigger_optimizes_bench_and_no_transfer(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    lineup_kwargs = {}
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: lineup_kwargs.update(k) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: None)
    xfers = []
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer", lambda *a, **k: xfers.append(1))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert lineup_kwargs.get("optimize_bench") is True
    assert xfers == []                                    # no qualifying transfer
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"
    assert "executed" in notes


def test_trigger_executes_flagged_transfer(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: 2)
    xfers = []
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer",
                        lambda conn, key, **k: xfers.append(k.get("rank")) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert xfers == [2]                                    # ran the chosen rank, live
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"


def test_trigger_transfer_failure_keeps_lineup(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: 1)

    def boom(conn, key, **k):
        raise RuntimeError("transfer api down")

    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer", boom)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    row = db.execute("SELECT state, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "DEADGUARD_EXECUTED" and row["deadguard_triggered_at"] is not None
    assert "alert" in alerts


def test_trigger_summary_log_failure_still_notifies(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20))
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: None)

    def boom_log(conn, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(deadguard.repository, "log_activity", boom_log)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)   # must NOT raise
    assert "executed" in notes                                          # notify fired despite log failure
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_EXECUTED"


def test_pick_flagged_transfer_none_on_any_negative_hit(db, monkeypatch):
    _seed_player_status(db, 7, "i")
    for hc in (-1, -4, -8):
        sugg = {"suggestions": [{**_SUGG["suggestions"][0], "hit_cost": hc}], "empty_reason": None}
        monkeypatch.setattr(deadguard.transfers, "get_transfer_suggestions", lambda conn, s=sugg: s)
        assert deadguard._pick_flagged_transfer(db, _CFG) is None
