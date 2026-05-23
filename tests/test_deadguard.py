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
