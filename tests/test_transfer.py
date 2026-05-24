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
    assert sess.posted["url"] == "https://fantasy.premierleague.com/api/transfers/"
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


def _seed_next_gw_and_player(db, out_id=7, out_price=5.4):
    db.execute("INSERT INTO gameweeks (id, is_next, finished) VALUES (30, 1, 0)")
    db.execute("INSERT INTO players (id, web_name, price) VALUES (?, 'Out', ?)", (out_id, out_price))
    db.commit()


def test_run_undo_transfer_builds_reverse_payload(db):
    from src.execution import transfer as transfer_mod
    _seed_next_gw_and_player(db, out_id=7, out_price=5.4)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])
    res = transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=True,
                                         confirm_fn=lambda d: True, session=sess)
    assert res.ok
    t = sess.posted["transfers"][0]
    assert t["element_out"] == 99 and t["element_in"] == 7
    assert t["selling_price"] == 60
    assert t["purchase_price"] == 54


def test_run_undo_transfer_dry_run_does_not_post(db):
    from src.execution import transfer as transfer_mod
    _seed_next_gw_and_player(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])
    res = transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=False, session=sess)
    assert res.dry_run is True and sess.posted is None


def test_run_undo_transfer_in_player_gone_raises(db):
    from src.execution import transfer as transfer_mod
    from src.execution import executor as executor_mod
    _seed_next_gw_and_player(db)
    sess = _UndoSession([{"element": 11, "selling_price": 50}])
    with pytest.raises(executor_mod.ExecutorError):
        transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=True,
                                       confirm_fn=lambda d: True, session=sess)


def test_run_undo_transfer_out_player_unknown_raises(db):
    import pytest
    from src.execution import transfer as transfer_mod
    from src.execution import executor as executor_mod
    db.execute("INSERT INTO gameweeks (id, is_next, finished) VALUES (30, 1, 0)")   # no players row for out_id 7
    db.commit()
    sess = _UndoSession([{"element": 99, "selling_price": 60}])                      # in_id present; out_id not in players
    with pytest.raises(executor_mod.ExecutorError):
        transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=True,
                                       confirm_fn=lambda d: True, session=sess)
