from src.interface import telegram


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "123")
    assert telegram.is_configured() is True


def test_is_configured_false_when_only_token(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload


class _FakeSession:
    """Mimics requests.Session.post (json=, timeout=)."""
    def __init__(self, resp=None, boom=False):
        self._resp = resp or _Resp()
        self._boom = boom
        self.posted = None

    def post(self, url, json=None, timeout=None):
        if self._boom:
            import requests
            raise requests.RequestException("boom")
        self.posted = {"url": url, "json": json, "timeout": timeout}
        return self._resp


def _configure(monkeypatch, token="TOK", chat="42"):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, token)
    monkeypatch.setenv(telegram.CHAT_ID_ENV, chat)


def test_send_message_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sess = _FakeSession()
    assert telegram.send_message("hi", session=sess) is False
    assert sess.posted is None


def test_send_message_posts_and_returns_true(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    assert telegram.send_message("hello", session=sess) is True
    assert sess.posted["url"] == "https://api.telegram.org/botTOK/sendMessage"
    assert sess.posted["json"] == {"chat_id": "42", "text": "hello"}


def test_send_message_includes_buttons(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    btns = [[{"text": "Yes", "callback_data": "y"}]]
    telegram.send_message("q", buttons=btns, session=sess)
    assert sess.posted["json"]["reply_markup"] == {"inline_keyboard": btns}


def test_send_message_false_on_non_200(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(500, {}))) is False


def test_send_message_false_on_ok_false(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(200, {"ok": False}))) is False


def test_send_message_false_on_network_error(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(boom=True)) is False


def test_format_executed():
    assert telegram._format("executed", "Captain: X") == "✅ Executed\nCaptain: X"


def test_format_info_has_review_suffix():
    out = telegram._format("info", "Captain pending: X")
    assert out == "📊 Decision pending\nCaptain pending: X\nReview before the deadline."


def test_format_alert():
    assert telegram._format("alert", "session expired").startswith("❌ Autopilot blocked")


def test_notify_noop_unconfigured_no_log(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(1) or True)
    assert telegram.notify(db, kind="info", decision_type="captain", mode="manual", summary="s") is False
    assert sent == []
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_success_no_failure_log(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    assert telegram.notify(db, kind="executed", decision_type="captain", mode="auto",
                           summary="Captain: X") is True
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_failure_logs_one_row_without_token(db, monkeypatch):
    _configure(monkeypatch, token="SECRET_TOKEN")
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: False)
    assert telegram.notify(db, kind="info", decision_type="transfer", mode="hybrid",
                           summary="OUT A IN B") is False
    rows = db.execute(
        "SELECT decision_type, action_taken, inputs_json, executed FROM activity_log").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["decision_type"] == "notification"
    assert r["executed"] == 0
    assert "SECRET_TOKEN" not in (r["action_taken"] + (r["inputs_json"] or ""))
