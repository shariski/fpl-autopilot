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
