import pytest
from datetime import datetime, timedelta, timezone
from src.auth import session, master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeTokenSession:
    def __init__(self, *, status_code=200, payload=None):
        self.headers = {}
        self._status = status_code
        self._payload = payload

    def post(self, url, data=None, headers=None, timeout=None):
        return _Resp(status_code=self._status, payload=self._payload)


class _FakeMeSession:
    def __init__(self, *, me_payload, me_status=200):
        self.headers = {}
        self._me_payload = me_payload
        self._me_status = me_status

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def _key(tmp_path):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    return master.init_master_password("throwaway-master-12", s, v)


def test_refresh_access_token_ok():
    fake = _FakeTokenSession(payload={"access_token": "AT", "expires_in": 28800, "refresh_token": "RT2"})
    out = session.refresh_access_token("rt", session=fake)
    assert out["access_token"] == "AT"


def test_refresh_access_token_oauth_error():
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.TokenRefreshError) as exc:
        session.refresh_access_token("rt-throwaway", session=fake)
    assert "invalid_grant" in str(exc.value)
    assert "rt-throwaway" not in str(exc.value)


def test_validate_token_ok():
    fake = _FakeMeSession(me_payload={"player": {"entry": 3122849}})
    assert session.validate_token("AT", expected_team_id=3122849, session=fake) == 3122849


def test_validate_token_team_mismatch():
    fake = _FakeMeSession(me_payload={"player": {"entry": 999}})
    with pytest.raises(session.SessionValidationError):
        session.validate_token("AT", expected_team_id=3122849, session=fake)


def test_validate_token_not_authenticated():
    fake = _FakeMeSession(me_payload={"player": None})
    with pytest.raises(session.SessionValidationError):
        session.validate_token("AT", expected_team_id=3122849, session=fake)


def test_store_tokens_roundtrip(tmp_path, db):
    key = _key(tmp_path)
    exp = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=exp)
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "RT"
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "AT"
    assert repository.get_access_expiry(db) == exp.isoformat()
    assert repository.get_auth_state(db) == "active"


def test_ensure_session_uses_cached_token(tmp_path, db):
    key = _key(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=4)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT-cached", expires_at=future)
    boom = _FakeTokenSession(status_code=500, payload={"error": "should_not_be_called"})
    s = session.ensure_session(db, key, refresh_session=boom)
    assert s.headers["X-Api-Authorization"] == "Bearer AT-cached"


def test_ensure_session_refreshes_when_expired(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT-old", access_token="AT-old", expires_at=past)
    fake = _FakeTokenSession(payload={"access_token": "AT-new", "expires_in": 28800, "refresh_token": "RT-new"})
    s = session.ensure_session(db, key, refresh_session=fake)
    assert s.headers["X-Api-Authorization"] == "Bearer AT-new"
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "AT-new"
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "RT-new"


def test_ensure_session_refresh_failure_expires(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=past)
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.SessionExpired):
        session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_auth_state(db) == "expired"


def test_ensure_session_not_initialized(tmp_path, db):
    key = _key(tmp_path)
    with pytest.raises(session.SessionNotInitialized):
        session.ensure_session(db, key, refresh_session=_FakeTokenSession())


class _BoomTokenSession:
    """A token session whose POST fails at the network layer."""
    headers = {}

    def post(self, *a, **k):
        import requests
        raise requests.ConnectionError("network down")


def test_ensure_session_network_error_is_not_expiry(tmp_path, db):
    import requests
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=past)
    with pytest.raises(requests.RequestException):
        session.ensure_session(db, key, refresh_session=_BoomTokenSession())
    assert repository.get_auth_state(db) == "active"  # a network blip must NOT flip to expired


def test_ensure_session_refresh_failure_increments_relogin(tmp_path, db):
    key = _key(tmp_path)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT", access_token="AT", expires_at=past)
    fake = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    with pytest.raises(session.SessionExpired):
        session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_relogin_failures(db) == 1          # counted this failure


def test_ensure_session_success_resets_relogin(tmp_path, db):
    key = _key(tmp_path)
    repository.increment_relogin_failures(db)                # pretend a prior failure
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.store_tokens(db, key, refresh_token="RT-old", access_token="AT-old", expires_at=past)
    fake = _FakeTokenSession(payload={"access_token": "AT-new", "expires_in": 28800, "refresh_token": "RT-new"})
    session.ensure_session(db, key, refresh_session=fake)
    assert repository.get_relogin_failures(db) == 0          # store_tokens -> mark_session_ok resets
