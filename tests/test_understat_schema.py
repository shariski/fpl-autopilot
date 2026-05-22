def test_understat_players_table_exists(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(understat_players)")}
    expected = {
        "understat_id", "fpl_player_id", "season", "player_name", "team_title",
        "games", "minutes", "goals", "assists", "xg", "xa", "npg", "npxg",
        "xg_per_90", "xa_per_90", "updated_at",
    }
    assert expected <= cols
