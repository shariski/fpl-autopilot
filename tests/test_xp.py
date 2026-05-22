from src.analytics import xp


def test_striker_easier_fixture_scores_more():
    easy = xp.compute_player_xp("FWD", "a", 0.7, 0.2, 2700, 30, fdr_attack=1, fdr_defense=3)
    hard = xp.compute_player_xp("FWD", "a", 0.7, 0.2, 2700, 30, fdr_attack=5, fdr_defense=3)
    assert easy["xp"] > hard["xp"]
    assert easy["xgoals"] > hard["xgoals"]


def test_gk_earns_clean_sheet_xp():
    gk = xp.compute_player_xp("GKP", "a", 0.0, 0.0, 2700, 30, fdr_attack=3, fdr_defense=1)
    assert gk["xcs"] > 0
    assert gk["xp"] > 0


def test_position_weighting_def_beats_fwd_same_inputs():
    d = xp.compute_player_xp("DEF", "a", 0.3, 0.0, 2700, 30, fdr_attack=3, fdr_defense=3)
    f = xp.compute_player_xp("FWD", "a", 0.3, 0.0, 2700, 30, fdr_attack=3, fdr_defense=3)
    assert d["xgoals"] == f["xgoals"]      # identical expected goals
    assert d["xp"] > f["xp"]               # DEF goal=6 + CS bonus > FWD goal=4 + no CS


def test_injured_status_zero_xp():
    inj = xp.compute_player_xp("FWD", "i", 0.7, 0.2, 2700, 30, fdr_attack=1, fdr_defense=1)
    assert inj["xminutes"] == 0.0
    assert inj["xp"] == 0.0


def test_zero_games_no_division_error():
    res = xp.compute_player_xp("MID", "a", 0.5, 0.5, 0, 0, fdr_attack=2, fdr_defense=2)
    assert res["xminutes"] == 0.0
    assert res["xp"] == 0.0
