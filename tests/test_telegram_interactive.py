import types
from datetime import datetime, timezone, timedelta
from src.auth.session import SessionExpired
from src.data import repository
from src.interface import telegram, telegram_interactive as ti


def _configure(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "T")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "42")


def test_is_enabled_requires_config_and_flag(monkeypatch):
    _configure(monkeypatch)
    assert ti.is_enabled({"telegram": {"interactive": True}}) is True
    assert ti.is_enabled({"telegram": {"interactive": False}}) is False
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    assert ti.is_enabled({"telegram": {"interactive": True}}) is False  # unconfigured


def test_send_pending_creates_row_and_buttons(db, monkeypatch):
    _configure(monkeypatch)
    sent = {}
    monkeypatch.setattr(telegram, "send_message",
                        lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    entry = {"decision": "transfer", "summary": "Transfer pending: OUT O IN I",
             "identity": {"out_id": 7, "in_id": 99}}
    ti.send_pending(db, entry, gw=30)
    rows = db.execute("SELECT id, decision_type, status, summary FROM pending_decisions").fetchall()
    assert len(rows) == 1
    pid = rows[0]["id"]
    assert rows[0]["decision_type"] == "transfer" and rows[0]["status"] == "pending"
    assert "Transfer pending: OUT O IN I" in sent["text"]
    assert sent["buttons"] == [[{"text": "✅ Confirm", "callback_data": f"c:{pid}"},
                                {"text": "❌ Reject", "callback_data": f"r:{pid}"}]]


def test_send_pending_noop_unconfigured(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    ti.send_pending(db, {"decision": "captain", "summary": "x", "identity": {"captain_id": 1, "vice_id": 2}},
                    gw=1)
    assert db.execute("SELECT COUNT(*) c FROM pending_decisions").fetchone()["c"] == 0


def test_notify_plan_routes_executed_and_pending(db, monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: calls.append(("notify", k["kind"])))
    monkeypatch.setattr(ti, "send_pending", lambda conn, entry, **k: calls.append(("pending", entry["decision"])))
    plan = [{"decision": "captain", "executed": True, "summary": "Captain: X",
             "identity": {"captain_id": 5, "vice_id": 6}},
            {"decision": "transfer", "executed": False, "summary": "Transfer pending: OUT O IN I",
             "identity": {"out_id": 7, "in_id": 99}}]
    ti.notify_plan(db, plan, gw=30, mode="hybrid")
    assert calls == [("notify", "executed"), ("pending", "transfer")]


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _seed_gw(db, gw=30, deadline=None):
    deadline = deadline or (_NOW + timedelta(hours=1))
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next) VALUES (?, ?, 1)",
               (gw, deadline.isoformat()))
    db.commit()


def _cq(data, chat_id="42", cb_id="cb1"):
    return {"id": cb_id, "data": data, "message": {"chat": {"id": chat_id}}}


def _ranker_caps(captain_id=5, vice_id=6, name="Cap"):
    def f(conn):
        return {"picks": [{"player_id": captain_id, "web_name": name, "xp": 8.0}],
                "vice_player_id": vice_id, "confidence": 80}
    return f


def _suggester_top(out_id=7, in_id=99):
    def f(conn):
        return {"suggestions": [{"out": {"player_id": out_id, "web_name": "O", "price": 5.0},
                                 "in": {"player_id": in_id, "web_name": "I", "price": 6.0},
                                 "ep_delta_5gw": 5.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None}
    return f


def _ok_result(*a, **k):
    return types.SimpleNamespace(ok=True, dry_run=False, status=200)


def test_handle_callback_wrong_chat_ignored(db, monkeypatch):
    _configure(monkeypatch)  # CHAT_ID=42
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}", chat_id="999"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "pending"


def test_handle_callback_reject(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "notify", lambda *a, **k: None)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"r:{pid}"), now=_NOW,
                       transfer_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "rejected"


def test_handle_callback_already_resolved_ignored(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="x")
    repository.set_pending_status(db, pid, "confirmed")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []  # idempotent: not re-executed


def test_handle_callback_confirm_match_executes(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    executed = []

    def fake_transfer(conn, key, **k):
        executed.append((k.get("live"), k.get("rank")))
        return _ok_result()

    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=_suggester_top(7, 99), transfer_fn=fake_transfer)
    assert executed == [(True, 1)]
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "confirmed"
    assert "executed" in notes


