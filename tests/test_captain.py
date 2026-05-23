import json

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


def test_rank_captains_single_candidate():
    picks = captain.rank_captains([_cand(1, "Solo", 5.0, fixture="MCI v BOU (H)")])
    assert len(picks) == 1
    assert picks[0]["reason"] == "Highest xP (5.0) MCI v BOU (H)."


def _seed_squad(db):
    # 4 teams (need short_name for the fixture display string)
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)", [
        (1, "Man City", "MCI"), (2, "Bournemouth", "BOU"),
        (3, "Liverpool", "LIV"), (4, "Arsenal", "ARS"),
    ])
    # gw9 finished, gw10 upcoming -> next_gw = 10
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (9,'GW9',1),(10,'GW10',0)")
    # gw10 fixtures: MCI(1) home v BOU(2); LIV(3) home v ARS(4)
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) "
               "VALUES (1,10,1,2,0),(2,10,3,4,0)")
    db.executemany("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
                   "VALUES (?,10,?,?,'t')", [(1, 2, 3), (2, 4, 3), (3, 3, 2), (4, 3, 4)])
    # 15 players, ids 101..115, descending xp so ordering is unambiguous
    teams_cycle = [1, 3, 2, 4]
    xps = [7.2, 6.1, 5.5, 5.0, 4.5, 4.0, 3.8, 3.5, 3.2, 3.0, 2.8, 2.5, 2.2, 2.0, 1.5]
    pids = list(range(101, 116))
    for idx, pid in enumerate(pids):
        web = "Haaland" if pid == 101 else f"P{pid}"
        team = 1 if pid == 101 else teams_cycle[idx % 4]
        db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
                   "VALUES (?,?,?,?,?, 'a')", (pid, web, web, team, "FWD"))
        db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
                   "xassists, xcs, computed_at) VALUES (?,10,'v1',?,85.0,0,0,0,'t')",
                   (pid, xps[idx]))
    picks_json = json.dumps([
        {"element": pid, "position": i + 1, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False} for i, pid in enumerate(pids)])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, snapshot_at) "
               "VALUES (10, ?, 0, 0, 't')", (picks_json,))
    db.commit()


def test_get_captain_picks_integration(db):
    _seed_squad(db)
    result = captain.get_captain_picks(db)

    # shape matches docs/api-contract.md /api/captain
    assert set(result.keys()) == {"picks", "vice_player_id", "confidence"}
    assert isinstance(result["confidence"], int)
    assert 0 <= result["confidence"] <= 100
    assert len(result["picks"]) == 5
    for p in result["picks"]:
        assert set(p.keys()) == {"player_id", "web_name", "xp", "fixture", "reason"}

    # descending xp
    xs = [p["xp"] for p in result["picks"]]
    assert xs == sorted(xs, reverse=True)

    # top pick is the premium attacker, home fixture rendered correctly
    top = result["picks"][0]
    assert top["player_id"] == 101 and top["web_name"] == "Haaland"
    assert top["fixture"] == "MCI v BOU (H)"
    assert "gap 1.1" in top["reason"]

    # away player renders the (A) venue with the opponent listed second
    assert result["picks"][2]["fixture"] == "BOU v MCI (A)"

    # vice = #2 pick
    assert result["vice_player_id"] == result["picks"][1]["player_id"] == 102


def test_get_captain_picks_no_upcoming_gw_returns_empty(db):
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1)")
    db.commit()
    assert captain.get_captain_picks(db) == {"picks": [], "vice_player_id": None, "confidence": None}


def test_get_captain_picks_no_xp_row_ranks_last(db):
    # a squad player with no xp row for the GW (blank GW / unmatched) ranks last,
    # treated as xp 0.0 with a "—" fixture (spec §4).
    db.execute("INSERT INTO teams (id, name, short_name) "
               "VALUES (1,'Man City','MCI'),(2,'Bournemouth','BOU')")
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (10,'GW10',0)")
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) "
               "VALUES (1,10,1,2,0)")
    db.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
               "VALUES (1,10,2,3,'t'),(2,10,4,3,'t')")
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
               "VALUES (201,'Has XP','HasXP',1,'FWD','a'),(202,'No XP','NoXP',2,'FWD','a')")
    # player 201 has an xp row; player 202 deliberately does NOT
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
               "xassists, xcs, computed_at) VALUES (201,10,'v1',5.0,85.0,0,0,0,'t')")
    picks_json = json.dumps([
        {"element": pid, "position": i + 1, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False}
        for i, pid in enumerate([201, 202])])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, team_value, snapshot_at) "
               "VALUES (10, ?, 0, 0, 't')", (picks_json,))
    db.commit()

    result = captain.get_captain_picks(db)
    assert [p["player_id"] for p in result["picks"]] == [201, 202]
    last = result["picks"][1]
    assert last["xp"] == 0.0
    assert last["fixture"] == "—"
