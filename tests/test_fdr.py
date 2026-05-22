from src.analytics import fdr
from src.data.models import BootstrapStatic
from src.data import repository


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


def test_compute_and_store_persists_for_horizon(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    # gw4 finished, gw5 & gw6 upcoming -> next_gw = 5
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (4,'GW4',1),(5,'GW5',0),(6,'GW6',0)")
    tids = [r["id"] for r in db.execute("SELECT id FROM teams LIMIT 4")]
    db.execute(
        "INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) "
        "VALUES (1,5,?,?,0),(2,6,?,?,0)",
        (tids[0], tids[1], tids[2], tids[3]),
    )
    db.commit()

    n = fdr.compute_and_store(db, horizon=6)
    assert n == 4  # 2 fixtures x 2 teams
    rows = db.execute("SELECT team_id, gw, fdr_attack, fdr_defense FROM fdr").fetchall()
    assert len(rows) == 4
    for r in rows:
        assert 1 <= r["fdr_attack"] <= 5
        assert 1 <= r["fdr_defense"] <= 5
    assert {r["gw"] for r in rows} == {5, 6}


def test_compute_and_store_no_upcoming_returns_zero(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1)")
    db.commit()
    assert fdr.compute_and_store(db) == 0
    assert db.execute("SELECT COUNT(*) c FROM fdr").fetchone()["c"] == 0


def test_compute_and_store_idempotent(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5,'GW5',0)")
    tids = [r["id"] for r in db.execute("SELECT id FROM teams LIMIT 2")]
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (1,5,?,?,0)",
               (tids[0], tids[1]))
    db.commit()
    fdr.compute_and_store(db)
    fdr.compute_and_store(db)  # second run must not duplicate (PK team_id,gw)
    assert db.execute("SELECT COUNT(*) c FROM fdr").fetchone()["c"] == 2