def test_handle_callback_confirm_changed_supersedes(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    executed = []
    # suggester now returns a DIFFERENT in player (88 != 99)
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=_suggester_top(7, 88), transfer_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "superseded"
    # a NEW pending row was created for the changed recommendation
    assert db.execute("SELECT COUNT(*) c FROM pending_decisions WHERE status='pending'").fetchone()["c"] == 1


def test_handle_callback_confirm_past_deadline_expires(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db, deadline=_NOW - timedelta(hours=1))  # already passed
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="x")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "expired"


def test_handle_callback_confirm_execution_failure_marks_failed(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))

    def boom(conn, key, **k):
        raise SessionExpired("expired")

    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=boom)
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "failed"
    assert "alert" in alerts


def test_poll_once_noop_when_disabled(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    called = []
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: called.append(1) or [])
    ti.poll_once(b"key", conn=db)
    assert called == []


def test_poll_once_dispatches_and_advances_offset(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 10, "callback_query": {"id": "a", "data": "r:1"}},
               {"update_id": 11, "callback_query": {"id": "b", "data": "r:2"}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    seen = []
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: seen.append(cq["id"]))
    ti.poll_once(b"key", conn=db)
    assert seen == ["a", "b"]
    assert repository.get_telegram_state(db, "update_offset") == "12"  # last update_id + 1


def test_poll_once_passes_stored_offset(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    repository.set_telegram_state(db, "update_offset", "5")
    seen_offset = []
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: seen_offset.append(offset) or [])
    ti.poll_once(b"key", conn=db)
    assert seen_offset == [5]


def test_handle_callback_unknown_action_ignored(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"z:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "pending"


def test_handle_callback_recompute_failure_marks_failed(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))

    def boom_suggester(conn):
        raise RuntimeError("ranker exploded")

    executed = []
    # must NOT raise out of handle_callback (poller-safety invariant)
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=boom_suggester, transfer_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "failed"
    assert "alert" in alerts


def test_handle_callback_confirm_result_not_ok_marks_failed(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=_suggester_top(7, 99),
                       transfer_fn=lambda *a, **k: types.SimpleNamespace(ok=False))
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "failed"
    assert "alert" in alerts


def test_handle_callback_confirm_lineup_match_executes(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    executed = []

    def fake_lineup(conn, key, **k):
        executed.append(k.get("live"))
        return _ok_result()

    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(5, 6), lineup_fn=fake_lineup)
    assert executed == [True]
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "confirmed"
    assert "executed" in notes


def test_poll_once_advances_offset_when_handle_raises(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 20, "callback_query": {"id": "x", "data": "c:1"}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)

    def boom(conn, key, cq, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ti, "handle_callback", boom)
    ti.poll_once(b"key", conn=db)   # must NOT raise
    assert repository.get_telegram_state(db, "update_offset") == "21"


def test_poll_once_routes_keep_callback_to_deadguard(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 30, "callback_query": {"id": "k", "data": "k:30", "message": {"chat": {"id": "42"}}}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    from src.interface import deadguard
    kept = []
    monkeypatch.setattr(deadguard, "handle_keep", lambda conn, cq, **k: kept.append(cq["data"]))
    cr = []
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: cr.append(cq["data"]))
    ti.poll_once(b"key", conn=db)
    assert kept == ["k:30"] and cr == []


def test_handle_freeze_sets_frozen_and_offers_unfreeze(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)                               # CHAT_ID=42
    sent = {}
    monkeypatch.setattr(telegram, "send_message",
                        lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    ti.handle_freeze(db, _cq("f:1"))
    assert override.is_frozen(db) is True
    assert sent["buttons"] == [[{"text": "▶️ Unfreeze", "callback_data": "u:1"}]]


def test_handle_freeze_wrong_chat_ignored(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    ti.handle_freeze(db, _cq("f:1", chat_id="999"))
    assert override.is_frozen(db) is False


def test_handle_unfreeze_clears(db, monkeypatch):
    from src.execution import override
    _configure(monkeypatch)
    override.freeze(db, reason="x", source="user")
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    ti.handle_unfreeze(db, _cq("u:1"))
    assert override.is_frozen(db) is False


def test_poll_once_routes_freeze_and_unfreeze(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 40, "callback_query": {"id": "f", "data": "f:1", "message": {"chat": {"id": "42"}}}},
               {"update_id": 41, "callback_query": {"id": "u", "data": "u:1", "message": {"chat": {"id": "42"}}}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    froze, thawed, confirms = [], [], []
    monkeypatch.setattr(ti, "handle_freeze", lambda conn, cq, **k: froze.append(cq["id"]))
    monkeypatch.setattr(ti, "handle_unfreeze", lambda conn, cq, **k: thawed.append(cq["id"]))
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: confirms.append(cq["id"]))
    ti.poll_once(b"key", conn=db)
    assert froze == ["f"] and thawed == ["u"] and confirms == []
