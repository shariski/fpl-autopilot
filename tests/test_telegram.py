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
