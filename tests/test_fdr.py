from src.analytics import fdr


def test_quintile_bucket_boundaries():
    dist = list(range(20))  # 0..19, n=20 -> buckets of 4
    assert fdr.quintile_bucket(0, dist) == 1     # below=0
    assert fdr.quintile_bucket(3, dist) == 1     # below=3 -> 0 -> 1
    assert fdr.quintile_bucket(4, dist) == 2     # below=4 -> 1 -> 2
    assert fdr.quintile_bucket(16, dist) == 5    # below=16 -> 4 -> 5
    assert fdr.quintile_bucket(19, dist) == 5
    assert fdr.quintile_bucket(999, dist) == 5   # above max -> capped 5


def _teams_uniform():
    # 5 teams; every strength column = 1000 + 100*i, so team i ranks to bucket i in every column.
    return [
        {"id": i, "strength_attack_home": 1000 + 100 * i, "strength_attack_away": 1000 + 100 * i,
         "strength_defence_home": 1000 + 100 * i, "strength_defence_away": 1000 + 100 * i}
        for i in range(1, 6)
    ]


def test_compute_fdr_two_rows_per_fixture_and_ranking():
    teams = _teams_uniform()
    fixtures = [{"gw": 5, "home_team_id": 5, "away_team_id": 1}]  # strongest home vs weakest away
    rows = fdr.compute_fdr(teams, fixtures)
    assert len(rows) == 2
    by_team = {r["team_id"]: r for r in rows}
    assert by_team[5]["fdr_attack"] == 1
    assert by_team[5]["fdr_defense"] == 1
    assert by_team[1]["fdr_attack"] == 5
    assert by_team[1]["fdr_defense"] == 5
    assert by_team[5]["gw"] == 5


def test_compute_fdr_uses_correct_venue_columns():
    # away-defence ascending (team i -> 1000+100i); home-defence DESCENDING (team i -> 1600-100i).
    teams = [
        {"id": i, "strength_attack_home": 1200, "strength_attack_away": 1200,
         "strength_defence_home": 1600 - 100 * i, "strength_defence_away": 1000 + 100 * i}
        for i in range(1, 6)
    ]
    fixtures = [{"gw": 1, "home_team_id": 1, "away_team_id": 5}]
    rows = fdr.compute_fdr(teams, fixtures)
    by_team = {r["team_id"]: r for r in rows}
    # Home team 1's attack difficulty must use AWAY team 5's *away* defence (=1500 -> hardest -> 5),
    # NOT team 5's home defence (=1100 -> easiest -> 1).
    assert by_team[1]["fdr_attack"] == 5
