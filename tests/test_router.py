import pytest
from src.execution import router


def test_route_manual_always_notify():
    assert router.route("manual", "captain", confidence=90) == "notify"
    assert router.route("manual", "transfer", confidence=90, ep_delta=10.0) == "notify"


def test_route_auto_confidence_gate():
    assert router.route("auto", "captain", confidence=80, floor=70) == "execute"
    assert router.route("auto", "captain", confidence=60, floor=70) == "notify"
    assert router.route("auto", "transfer", confidence=80, ep_delta=1.0, floor=70) == "execute"


def test_route_hybrid_captain_conf_gated():
    assert router.route("hybrid", "captain", confidence=80, floor=70) == "execute"
    assert router.route("hybrid", "captain", confidence=60, floor=70) == "notify"  # universal gate


def test_route_hybrid_transfer_threshold():
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=5.0, is_hit=False, floor=70) == "execute"
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=2.0, is_hit=False, floor=70) == "notify"
    assert router.route("hybrid", "transfer", confidence=80, ep_delta=10.0, is_hit=True, floor=70) == "notify"


def test_route_none_confidence_notifies():
    assert router.route("auto", "captain", confidence=None, floor=70) == "notify"


def test_route_chip_or_unknown_notify():
    assert router.route("hybrid", "chip", confidence=99) == "notify"
    assert router.route("weird-mode", "captain", confidence=99) == "notify"


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
        self.got = False

    def get(self, url, timeout=None):
        self.got = True
        return _Resp(200, {"picks": self._current})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def _current():
    return [{"element": e, "position": e, "selling_price": 50 + e,
             "is_captain": e == 1, "is_vice_captain": e == 2} for e in range(1, 16)]


def _ranker(conf=82):
    def f(conn):
        return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                          {"player_id": 6, "web_name": "Vc", "xp": 6.0}],
                "vice_player_id": 6, "confidence": conf}
    return f


def _suggester(conf=80, ep=5.0):
    def f(conn):
        return {"suggestions": [{"out": {"player_id": 7, "web_name": "O", "price": 5.4},
                                 "in": {"player_id": 99, "web_name": "I", "price": 6.0},
                                 "ep_delta_5gw": ep, "hit_cost": 0, "confidence": conf}],
                "empty_reason": None}
    return f


def test_route_gameweek_auto_executes(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="auto",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 5.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes == {"captain": "execute", "transfer": "execute"}
    assert sess.got
    assert sess.posted is None
    assert db.execute("SELECT COUNT(*) c FROM activity_log WHERE executed=1").fetchone()["c"] == 0


def test_route_gameweek_manual_notifies(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    assert all(p["route"] == "notify" for p in plan)
    assert not sess.got and sess.posted is None
    rows = db.execute("SELECT action_taken FROM activity_log").fetchall()
    assert len(rows) == 2 and all(r["action_taken"].startswith("pending") for r in rows)


def test_route_gameweek_hybrid_mixed(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="hybrid",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 2.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes == {"captain": "execute", "transfer": "notify"}


def test_route_gameweek_low_conf_captain_gated(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="hybrid",
                                 session=sess, ranker=_ranker(60), suggester=_suggester(80, 5.0))
    routes = {p["decision"]: p["route"] for p in plan}
    assert routes["captain"] == "notify"


def test_route_gameweek_plan_has_summary_and_executed(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="auto",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 5.0))
    by = {p["decision"]: p for p in plan}
    assert by["captain"]["executed"] is True
    assert "Captain: Cap" in by["captain"]["summary"]
    assert by["transfer"]["executed"] is True
    assert "OUT O" in by["transfer"]["summary"] and "IN I" in by["transfer"]["summary"]


def test_route_gameweek_notify_entries_executed_false(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    assert all(p["executed"] is False for p in plan)
    assert all("pending" in p["summary"].lower() for p in plan)


def test_route_gameweek_entries_carry_identity(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    by = {p["decision"]: p for p in plan}
    assert by["captain"]["identity"] == {"captain_id": 5, "vice_id": 6}
    assert by["transfer"]["identity"] == {"out_id": 7, "in_id": 99}
