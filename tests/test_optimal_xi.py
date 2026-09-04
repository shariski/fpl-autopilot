from src.decisions import optimal_xi as opt


# 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD. Enough to fill any valid XI.
def _seed_basics(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE")])
    players = [
        (1, "GK1", "GKP", 1),
        (2, "GK2", "GKP", 1),
        (3, "D1", "DEF", 1), (4, "D2", "DEF", 1), (5, "D3", "DEF", 1),
        (6, "D4", "DEF", 1), (7, "D5", "DEF", 1),
        (8, "M1", "MID", 1), (9, "M2", "MID", 1), (10, "M3", "MID", 1),
        (11, "M4", "MID", 1), (12, "M5", "MID", 1),
        (13, "F1", "FWD", 1), (14, "F2", "FWD", 1), (15, "F3", "FWD", 1),
    ]
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   players)
    # xp v2 for the upcoming GW — give each player a distinct xP
    xp_values = {
        1: 3.0, 2: 2.5,
        3: 4.5, 4: 4.0, 5: 3.5, 6: 2.0, 7: 1.0,
        8: 6.0, 9: 5.5, 10: 5.0, 11: 2.5, 12: 1.5,
        13: 5.0, 14: 4.5, 15: 1.0,
    }
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists) "
        "VALUES (?,?,?,?,?,?,?)",
        [(pid, 3, "v2", float(xp), 90.0, 0.0, 0.0) for pid, xp in xp_values.items()])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.commit()


def _squad():
    """Real FPL payload shape: each pick has an integer squad slot (1-15).
    The optimizer resolves player positions via the players table, not
    from this `position` field."""
    return [{"element": pid, "position": slot, "is_captain": False,
             "is_vice_captain": False}
            for pid, slot in [
        (1, 1), (2, 12),
        (3, 2), (4, 3), (5, 4), (6, 5), (7, 13),
        (8, 6), (9, 7), (10, 8), (11, 9), (12, 14),
        (13, 10), (14, 11), (15, 15),
    ]]


def test_can_form_xi_true_when_squad_is_full(db):
    _seed_basics(db)
    assert opt.can_form_xi(db, _squad()) is True


def test_can_form_xi_false_when_no_gk(db):
    _seed_basics(db)
    squad = [p for p in _squad() if p["element"] not in (1, 2)]
    assert opt.can_form_xi(db, squad) is False


def test_can_form_xi_false_when_no_fwd_at_all(db):
    _seed_basics(db)
    squad = [p for p in _squad() if p["element"] not in (13, 14, 15)]
    assert opt.can_form_xi(db, squad) is False


def test_select_returns_none_when_no_gw(db):
    _seed_basics(db)
    db.execute("DELETE FROM gameweeks")
    db.commit()
    assert opt.select(db, _squad()) is None


def test_select_picks_highest_xp_in_each_position(db):
    """GK1 + top-D DEF + top-M MID + top-F FWD, where D+M+F=10."""
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    assert len(res["xi"]) == 11   # 10 outfield + 1 GK
    assert len(res["bench"]) == 4
    # GK1 has higher xp (3.0) than GK2 (2.5) → GK1 must be starter
    assert 1 in res["xi"]
    assert 2 in res["bench"]
    # top-3 DEF by xP: D1(4.5), D2(4.0), D3(3.5)
    starter_defs = sorted([eid for eid in res["xi"] if eid in (3, 4, 5, 6, 7)])
    assert starter_defs[:3] == [3, 4, 5]
    # top-3 MID: M1(6.0), M2(5.5), M3(5.0)
    starter_mids = sorted([eid for eid in res["xi"] if eid in (8, 9, 10, 11, 12)])
    assert starter_mids[:3] == [8, 9, 10]
    # top-1 FWD: F1(5.0)
    starter_fwds = [eid for eid in res["xi"] if eid in (13, 14, 15)]
    assert 13 in starter_fwds


