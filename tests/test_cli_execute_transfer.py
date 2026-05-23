from src import cli
from src.auth import master


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, current, post_status=200):
        self._current = current
        self._post_status = post_status
        self.posted = None

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._current})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def _current():
    return [{"element": e, "position": e, "selling_price": 50 + e,
             "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]


def _suggester(conn):
    return {"suggestions": [
        {"out": {"player_id": 7, "web_name": "OutA", "price": 5.4},
         "in": {"player_id": 99, "web_name": "InA", "price": 6.0},
         "ep_delta_5gw": 3.1, "hit_cost": 0, "confidence": None},
    ], "empty_reason": None}


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_execute_transfer_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current())
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False,
                              session=sess, suggester=_suggester)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 0


def test_execute_transfer_live_confirmed(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current(), post_status=200)
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=True,
                              session=sess, suggester=_suggester, confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert "Submitted" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_execute_transfer_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    cli._execute_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False,
                              session=_FakeSession(_current()), suggester=_suggester)
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
