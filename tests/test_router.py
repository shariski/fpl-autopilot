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
