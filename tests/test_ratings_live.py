"""v0.23: ratings row union — current-season live rows (player_gw_stats) enter the
LF/SF windows alongside databank rows (natural window, no new blend constants)."""
import pytest

from src.analytics import ratings
from src.data import repository
from src.data.db import connect, init_db


def _seed(conn, prior_gws=5):
    """Teams + players; `prior_gws` GWs of 25-26 databank (everyone starts, xg 0.3)."""
    conn.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                     [(1, "Team A", "TA"), (2, "Team B", "TB")])
    conn.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                     "VALUES (?,?,?,?,?, 'a')",
                     [(101, "Starter", "Starter", 1, "MID"),
                      (102, "Keeper", "Keeper", 1, "GKP"),
                      (103, "NewBoy", "NewBoy", 2, "FWD"),
                      (104, "MidB", "MidB", 2, "MID"),
                      (105, "FwdB", "FwdB", 2, "FWD")])
    for gw in range(1, prior_gws + 1):
        repository.upsert_databank_stats(conn, "2025-26", gw, [
            {"element": pid, "name": n, "team": t, "position": p, "minutes": 90,
             "expected_goals": 0.3, "expected_assists": 0.1,
             "expected_goals_conceded": 1.4, "dc": 2, "saves": 0, "starts": 1,
             "bps": 20, "yellow_cards": 0, "red_cards": 0, "was_home": True,
             "value": 5.0, "bonus": 0, "total_points": 5}
            for pid, n, t, p in [(101, "Starter", "Team A", "MID"),
                                 (102, "Keeper", "Team A", "GK"),
                                 (104, "MidB", "Team B", "MID"),
                                 (105, "FwdB", "Team B", "FWD")]])
    conn.commit()


def _live_row(conn, pid, gw, fixture, *, starts=1, minutes=90, xg=0.5, xa=0.2,
              xgc=1.4, dc=2, saves=0, bps=20, yc=0, rc=0, starts_null=False):
    """Insert one full-stat live row (the settlement output shape, v0.23)."""
    cols = ("player_id, gw, fixture_id, minutes, goals_scored, assists, clean_sheets, "
            "bonus, total_points, starts, saves, bps, expected_goals, expected_assists, "
            "expected_goals_conceded, defensive_contribution, yellow_cards, red_cards, "
            "settled_at")
    vals = (pid, gw, fixture, minutes, 0, 0, 0, 0, 5,
            None if starts_null else starts, saves, bps, xg, xa, xgc, dc, yc, rc, "t")
    conn.execute(f"INSERT INTO player_gw_stats ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 vals)
    conn.commit()


def _rates(conn, **kw):
    return ratings.compute_player_rates(conn, **kw)


def test_window_ordering_mixed_sources():
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 101, 1, 42)
    rows = ratings._rating_rows(conn)
    keys = ratings._window_keys(rows, 5)
    # last 5 of [(db,1)..(db,5), (live,1)] ordered by season then gw
    assert keys == {(f"fpl_databank:2025-26", g) for g in (2, 3, 4, 5)} | {("fpl_live:2026-27", 1)}
    # SF window (2) = last prior GW + the live GW
    sf = ratings._window_keys(rows, 2)
    assert sf == {("fpl_databank:2025-26", 5), ("fpl_live:2026-27", 1)}


def test_benched_starter_keeps_sane_rotation():
    """A GW1 benching must NOT zero a player: 4/5-style starts stay ~0.8."""
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=5)
    # keeper benched GW1: zeroed stats (a real benched player carries xg=0)
    _live_row(conn, 102, 1, 42, starts=0, minutes=0, xg=0.0, xa=0.0, xgc=0.0, dc=0)
    pr = _rates(conn, lf_gw_count=5, sf_gw_count=2)[102]
    assert pr.starts == 4            # 4 prior starts in-window + 0 live
    assert pr.squads_made == 5       # 4 prior + 1 live team GW in-window
    assert pr.xg_per_start > 0.0     # rates still anchored by prior season


