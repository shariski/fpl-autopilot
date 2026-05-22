def test_init_db_creates_all_tables(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected = {
        "players", "teams", "player_stats", "fixtures", "fdr", "xp",
        "my_team", "gameweeks", "activity_log", "credentials", "cache_meta",
    }
    assert expected <= tables


def test_gameweeks_state_defaults_to_pending(db):
    db.execute("INSERT INTO gameweeks (id, name) VALUES (1, 'Gameweek 1')")
    row = db.execute("SELECT state FROM gameweeks WHERE id=1").fetchone()
    assert row["state"] == "PENDING"
