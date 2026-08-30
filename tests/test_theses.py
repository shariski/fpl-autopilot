"""v0.26: deterministic theses cross-check for user speculation insights."""
from src.ai.squad import theses
from src.data import repository


def _seed(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Chelsea", "CHE"), (2, "Newcastle", "NEW")])
    db.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                   "VALUES (?,?,?,?,?, 'a')",
                   [(7, "Morgan Rogers", "Rogers", 1, "MID"),
                    (9, "Wissa", "Wissa", 2, "FWD")])
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1),(2,'GW2',0)")
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished, "
               "home_score, away_score) VALUES (1,1,2,1,1,2,3)")
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished, "
               "home_score, away_score) VALUES (2,2,1,2,0,NULL,NULL)")
    db.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
               "VALUES (1,2,3,3,'t'),(2,2,2,4,'t')")
    db.executemany(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, starts, saves,
           bps, expected_goals, expected_assists, expected_goals_conceded,
           defensive_contribution, yellow_cards, red_cards, settled_at)
           VALUES (?,1,1,?,0,0,0,0,?,1,0,20,?,0.1,1.4,2,0,0,'t')""",
        [(7, 90, 8, 0.597), (9, 90, 4, 1.0)])
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
               "xassists, xcs, computed_at) VALUES (7,2,'v2',4.0,70,0.5,0.2,0,'t')")
    db.commit()


def test_theses_player_checks(db):
    _seed(db)
    repository.add_speculation_note(db, "rogers takes long shots", team_id=1, player_id=7)
    t = theses.build_theses(db)[0]
    assert t["player_name"] == "Rogers" and t["team_short"] == "CHE"
    c = t["checks"]["player"]
    assert c["gw1_minutes"] == 90 and c["gw1_xg"] == 0.597
    assert c["xp_next"] == 4.0
    assert c["live_gws"] == 1 and c["live_starts"] == 1


def test_theses_team_checks_use_db_clubs(db):
    """Club data comes from the DB — a note scoped to NEW resolves to Newcastle."""
    _seed(db)
    repository.add_speculation_note(db, "newcastle incredibly good", team_id=2)
    t = theses.build_theses(db)[0]
    assert t["team_short"] == "NEW"
    assert t["checks"]["team"]["last_result"] == "NEW 2-3 CHE"
    assert len(t["checks"]["team"]["next3"]) == 1  # one upcoming fixture seeded


def test_theses_verdict_contradicts_zero_live_starts(db):
    _seed(db)
    db.execute("UPDATE player_gw_stats SET starts=0, minutes=0 WHERE player_id=9")
    db.commit()
    repository.add_speculation_note(db, "wissa starts every week", team_id=2, player_id=9)
    t = theses.build_theses(db)[0]
    assert t["verdict"] == "contradicts"


def test_theses_verdict_neutral_without_player(db):
    _seed(db)
    repository.add_speculation_note(db, "xabi alonso is pretty good", team_id=1)
    assert theses.build_theses(db)[0]["verdict"] == "neutral"
