from src import cli
from src.auth import master, crypto
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
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


def _setup_master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_init_fpl_stores_tokens(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-paste-xyz")
    tok = _FakeTokenSession(payload={"access_token": "access-xyz", "expires_in": 28800, "refresh_token": "refresh-rot-xyz"})
    me = _FakeMeSession(me_payload={"player": {"entry": 3122849}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok, me_session=me)
    key = master.load_key("throwaway-master-12", s, v)
    assert crypto.decrypt(key, repository.get_encrypted(db, "access_token_encrypted")) == "access-xyz"
    assert crypto.decrypt(key, repository.get_encrypted(db, "refresh_token_encrypted")) == "refresh-rot-xyz"
    assert repository.get_auth_state(db) == "active"
    out = capsys.readouterr().out
    assert "3122849" in out
    assert "refresh-paste-xyz" not in out and "access-xyz" not in out


def test_init_fpl_rejects_bad_refresh_token(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-bad")
    tok = _FakeTokenSession(status_code=400, payload={"error": "invalid_grant"})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok,
                      me_session=_FakeMeSession(me_payload={}))
    assert repository.get_encrypted(db, "refresh_token_encrypted") is None
    assert "rejected" in capsys.readouterr().out.lower()


def test_init_fpl_rejects_wrong_team(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-ok")
    tok = _FakeTokenSession(payload={"access_token": "access-xyz", "expires_in": 28800})
    me = _FakeMeSession(me_payload={"player": {"entry": 999}})
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v, refresh_session=tok, me_session=me)
    assert repository.get_encrypted(db, "refresh_token_encrypted") is None
    assert "rejected" in capsys.readouterr().out.lower()


def test_init_fpl_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-ok")
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v,
                      refresh_session=_FakeTokenSession(), me_session=_FakeMeSession(me_payload={}))
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"] == 0


def test_auth_status_cli(db, capsys):
    repository.set_auth_state(db, "active")
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "active" in out
    assert "auth_state" in out


class _BoomTokenSession:
    headers = {}

    def post(self, *a, **k):
        import requests
        raise requests.ConnectionError("network down")


def test_init_fpl_network_error_stores_nothing(tmp_path, monkeypatch, db, capsys):
    s, v = _setup_master(tmp_path, monkeypatch)
    monkeypatch.setenv("FPL_REFRESH_TOKEN", "refresh-ok")
    cli._init_fpl_cli(conn=db, salt_path=s, verify_path=v,
                      refresh_session=_BoomTokenSession(), me_session=_FakeMeSession(me_payload={}))
    assert repository.get_encrypted(db, "refresh_token_encrypted") is None
    out = capsys.readouterr().out.lower()
    assert "couldn't reach" in out or "connection" in out
