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
