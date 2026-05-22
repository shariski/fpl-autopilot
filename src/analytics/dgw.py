def team_fixture_count(conn, team_id, gw):
    """Number of fixtures a team has in a GW: 0 = blank, 1 = single, 2 = double gameweek."""
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM fixtures WHERE gw=? AND (home_team_id=? OR away_team_id=?)",
        (gw, team_id, team_id)).fetchone()
    return r["c"]


def team_gw_fdr(conn, team_id, gw):
    """The team's stored FDR row for a GW, or None."""
    return conn.execute(
        "SELECT fdr_attack, fdr_defense FROM fdr WHERE team_id=? AND gw=?", (team_id, gw)).fetchone()
