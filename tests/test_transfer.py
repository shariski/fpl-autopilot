import pytest
from src.execution import transfer, executor


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
        {"out": {"player_id": 8, "web_name": "OutB", "price": 5.0},
         "in": {"player_id": 98, "web_name": "InB", "price": 5.5},
         "ep_delta_5gw": 2.0, "hit_cost": 0, "confidence": None},
    ], "empty_reason": None}


def _empty(conn):
    return {"suggestions": [], "empty_reason": "no squad snapshot yet"}


def test_run_transfer_dry_run_uses_live_selling_price(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", live=False, session=sess, suggester=_suggester)
    assert res.dry_run and sess.posted is None
    t = res.request["body"]["transfers"][0]
    assert t["element_out"] == 7 and t["element_in"] == 99
    assert t["selling_price"] == 57          # from /my-team, NOT out.price*10 (54)
    assert t["purchase_price"] == 60         # round(in.price * 10)
    row = db.execute("SELECT executed, decision_type FROM activity_log").fetchone()
    assert row["executed"] == 0 and row["decision_type"] == "transfer"


def test_run_transfer_rank_2(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", rank=2, live=False, session=sess, suggester=_suggester)
    t = res.request["body"]["transfers"][0]
    assert t["element_out"] == 8 and t["element_in"] == 98


def test_run_transfer_live_confirmed(db):
    sess = _FakeSession(_current(), post_status=200)
    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                                session=sess, suggester=_suggester)
    assert not res.dry_run and res.ok and sess.posted is not None
    assert "entry/" in sess.posted["url"] and sess.posted["url"].endswith("/transfers/")
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_run_transfer_live_aborted(db):
    sess = _FakeSession(_current())
    res = transfer.run_transfer(db, key=b"unused", live=True, confirm_fn=lambda d: False,
                                session=sess, suggester=_suggester)
    assert sess.posted is None
    row = db.execute("SELECT action_taken, executed FROM activity_log").fetchone()
    assert row["action_taken"] == "aborted" and row["executed"] == 0


def test_run_transfer_no_suggestions(db):
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", session=_FakeSession(_current()), suggester=_empty)


def test_run_transfer_rank_out_of_range(db):
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", rank=9, session=_FakeSession(_current()),
                              suggester=_suggester)


def test_run_transfer_out_not_in_squad(db):
    sess = _FakeSession([p for p in _current() if p["element"] != 7])
    with pytest.raises(executor.ExecutorError):
        transfer.run_transfer(db, key=b"unused", session=sess, suggester=_suggester)
