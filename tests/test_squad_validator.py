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
    # pool: 0-2 GKP, 3-9 DEF, 10-16 MID, 17-21 FWD
    ids = [0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19]
    assert validate_squad(_picks(*ids), POOL) == []


def test_wrong_position_for_slot():
    ids = [0, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19, 2]  # FWD3 slot gets GKP
    assert any("position" in p for p in validate_squad(_picks(*ids), POOL))


def test_duplicate_player():
    ids = [0, 0, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19]
    assert any("duplicate" in p for p in validate_squad(_picks(*ids), POOL))


def test_over_budget():
    ids = [0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19]
    expensive = [dict(p, price=p["price"] + 5) for p in POOL]
    assert any("budget" in p for p in validate_squad(_picks(*ids), expensive))


def test_three_per_club():
    # players 0, 5, 10, 15 are all club T0 -> 4 from one club
    ids = [0, 5, 10, 15, 1, 3, 4, 6, 7, 11, 12, 13, 14, 17, 18]
    assert any("club" in p for p in validate_squad(_picks(*ids), POOL))


def test_unknown_player_rejected():
    ids = [0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 9999]
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


def test_repair_budget_fixes_over_budget_squad():
    from src.decisions.squad_validator import repair_budget

    pool = [dict(p) for p in POOL]
    pool.append({"player_id": 50, "web_name": "CheapGK", "team_short": "T9",
                 "position": "GKP", "price": 4.0, "status": "a", "xp_next": 1.0,
                 "xp_6gw": 6.0, "value": 1.5})
    picks = _picks(0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19)
    # bump GKP1's price by 5 -> 102 total (over budget)
    bumped = [dict(p, price=p["price"] + 5 if p["player_id"] == 0 else p["price"])
              for p in pool]
    assert any("budget" in p for p in validate_squad(picks, bumped))
    repaired = repair_budget(picks, bumped)
    assert repaired is not None
    assert validate_squad(repaired, bumped) == []
    # GKP1's slot now holds the cheap GK; rest of the structure preserved
    gkp1 = next(p for p in repaired if p["slot"] == "GKP1")
    assert gkp1["player_id"] == 50
    assert len(repaired) == 15


def test_repair_budget_returns_none_when_unfixable():
    from src.decisions.squad_validator import repair_budget

    # every pool player is expensive -> no cheaper alternative exists
    all_expensive = [dict(p, price=9.0) for p in POOL]
    picks = _picks(0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19)
    assert repair_budget(picks, all_expensive) is None


def test_optimize_squad_budget_aware_with_premiums():
    """A premium-heavy pool must still fill all 15 slots (naive greedy stranding
    a later slot was observed 2026-08-14)."""
    from src.decisions.squad_validator import optimize_squad

    pool = [dict(p) for p in POOL]
    pool += [
        {"player_id": 100, "web_name": "Haaland", "team_short": "T10",
         "position": "FWD", "price": 15.5, "status": "a", "xp_next": 8.0,
         "xp_6gw": 48.0, "value": 48.0 / 15.5},
        {"player_id": 101, "web_name": "Saka", "team_short": "T11",
         "position": "MID", "price": 10.5, "status": "a", "xp_next": 7.0,
         "xp_6gw": 40.0, "value": 40.0 / 10.5},
        {"player_id": 102, "web_name": "Sánchez", "team_short": "T12",
         "position": "GKP", "price": 5.5, "status": "a", "xp_next": 5.0,
         "xp_6gw": 26.0, "value": 26.0 / 5.5},
    ]
    picks = optimize_squad(pool)
    assert len(picks) == 15
    assert validate_squad(picks, pool) == []


def test_normalize_squad_fixes_llm_slop():
    """LLM output with invented slots, position mismatch, surplus and over-budget
    picks is normalized into a legal squad."""
    from src.decisions.squad_validator import normalize_squad

    pool = [dict(p) for p in POOL]
    # GKP: 3 GKP picked (surplus of 1); DEF: 7 picked (surplus of 2);
    # a MID wrongly slotted as FWD3; a 4th player from T0 (club limit)
    slop = [
        {"player_id": 0, "slot": "GKP1"}, {"player_id": 1, "slot": "GKP2"},
        {"player_id": 2, "slot": "GKP3"},
        {"player_id": 3, "slot": "DEF1"}, {"player_id": 4, "slot": "DEF2"},
        {"player_id": 5, "slot": "DEF3"}, {"player_id": 6, "slot": "DEF4"},
        {"player_id": 7, "slot": "DEF5"}, {"player_id": 8, "slot": "DEF6"},
        {"player_id": 9, "slot": "DEF7"},
        {"player_id": 10, "slot": "MID1"}, {"player_id": 11, "slot": "MID2"},
        {"player_id": 12, "slot": "MID3"}, {"player_id": 13, "slot": "MID4"},
        {"player_id": 14, "slot": "FWD3"},   # MID in a FWD slot
    ]
    normalized = normalize_squad(slop, pool)
    assert normalized is not None
    assert validate_squad(normalized, pool) == []
    assert len(normalized) == 15
    slots = [pk["slot"] for pk in normalized]
    assert slots.count("GKP1") == 1 and slots.count("GKP2") == 1
    assert "DEF6" not in slots and "DEF7" not in slots
