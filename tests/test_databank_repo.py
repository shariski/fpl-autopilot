"""Repository tests for v0.12/v0.13: databank upsert, chance_of_playing, xp v2 columns."""
from src.data import repository


def _seed_players(conn, n=2):
    conn.executemany(
        "INSERT INTO players (id, name, web_name, team_id, position, price, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?,'t')",
        [(217, "Erling Haaland", "Haaland", 11, "FWD", 15.0, "a"),
         (154, "David Raya", "Raya", 1, "GKP", 5.5, "a")][:n])
    conn.commit()


def _row(element=217, gw=1, minutes=90, xg=1.4, xa=0.5, xgc=0.35, dc=2, saves=0,
         starts=1, bps=35, bonus=3, total=13, was_home=True, value=13.0,
         yellow_cards=0, red_cards=0):
    return {"element": element, "name": "x", "team": "MCI", "position": "FWD",
            "minutes": minutes, "expected_goals": xg, "expected_assists": xa,
            "expected_goals_conceded": xgc, "dc": dc, "saves": saves, "starts": starts,
            "bps": bps, "bonus": bonus, "total_points": total,
            "yellow_cards": yellow_cards, "red_cards": red_cards,
            "was_home": was_home, "value": value}


def test_upsert_databank_stats_persists_all_columns(db):
    _seed_players(db)
    n = repository.upsert_databank_stats(db, "2025-26", 1, [_row()])
    assert n == 1
    r = db.execute("SELECT * FROM player_stats WHERE player_id=217 AND gw=1").fetchone()
    assert r["source"] == "fpl_databank:2025-26"
    assert r["minutes"] == 90 and r["xg"] == 1.4 and r["xa"] == 0.5
    assert r["xgc"] == 0.35 and r["dc"] == 2 and r["saves"] == 0 and r["starts"] == 1
    assert r["bps"] == 35 and r["bonus"] == 3 and r["total_points"] == 13
    assert r["was_home"] == 1 and r["value"] == 13.0


def test_upsert_databank_stats_updates_existing_row(db):
    _seed_players(db)
    repository.upsert_databank_stats(db, "2025-26", 1, [_row(total=13)])
    repository.upsert_databank_stats(db, "2025-26", 1, [_row(total=16)])
    rows = db.execute("SELECT COUNT(*) c FROM player_stats WHERE player_id=217 AND gw=1").fetchone()
    assert rows["c"] == 1
    r = db.execute("SELECT total_points FROM player_stats WHERE player_id=217 AND gw=1").fetchone()
    assert r["total_points"] == 16


def test_upsert_databank_stats_seasons_do_not_collide(db):
    _seed_players(db)
    repository.upsert_databank_stats(db, "2024-25", 1, [_row(total=9)])
    repository.upsert_databank_stats(db, "2025-26", 1, [_row(total=13)])
    n = db.execute("SELECT COUNT(*) c FROM player_stats WHERE gw=1").fetchone()["c"]
    assert n == 2


def test_upsert_databank_stats_skips_unknown_player(db):
    _seed_players(db, n=1)
    repository.upsert_databank_stats(db, "2025-26", 1, [_row(), _row(element=9999)])
    n = db.execute("SELECT COUNT(*) c FROM player_stats").fetchone()["c"]
    assert n == 1


def test_upsert_players_saves_chance_of_playing(db):
    from src.data.models import BootstrapStatic
    bs = BootstrapStatic.model_validate({
        "events": [], "teams": [],
        "element_types": [{"id": 4, "singular_name_short": "FWD"}],
        "elements": [{"id": 217, "first_name": "Erling", "second_name": "Haaland",
                      "web_name": "Haaland", "team": 11, "element_type": 4,
                      "now_cost": 150, "status": "a", "selected_by_percent": 55.0,
                      "form": 8.0, "transfers_in": 0, "transfers_out": 0,
                      "chance_of_playing_next_round": 75}]})
    repository.upsert_players(db, bs.elements, bs.element_types)
    r = db.execute("SELECT chance_of_playing FROM players WHERE id=217").fetchone()
    assert r["chance_of_playing"] == 75.0


def test_upsert_xp_persists_v2_component_columns(db):
    repository.upsert_xp(db, [{
        "player_id": 217, "gw": 5, "model_version": "v2",
        "xp": 7.5, "xminutes": 80.0, "xgoals": 0.6, "xassists": 0.3, "xcs": 0.4,
        "p_start": 0.9, "xbonus": 0.3, "xdc": 0.5, "xcs_lambda": 1.2,
    }])
    r = db.execute("SELECT * FROM xp WHERE player_id=217 AND gw=5").fetchone()
    assert r["model_version"] == "v2" and r["p_start"] == 0.9
    assert r["xbonus"] == 0.3 and r["xdc"] == 0.5 and r["xcs_lambda"] == 1.2
