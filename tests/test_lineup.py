from src.execution import lineup


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
    return [{"element": e, "position": e, "is_captain": e == 1, "is_vice_captain": e == 2}
            for e in range(1, 16)]


def _ranker(conn):
    return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                      {"player_id": 6, "web_name": "Vice", "xp": 6.0}],
            "vice_player_id": 6}


def test_run_lineup_dry_run(db):
    sess = _FakeSession(_current())
    res = lineup.run_lineup(db, key=b"unused", live=False, session=sess, ranker=_ranker)
    assert res.dry_run and sess.posted is None
    row = db.execute("SELECT executed, decision_type FROM activity_log").fetchone()
    assert row["executed"] == 0 and row["decision_type"] == "lineup"


def test_run_lineup_live_confirmed(db):
    sess = _FakeSession(_current(), post_status=200)
    res = lineup.run_lineup(db, key=b"unused", live=True, confirm_fn=lambda d: True,
                            session=sess, ranker=_ranker)
    assert not res.dry_run and res.ok and sess.posted is not None
    by_el = {p["element"]: p for p in sess.posted["json"]["picks"]}
    assert by_el[5]["is_captain"] and by_el[6]["is_vice_captain"]
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_run_lineup_live_aborted(db):
    sess = _FakeSession(_current())
    res = lineup.run_lineup(db, key=b"unused", live=True, confirm_fn=lambda d: False,
                            session=sess, ranker=_ranker)
    assert sess.posted is None
    row = db.execute("SELECT action_taken, executed FROM activity_log").fetchone()
    assert row["action_taken"] == "aborted" and row["executed"] == 0
