"""Season-aware rating windows (v0.27) tests.

In-season (live rows present) the ratings blend LF20/SF6 at 0.6/0.4 instead of the
pre-season LF38/SF6 at 0.8/0.2, so early live GWs move team/player rates ~2x faster.
Pins: pre-season defaults == explicit pre-season params; in-season defaults ==
explicit in-season params; the two differ (in-season trusts recent data more).
"""
from src.analytics import ratings


def _seed(conn, live_gws=0):
    conn.executemany(
        "INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
        [(1, "T1", "T1"), (2, "T2", "T2")])
    conn.executemany(
        "INSERT INTO players (id, web_name, team_id, position) VALUES (?,?,?,?)",
        [(101, "P1", 1, "DEF"), (102, "P2", 1, "MID"),
         (103, "P3", 2, "DEF"), (104, "P4", 2, "MID")])
    rows = []
    for gw in (33, 34, 35, 36, 37, 38):
        for pid in (101, 102, 103, 104):
            rows.append((pid, gw, "fpl_databank:2025-26", 90, 0.05, 0.05, 1.0,
                         5, 1, 0.05, 0, 0))
    conn.executemany(
        "INSERT INTO player_stats (player_id, gw, source, minutes, xg, xa, xgc, "
        "dc, starts, saves, yellow_cards, red_cards) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    live = []
    for gw in range(1, live_gws + 1):
        for pid in (101, 102, 103, 104):
            live.append((pid, gw, 1, 90, 0, 0, 0, 3, 0, "2026-09-01T00:00:00+00:00",
                         1, 0, 10, 0.05, 0.05, 4.0, 8, 0, 0))
    conn.executemany(
        "INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes, "
        "goals_scored, assists, clean_sheets, total_points, bonus, settled_at, "
        "starts, saves, bps, expected_goals, expected_assists, "
        "expected_goals_conceded, defensive_contribution, yellow_cards, "
        "red_cards) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", live)
    conn.commit()


def _team1_xgc(conn, **kwargs):
    ratings_map, _la = ratings.compute_team_ratings(conn, **kwargs)
    return ratings_map[1].xgc90


def test_preseason_defaults_equal_explicit_preseason_params(db):
    """No live rows: defaults stay LF38/SF6 @ 0.8/0.2 (v0.23 behavior)."""
    _seed(db, live_gws=0)
    default = _team1_xgc(db)
    explicit = _team1_xgc(db, lf_gw_count=38, sf_gw_count=6)
    assert default == explicit
    assert 0.9 < default < 1.1  # pure databank: xGC 1.0/match


def test_inseason_defaults_equal_explicit_inseason_params(db):
    """Live rows present: defaults switch to LF20/SF6 @ 0.6/0.4."""
    _seed(db, live_gws=1)
    default = _team1_xgc(db)
    explicit = _team1_xgc(db, lf_gw_count=20, sf_gw_count=6)
    assert default == explicit


def test_inseason_trusts_recent_more_than_preseason_windows(db):
    """Live xGC 4.0 (worse than databank 1.0) moves the rating further under the
    in-season window defaults than under the pre-season window params."""
    _seed(db, live_gws=1)
    in_season = _team1_xgc(db)
    pre = _team1_xgc(db, lf_gw_count=38, sf_gw_count=6,
                     lf_weight=0.8, sf_weight=0.2)
    assert in_season > pre > 1.0  # in-season must sit closer to live 4.0
