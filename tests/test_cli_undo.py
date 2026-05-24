from src import cli
from src.auth import master
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _UndoSession:
    def __init__(self, picks, post_status=200):
        self._picks = picks
        self._post_status = post_status
        self.posted = None
        self.headers = {}

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._picks})

    def post(self, url, json=None, timeout=None):
        self.posted = json
        return _Resp(self._post_status, {})


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def _seed(db):
    from datetime import datetime, timezone, timedelta
    deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.execute("INSERT INTO gameweeks (id, is_next, finished, state, deadline_utc) VALUES (30, 1, 0, 'DEADGUARD_EXECUTED', ?)",
               (deadline,))
    db.execute("INSERT INTO players (id, web_name, price) VALUES (7, 'Out', 5.4)")
    db.commit()
    repository.set_deadguard_transfer(db, 30, 7, 99)


def test_undo_cli_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    _seed(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])
    cli._undo_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False, session=sess)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out


def test_undo_cli_live(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    _seed(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}], post_status=200)
    cli._undo_transfer_cli(conn=db, salt_path=s, verify_path=v, live=True, session=sess,
                           confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "USER_ACTED"
