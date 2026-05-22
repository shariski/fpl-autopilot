import json
import pytest
import requests
from src.auth import session, master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Returns a canned /me response; ignores cookies (logic is what we test)."""

    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _key(tmp_path):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    return master.init_master_password("throwaway-master-12", s, v)


def _store_cookies(db, key, cookies):
    repository.set_encrypted(db, "session_cookie_encrypted", crypto.encrypt(key, json.dumps(cookies)))


def test_ensure_session_valid(tmp_path, db):
    key = _key(tmp_path)
    _store_cookies(db, key, {"pl_profile": "abc"})
    repository.mark_session_ok(db)
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    called = []
    out = session.ensure_session(db, key, expected_team_id=3122849,
                                 login_fn=lambda *a, **k: called.append(1), session=fake)
    assert out is fake
    assert not called  # no re-login when the session is valid
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_not_initialized(tmp_path, db):
    key = _key(tmp_path)
    fake = _FakeSession(me_payload={"player": {"entry": 3122849}})
    with pytest.raises(session.SessionNotInitialized):
        session.ensure_session(db, key, expected_team_id=3122849, session=fake)


def test_ensure_session_frozen_refuses(tmp_path, db):
    key = _key(tmp_path)
    _store_cookies(db, key, {"pl_profile": "abc"})
    repository.set_auth_state(db, "frozen")
    called = []
    with pytest.raises(session.SessionFrozen):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=lambda *a, **k: called.append(1),
                               session=_FakeSession(me_payload={}))
    assert not called  # frozen refuses without attempting login


def _store_creds(db, key, cookies):
    _store_cookies(db, key, cookies)
    repository.set_encrypted(db, "fpl_email_encrypted", crypto.encrypt(key, "me@example.com"))
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "throwaway-fpl-pw"))


def test_ensure_session_relogin_ok(tmp_path, db):
    from src.auth import fpl_login
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    expired = _FakeSession(me_payload={}, me_status=200)  # no player -> expired
    fresh = fpl_login.LoginResult(cookies={"pl_profile": "fresh"}, csrf="t2", entry_id=3122849)
    out = session.ensure_session(db, key, expected_team_id=3122849,
                                 login_fn=lambda *a, **k: fresh, session=expired)
    assert isinstance(out, requests.Session)
    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0
    stored = json.loads(crypto.decrypt(key, repository.get_encrypted(db, "session_cookie_encrypted")))
    assert stored == {"pl_profile": "fresh"}


def _failing_login(*a, **k):
    from src.auth.fpl_login import FPLLoginError
    raise FPLLoginError("bad creds")


def test_ensure_session_relogin_fails_once(tmp_path, db):
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    with pytest.raises(session.ReloginFailed):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    assert repository.get_auth_state(db) == "expired"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 1


def test_ensure_session_freezes_after_two(tmp_path, db):
    key = _key(tmp_path)
    _store_creds(db, key, {"pl_profile": "stale"})
    repository.mark_session_ok(db)
    # first failed re-login
    with pytest.raises(session.ReloginFailed):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    # second consecutive failure -> frozen
    with pytest.raises(session.SessionFrozen):
        session.ensure_session(db, key, expected_team_id=3122849,
                               login_fn=_failing_login, session=_FakeSession(me_payload={}))
    assert repository.get_auth_state(db) == "frozen"
