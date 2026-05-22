from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import repository, name_resolver


def _setup(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    us = UnderstatPlayersResponse.model_validate(load("understat-players.json")).players
    res = name_resolver.resolve_players(players, teams, us)
    return us, res


def test_upsert_understat_players_maps_and_derives(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    count = db.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert count == len(us)
    haaland = next(p for p in us if p.player_name == "Erling Haaland")
    row = db.execute(
        "SELECT fpl_player_id, xg_per_90, minutes, xg FROM understat_players WHERE understat_id=?",
        (haaland.id,),
    ).fetchone()
    assert row["fpl_player_id"] is not None
    assert row["xg_per_90"] == round(haaland.xG / (haaland.time / 90.0), 4)


def test_upsert_understat_zero_minutes_per90_is_zero(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    rows = db.execute("SELECT xg_per_90 FROM understat_players WHERE minutes=0").fetchall()
    assert all(r["xg_per_90"] == 0.0 for r in rows)


def test_upsert_understat_idempotent(db, load):
    us, res = _setup(db, load)
    repository.upsert_understat_players(db, us, res, "2025")
    repository.upsert_understat_players(db, us, res, "2025")
    count = db.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert count == len(us)
