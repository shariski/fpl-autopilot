from src.decisions import captain


def _cand(pid, web_name, xp, xminutes=80.0, fdr_attack=3, fixture="ABC v XYZ (H)"):
    return {"player_id": pid, "web_name": web_name, "position": "MID",
            "xp": xp, "xminutes": xminutes, "fdr_attack": fdr_attack, "fixture": fixture}


def test_rank_captains_orders_by_xp():
    picks = captain.rank_captains([
        _cand(2, "Mid", 5.0), _cand(1, "Top", 7.2), _cand(3, "Low", 3.1),
    ])
    assert [p["player_id"] for p in picks] == [1, 2, 3]
    assert [p["xp"] for p in picks] == [7.2, 5.0, 3.1]


def test_rank_captains_tiebreak_minutes_then_fdr():
    # equal xp -> higher xminutes wins
    picks = captain.rank_captains([
        _cand(1, "LowMin", 6.0, xminutes=60.0),
        _cand(2, "HighMin", 6.0, xminutes=88.0),
    ])
    assert [p["player_id"] for p in picks] == [2, 1]
    # equal xp AND equal xminutes -> lower fdr_attack (easier fixture) wins
    picks = captain.rank_captains([
        _cand(3, "HardFix", 6.0, xminutes=88.0, fdr_attack=5),
        _cand(4, "EasyFix", 6.0, xminutes=88.0, fdr_attack=2),
    ])
    assert [p["player_id"] for p in picks] == [4, 3]


def test_rank_captains_reason_includes_gap():
    picks = captain.rank_captains([
        _cand(1, "Haaland", 7.2, fixture="MCI v BOU (H)"),
        _cand(2, "Salah", 6.1),
    ])
    assert "Highest xP (7.2)" in picks[0]["reason"]
    assert "MCI v BOU (H)" in picks[0]["reason"]
    assert "Salah" in picks[0]["reason"]
    assert "gap 1.1" in picks[0]["reason"]
    # ranks 2-5 use the short form
    assert picks[1]["reason"] == "xP 6.1 ABC v XYZ (H)."


def test_rank_captains_vice_is_second():
    # the #2 pick is the vice; assert ranking puts the 2nd-highest xp there
    picks = captain.rank_captains([
        _cand(1, "Top", 7.2), _cand(2, "Second", 6.1), _cand(3, "Third", 5.0),
    ])
    assert picks[1]["player_id"] == 2


def test_rank_captains_caps_at_five():
    picks = captain.rank_captains([_cand(i, f"P{i}", float(20 - i)) for i in range(1, 9)])
    assert len(picks) == 5
    assert [p["player_id"] for p in picks] == [1, 2, 3, 4, 5]
