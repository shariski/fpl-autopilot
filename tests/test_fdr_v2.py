"""FDR v2 (xG-based multipliers) + team ratings from the databank (v0.12)."""
import pytest
from src.analytics import ratings, fdr
from src.data import repository


def _seed_teams(conn):
    conn.executemany(
        "INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
        [(1, "Arsenal", "ARS"), (2, "Man City", "MCI"), (3, "Ipswich", "IPS")])
    conn.commit()


def _seed_players(conn):
    conn.executemany(
        "INSERT INTO players (id, name, web_name, team_id, position, price, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?,'t')",
        [(101, "A1", "a1", 1, "DEF", 5.0, "a"), (102, "A2", "a2", 1, "MID", 6.0, "a"),
         (201, "C1", "c1", 2, "FWD", 8.0, "a"), (202, "C2", "c2", 2, "GK", 5.0, "a"),
         (301, "I1", "i1", 3, "FWD", 6.0, "a")])
    conn.commit()


def _db_row(element, gw, minutes, xg, xgc, dc=0, starts=1):
    return {"element": element, "name": "x", "team": "T", "position": "P",
            "minutes": minutes, "expected_goals": xg, "expected_assists": 0.0,
            "expected_goals_conceded": xgc, "dc": dc, "saves": 0, "starts": starts,
            "bps": 0, "bonus": 0, "total_points": 0,
            "yellow_cards": 0, "red_cards": 0,
            "was_home": True, "value": 5.0}


def _seed_databank(conn, season="2025-26"):
    """6 GWs: Arsenal strong (xg 2.0/gw), Man City mid (1.0), spread over gw 1..6."""
    for gw, team_rows in {
        1: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
        2: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
        3: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
        4: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
        5: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
        6: [(101, 90, 2.0, 0.5), (102, 90, 1.5, 0.5), (201, 90, 1.0, 1.0), (202, 90, 0.0, 1.0)],
    }.items():
        repository.upsert_databank_stats(
            conn, season, gw,
            [_db_row(el, gw, m, xg, xgc) for el, m, xg, xgc in team_rows])


def test_damp_linear_within_threshold():
    assert ratings.damp(1.2) == 1.2
    assert ratings.damp(-1.2) == -1.2
    assert ratings.damp(1.0) == 1.0


def test_damp_caps_beyond_threshold():
    assert ratings.damp(2.0) == 1.55 + (2.0 - 1.55) * 0.4
    assert ratings.damp(5.0) == 1.55 + (5.0 - 1.55) * 0.4


def test_team_ratings_blend_lf_and_sf(db):
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    r, la = ratings.compute_team_ratings(db, lf_gw_count=38, sf_gw_count=6)
    # Arsenal: xg per gw = 3.5 (2.0+1.5) -> xg90 = 3.5 (per match, not ÷ squad size)
    # Man City: xg 1.0 -> xg90 = 1.0
    assert r[1].xg90 == pytest.approx(3.5) and r[2].xg90 == pytest.approx(1.0)
    # xGC: Arsenal 1.0/gw -> 0.5/90; Man City 2.0/gw -> 1.0/90
    assert r[1].xgc90 == pytest.approx(0.5) and r[2].xgc90 == pytest.approx(1.0)
    # league averages over the same window
    assert la.xg90 == pytest.approx(2.25) and la.xgc90 == pytest.approx(0.75)
    # blended = 0.8*lf + 0.2*sf (all 6 GWs in SF here, so equal)
    assert r[1].xg90 == pytest.approx(0.8 * 3.5 + 0.2 * 3.5)


def test_team_xg90_is_per_match_not_divided_by_squad_size(db):
    """Player xG sums to the team match total ONCE; dividing by ALL players'
    minutes deflates team+league xG/90 by the squad-size factor (~13x in real
    data). Regression: la.xg90 came out 0.132 vs the true ~1.35, and promoted
    override multipliers exploded (damp(1.3/0.132) = 4.86) — Lammens 14.43 xP.
    With a 2-player squad generating 3.5 xG/match, xg90 must be 3.5, not 1.75."""
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    r, la = ratings.compute_team_ratings(db, lf_gw_count=38, sf_gw_count=6)
    assert r[1].xg90 == pytest.approx(3.5)
    assert r[2].xg90 == pytest.approx(1.0)
    assert la.xg90 == pytest.approx(2.25)


def test_team_ratings_short_form_only_recent_gws(db):
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    r, _ = ratings.compute_team_ratings(db, lf_gw_count=4, sf_gw_count=2)
    # LF = last 4 GWs (3..6), SF = last 2 (5..6): same numbers here -> equal blends
    assert r[1].xg90 == pytest.approx(3.5)


def test_ratings_promoted_team_gets_override(db):
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    r, _ = ratings.compute_team_ratings(db)
    ipswich = r.get(3)  # no databank rows
    assert ipswich is None
    overrides = ratings.promoted_overrides(db)
    assert overrides[3] == (1.72, 1.55)  # IPS xG/90, xGC/90 from the doc


def test_fdr_v2_multipliers_from_ratings(db):
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    r, la = ratings.compute_team_ratings(db)
    fixtures = [
        {"gw": 7, "home_team_id": 1, "away_team_id": 2},   # ARS home vs MCI
        {"gw": 8, "home_team_id": 3, "away_team_id": 1},   # IPS (promoted) vs ARS
    ]
    overrides = {3: (1.72, 1.55)}
    rows = fdr.compute_fdr_v2(r, la, fixtures, overrides)
    by = {(x["team_id"], x["gw"]): x for x in rows}
    # ARS home: opponent MCI xGC90=1.0 / LA 0.75 = 1.333 -> damped 1.333; opponent xG 1.0/2.25=0.444
    assert by[(1, 7)]["fdr_attack_mult"] == pytest.approx(round(ratings.damp(1.0 / 0.75), 4))
    assert by[(1, 7)]["fdr_defense_mult"] == pytest.approx(round(ratings.damp(1.0 / 2.25), 4))
    # MCI away: opponent ARS xGC 0.5/0.75 = 0.667 -> easier fixture (mult < 1)
    assert by[(2, 7)]["fdr_attack_mult"] < 1.0
    # ARS (gw8, away vs promoted IPS): the opponent rating uses the override (1.72, 1.55)
    assert by[(1, 8)]["fdr_attack_mult"] == pytest.approx(round(ratings.damp(1.55 / 0.75), 4))
    assert by[(1, 8)]["fdr_defense_mult"] == pytest.approx(round(ratings.damp(1.72 / 2.25), 4))
    # IPS home vs ARS: opponent ARS xGC 0.5/0.75 -> easier fixture
    assert by[(3, 8)]["fdr_attack_mult"] == pytest.approx(round(ratings.damp(0.5 / 0.75), 4))


def test_fdr_v2_compute_and_store_persists(db):
    _seed_teams(db)
    _seed_players(db)
    _seed_databank(db)
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (7, 'GW7', 0)")
    db.executemany(
        "INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,?,?,?,0)",
        [(1, 7, 1, 2)])
    db.commit()
    n = fdr.compute_and_store_v2(db, horizon=1)
    assert n == 2
    row = db.execute("SELECT fdr_attack_mult, fdr_defense_mult FROM fdr WHERE team_id=1 AND gw=7").fetchone()
    assert row["fdr_attack_mult"] is not None and row["fdr_defense_mult"] is not None
