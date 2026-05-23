import json
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
    ti.send_pending(db, entry, gw=30, mode="manual")
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
                    gw=1, mode="manual")
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