def test_live_dgw_aggregation():
    """DGW: two fixture rows for one (player, gw) aggregate — starts=OR, rest=SUM."""
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 100, starts=1, minutes=90, xg=0.4)
    _live_row(conn, 101, 1, 101, starts=0, minutes=45, xg=0.6)
    db_rows, live_rows = ratings._rating_sources(conn)
    live = [r for r in live_rows if r["player_id"] == 101]
    assert len(live) == 1
    assert live[0]["starts"] == 1
    assert live[0]["minutes"] == 135
    assert live[0]["xg"] == 1.0


def test_current_season_databank_excluded_when_live_present():
    """R9: with live rows present, the same season's databank rows are excluded."""
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=1)
    repository.upsert_databank_stats(conn, "2026-27", 1, [
        {"element": 101, "name": "Starter", "team": "Team A", "position": "MID",
         "minutes": 90, "expected_goals": 99.0, "expected_assists": 0.1,
         "expected_goals_conceded": 1.4, "dc": 2, "saves": 0, "starts": 1,
         "bps": 20, "yellow_cards": 0, "red_cards": 0, "was_home": True,
         "value": 5.0, "bonus": 0, "total_points": 5}])
    _live_row(conn, 101, 1, 42, xg=0.5)
    db_rows, live_rows = ratings._rating_sources(conn)
    assert all(r["source"] != "fpl_databank:2026-27" for r in db_rows)
    assert len(live_rows) == 1
    # without live rows the databank row IS included (pre-season path unchanged)
    conn.execute("DELETE FROM player_gw_stats")
    conn.commit()
    db_rows2, live_rows2 = ratings._rating_sources(conn)
    assert any(r["source"] == "fpl_databank:2026-27" for r in db_rows2)
    assert live_rows2 == []


def test_new_signing_appears_with_live_only_rates():
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=5)
    _live_row(conn, 103, 1, 42, xg=1.0, minutes=90, starts=1)   # no prior rows
    rates = _rates(conn, lf_gw_count=5, sf_gw_count=2)
    assert 103 in rates
    pr = rates[103]
    assert pr.starts == 1
    assert pr.squads_made == 5       # team 2's matches in the LF window
    assert 0.0 < pr.xg_per_start <= 1.0


def test_null_starts_live_rows_skipped():
    """Rows not yet backfilled (starts NULL) must not poison the union."""
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 42, starts_null=True, xg=0.9)
    db_rows, live_rows = ratings._rating_sources(conn)
    assert live_rows == []
    rates = _rates(conn, lf_gw_count=1, sf_gw_count=1)
    assert 101 in rates          # prior-season rates intact


def test_single_live_gw_no_nan():
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=1)
    _live_row(conn, 101, 1, 42, xg=0.4)
    _live_row(conn, 104, 1, 42, xg=0.1)
    rates = _rates(conn, lf_gw_count=1, sf_gw_count=1)
    assert rates
    for pr in rates.values():
        for v in (pr.xg_per_start, pr.xa_per_start, pr.dc_hit_rate,
                  pr.saves_per_90, pr.yc_per_90, pr.rc_per_90, pr.p60):
            assert v == v and abs(v) < 1e9   # not NaN, not infinite


def test_team_ratings_include_live_gws():
    conn = connect(":memory:")
    init_db(conn)
    _seed(conn, prior_gws=5)
    r_before, _ = ratings.compute_team_ratings(conn, lf_gw_count=5, sf_gw_count=2)
    _live_row(conn, 101, 1, 42, xg=1.5, xgc=3.0)   # team A: one extreme live match
    _live_row(conn, 102, 1, 42, xg=1.5, xgc=3.0)
    r_after, _ = ratings.compute_team_ratings(conn, lf_gw_count=5, sf_gw_count=2)
    assert r_after[1].gw_count == 6                # 5 prior + 1 live
    assert r_after[1].xg90 > r_before[1].xg90