def test_formation_string_matches_chosen_xi(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    d, m, f = (int(x) for x in res["formation"].split("-"))
    assert 3 <= d <= 5 and 3 <= m <= 5 and 1 <= f <= 3
    assert d + m + f == 10  # 10 outfield slots + 1 GK = 11 starters


def test_captain_and_vice_are_top_two_starters_by_xp(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    # M1 has the highest xP among starters in any plausible formation
    assert res["captain_id"] in res["xi"]
    assert res["vice_id"] in res["xi"]
    assert res["captain_id"] != res["vice_id"]


def test_captain_prefers_attacker_over_higher_xp_defender_in_pre_season(db):
    """v0.28 regression: the optimizer must NOT captain a clean-sheet-heavy
    defender/GK over an attacker with a modest xP edge while the pre-season
    defensive penalty is active (live GW3 26/27: Virgil DEF over Mbeumo MID).

    Give D1 a higher raw xP than M1 but no goal involvement; M1 carries
    xG/xA. Raw xP would pick D1; the ranker-adjusted score must pick M1."""
    _seed_basics(db)
    # D1 (id 3) raw xP 6.4 > M1 (id 8) raw xP 6.0, but D1 has no upside
    db.execute("UPDATE xp SET xp=6.4, xgoals=0.0, xassists=0.0 "
               "WHERE player_id=3 AND gw=3 AND model_version='v2'")
    # M1 (id 8) carries real goal involvement
    db.execute("UPDATE xp SET xgoals=0.7, xassists=0.4 "
               "WHERE player_id=8 AND gw=3 AND model_version='v2'")
    db.commit()

    # no live rows -> sf_live_pairs=0 < 3 -> pre-season penalty is ON
    res = opt.select(db, _squad())
    assert res is not None
    # both D1 and M1 are starters in the chosen XI
    assert res["captain_id"] == 8, "attacker should take the armband over the defender"
    assert res["vice_id"] in res["xi"]


def test_captain_is_raw_top_xp_when_no_penalty_mid_season(db):
    """Mid-season (>=3 live pairs, penalty off): the ceiling term alone may not
    overcome a real xP gap, so a defender with a solid cushion stays captain —
    matching the ranker's behaviour."""
    _seed_basics(db)
    # D1 (id 3) raw xP 6.4 > M1 (id 8) 6.0, M1 with modest upside
    db.execute("UPDATE xp SET xp=6.4, xgoals=0.0, xassists=0.0 "
               "WHERE player_id=3 AND gw=3 AND model_version='v2'")
    db.execute("UPDATE xp SET xgoals=0.7, xassists=0.4 "
               "WHERE player_id=8 AND gw=3 AND model_version='v2'")
    db.commit()
    # seed 3+ live (source, gw) pairs in the SF window so the penalty is off
    _seed_live_pairs(db, n=3)

    res = opt.select(db, _squad())
    assert res is not None
    # 6.4 > 6.0 + 0.15*1.1 = 6.165 -> defender keeps the armband (no penalty)
    assert res["captain_id"] == 3


def _seed_live_pairs(db, n):
    """Insert `n` settled live (player_gw_stats) pairs for the seed squad —
    drives ratings.sf_live_pairs() >= 3 so the pre-season penalty is off."""
    # players 3 and 8, live rows for gw 1..n
    for gw in range(1, n + 1):
        db.execute(
            """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
               goals_scored, assists, clean_sheets, bonus, total_points, starts,
               saves, bps, expected_goals, expected_assists,
               expected_goals_conceded, defensive_contribution, yellow_cards,
               red_cards, settled_at)
               VALUES (?,?,?,90,0,0,0,0,5,1,0,10,0.3,0.1,1.0,1,0,0,'t')""",
            (8, gw, gw))
        db.execute(
            """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
               goals_scored, assists, clean_sheets, bonus, total_points, starts,
               saves, bps, expected_goals, expected_assists,
               expected_goals_conceded, defensive_contribution, yellow_cards,
               red_cards, settled_at)
               VALUES (?,?,?,90,0,0,0,0,5,1,0,10,0.0,0.0,1.0,2,0,0,'t')""",
            (3, gw, gw))
    db.commit()


def test_bench_anchors_sub_gk_at_index_zero(db):
    """The bench list is ordered with the GK first so the sub-GK takes slot 12."""
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    bench_first = res["bench"][0]
    assert bench_first == 2   # GK2 — the only GK not in XI
    assert res["bench_slots"][2] == 12


def test_starter_slots_are_in_xi_range(db):
    _seed_basics(db)
    res = opt.select(db, _squad())
    assert res is not None
    for eid, slot in res["starter_slots"].items():
        assert 1 <= slot <= 11
    for eid, slot in res["bench_slots"].items():
        assert 12 <= slot <= 15


def test_select_returns_none_when_squad_cant_form_xi(db):
    """Squad with 1 GK, 1 DEF, 1 MID, 1 FWD → can't form any valid XI."""
    db.execute("INSERT INTO teams VALUES (1, 'Arsenal', 'ARS', 0, 0, 0, 0)")
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   [(1, "GK", "GKP", 1), (2, "D", "DEF", 1),
                    (3, "M", "MID", 1), (4, "F", "FWD", 1)])
    db.executemany(
        "INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (?,?,?,?,?)",
        [(pid, 3, "v2", 2.0, 90.0) for pid in range(1, 5)])
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next, finished) "
               "VALUES (3, '2026-09-04T17:30:00+00:00', 1, 0)")
    db.commit()
    squad = [{"element": pid, "position": slot, "is_captain": False,
              "is_vice_captain": False}
             for pid, slot in [(1, 1), (2, 2), (3, 3), (4, 4)]]
    assert opt.select(db, squad) is None
