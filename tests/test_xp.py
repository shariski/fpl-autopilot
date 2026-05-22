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


from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import repository, name_resolver


def _seed_full(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    us = UnderstatPlayersResponse.model_validate(load("understat-players.json")).players
    fpl_players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    resolution = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(db, us, resolution, "2025")
    # one upcoming GW (5) with an FDR row for every team
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5, 'GW5', 0)")
    team_ids = [r["id"] for r in db.execute("SELECT id FROM teams")]
    db.executemany(
        "INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES (?,5,3,3,'t')",
        [(tid,) for tid in team_ids])
    db.commit()


def test_compute_and_store_persists_v1(db, load):
    _seed_full(db, load)
    n = xp.compute_and_store(db, horizon=6)
    assert n > 0
    rows = db.execute("SELECT model_version, xp FROM xp").fetchall()
    assert rows and all(r["model_version"] == "v1" for r in rows)
    haaland_id = db.execute("SELECT id FROM players WHERE web_name='Haaland'").fetchone()["id"]
    hx = db.execute("SELECT xp, xgoals FROM xp WHERE player_id=? AND gw=5", (haaland_id,)).fetchone()
    assert hx is not None and hx["xp"] > 0 and hx["xgoals"] > 0


def test_compute_and_store_idempotent(db, load):
    _seed_full(db, load)
    xp.compute_and_store(db)
    before = db.execute("SELECT COUNT(*) c FROM xp").fetchone()["c"]
    xp.compute_and_store(db)  # re-run must not duplicate (PK player_id,gw,model_version)
    after = db.execute("SELECT COUNT(*) c FROM xp").fetchone()["c"]
    assert before == after


def test_compute_and_store_no_upcoming_returns_zero(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1, 'GW1', 1)")
    db.commit()
    assert xp.compute_and_store(db) == 0
