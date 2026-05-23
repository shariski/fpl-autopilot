import pytest
from src.execution import executor


def _picks():
    return [
        {"element": 1, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False},
        {"element": 2, "position": 2, "multiplier": 1, "is_captain": False, "is_vice_captain": True},
        {"element": 3, "position": 3, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
    ]


def test_build_lineup_payload_sets_flags_and_preserves():
    out = executor.build_lineup_payload(_picks(), captain_id=2, vice_id=3)
    assert out["chip"] is None
    by_el = {p["element"]: p for p in out["picks"]}
    assert by_el[2]["is_captain"] and not by_el[2]["is_vice_captain"]
    assert by_el[3]["is_vice_captain"] and not by_el[3]["is_captain"]
    assert not by_el[1]["is_captain"] and not by_el[1]["is_vice_captain"]
    assert [p["position"] for p in out["picks"]] == [1, 2, 3]
    assert set(out["picks"][0]) == {"element", "position", "is_captain", "is_vice_captain"}


def test_build_lineup_payload_captain_equals_vice():
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_picks(), captain_id=2, vice_id=2)


def test_build_lineup_payload_captain_not_in_squad():
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_picks(), captain_id=99, vice_id=3)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, me=None, me_status=200, post_status=200):
        self._me = me
        self._me_status = me_status
        self._post_status = post_status
        self.posted = None

    def get(self, url, timeout=None):
        return _Resp(self._me_status, self._me)

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def test_fetch_current_picks_ok():
    sess = _FakeSession(me={"picks": _picks()})
    assert executor.fetch_current_picks(sess, 3122849) == _picks()


def test_fetch_current_picks_non_200():
    sess = _FakeSession(me_status=403)
    with pytest.raises(executor.ExecutorError):
        executor.fetch_current_picks(sess, 3122849)


def test_apply_lineup_dry_run_sends_nothing():
    sess = _FakeSession()
    res = executor.apply_lineup(sess, 3122849, {"chip": None, "picks": []}, dry_run=True)
    assert res.dry_run and res.ok and res.status is None
    assert res.request["method"] == "POST"
    assert "my-team/3122849" in res.request["url"]
    assert sess.posted is None


def test_apply_lineup_live_posts():
    sess = _FakeSession(post_status=200)
    payload = {"chip": None, "picks": []}
    res = executor.apply_lineup(sess, 3122849, payload, dry_run=False)
    assert not res.dry_run and res.ok and res.status == 200
    assert sess.posted["json"] == payload
    assert "my-team/3122849" in sess.posted["url"]


def test_apply_lineup_live_non_200():
    sess = _FakeSession(post_status=403)
    res = executor.apply_lineup(sess, 3122849, {"chip": None, "picks": []}, dry_run=False)
    assert not res.ok and res.status == 403


def test_build_transfer_payload_shape():
    out = executor.build_transfer_payload(entry=3122849, event=38, element_out=7,
                                          element_in=99, selling_price=57, purchase_price=60)
    assert out["chip"] is None
    assert out["entry"] == 3122849 and out["event"] == 38
    assert out["transfers"] == [{"element_in": 99, "element_out": 7,
                                 "purchase_price": 60, "selling_price": 57}]


def test_apply_transfers_dry_run_sends_nothing():
    sess = _FakeSession()
    res = executor.apply_transfers(sess, 3122849, {"transfers": []}, dry_run=True)
    assert res.dry_run and res.ok and res.status is None
    assert "entry/3122849/transfers" in res.request["url"]
    assert sess.posted is None


def test_apply_transfers_live_posts():
    sess = _FakeSession(post_status=200)
    payload = {"chip": None, "entry": 3122849, "event": 38, "transfers": []}
    res = executor.apply_transfers(sess, 3122849, payload, dry_run=False)
    assert not res.dry_run and res.ok and res.status == 200
    assert sess.posted["json"] == payload
    assert "entry/3122849/transfers" in sess.posted["url"]


def test_apply_transfers_live_non_200():
    sess = _FakeSession(post_status=400)
    res = executor.apply_transfers(sess, 3122849, {"transfers": []}, dry_run=False)
    assert not res.ok and res.status == 400


def _full_picks():
    return [{"element": e, "position": e, "selling_price": 50,
             "is_captain": False, "is_vice_captain": False} for e in range(1, 16)]


def test_build_lineup_payload_reorders_bench():
    from src.execution import executor
    payload = executor.build_lineup_payload(_full_picks(), captain_id=1, vice_id=2,
                                            bench_order=[15, 13, 14])
    pos = {p["element"]: p["position"] for p in payload["picks"]}
    assert pos[15] == 13 and pos[13] == 14 and pos[14] == 15   # reassigned in given order
    assert pos[1] == 1 and pos[11] == 11 and pos[12] == 12     # starters + sub-GK unchanged
    caps = {p["element"]: p["is_captain"] for p in payload["picks"]}
    vices = {p["element"]: p["is_vice_captain"] for p in payload["picks"]}
    assert caps[1] is True and vices[2] is True


def test_build_lineup_payload_bad_bench_order_raises():
    import pytest
    from src.execution import executor
    with pytest.raises(executor.ExecutorError):
        executor.build_lineup_payload(_full_picks(), 1, 2, bench_order=[13, 14, 99])  # 99 not on bench


def test_build_lineup_payload_none_unchanged():
    from src.execution import executor
    payload = executor.build_lineup_payload(_full_picks(), 1, 2)
    pos = {p["element"]: p["position"] for p in payload["picks"]}
    assert pos[13] == 13 and pos[14] == 14 and pos[15] == 15
