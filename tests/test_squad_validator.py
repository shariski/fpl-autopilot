import random

from src.decisions.squad_validator import optimize_squad, validate_squad

POOL = [
    {"player_id": i, "web_name": f"P{i}", "team_short": f"T{i % 5}",
     "position": pos, "price": price, "status": "a", "xp_next": 5.0,
     "xp_6gw": 30.0 - i, "value": (30.0 - i) / price}
    for i, (pos, price) in enumerate(
        [("GKP", 5.0)] * 3 + [("DEF", 5.0)] * 7 + [("MID", 7.0)] * 7 + [("FWD", 9.0)] * 5)
]


def _picks(*ids):
    slot_order = ["GKP1", "GKP2", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5",
                  "MID1", "MID2", "MID3", "MID4", "MID5", "FWD1", "FWD2", "FWD3"]
    return [{"player_id": pid, "slot": slot_order[i]} for i, pid in enumerate(ids)]


def test_valid_squad_has_no_problems():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert validate_squad(_picks(*ids), POOL) == []


def test_wrong_position_for_slot():
    ids = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 2, 1]  # GKP2 slot gets DEF
    assert any("position" in p for p in validate_squad(_picks(*ids), POOL))


def test_duplicate_player():
    ids = [0, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert any("duplicate" in p for p in validate_squad(_picks(*ids), POOL))


def test_over_budget():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    expensive = [dict(p, price=p["price"] + 5) for p in POOL]
    assert any("budget" in p for p in validate_squad(_picks(*ids), expensive))


def test_three_per_club():
    ids = [0, 5, 10, 1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14]  # T0,T5,T10 all club 0
    assert any("club" in p for p in validate_squad(_picks(*ids), POOL))


def test_unknown_player_rejected():
    ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 9999]
    assert any("unknown" in p for p in validate_squad(_picks(*ids), POOL))


def test_optimize_fallback_is_always_legal():
    for _ in range(20):
        random.shuffle(POOL)
        picks = optimize_squad(POOL)
        assert len(picks) == 15
        assert validate_squad(picks, POOL) == []
        counts = {}
        for p in picks:
            slot = p["slot"]
            pos = next(x["position"] for x in POOL if x["player_id"] == p["player_id"])
            assert slot.startswith(pos)
            counts[pos] = counts.get(pos, 0) + 1
        assert counts == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
