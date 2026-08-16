"""Deterministic per-player stats digest for the AI insight feature.

No LLM here — this is the closed-shape JSON payload the analysis prompt is
grounded against. Adapts to season state: pre-season it carries prior-season
understat + projections; mid-season it adds real per-GW actuals from
player_gw_stats (written by settlement).
"""


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def _player_row(conn, player_id):
    return conn.execute(
        "SELECT id, web_name, team_id, position, price, status, ownership, form "
        "FROM players WHERE id=?", (player_id,)).fetchone()


def _team_short(conn, team_id):
    r = conn.execute("SELECT short_name FROM teams WHERE id=?", (team_id,)).fetchone()
    return r["short_name"] if r else None


def _prior_season(conn, player_id):
    row = conn.execute(
        "SELECT xg_per_90, xa_per_90, minutes, games, goals, assists, season "
        "FROM understat_players WHERE fpl_player_id=? ORDER BY season DESC LIMIT 1",
        (player_id,)).fetchone()
    if row is None:
        return None
    return {"xg_per90": round(row["xg_per_90"], 3), "xa_per90": round(row["xa_per_90"], 3),
            "minutes": row["minutes"], "games": row["games"], "goals": row["goals"],
            "assists": row["assists"], "season": row["season"]}


def _current_gw_stats(conn, player_id):
    return [{"gw": r["gw"], "minutes": r["minutes"], "goals": r["goals_scored"],
             "assists": r["assists"], "total_points": r["total_points"]}
            for r in conn.execute(
                "SELECT gw, minutes, goals_scored, assists, total_points "
                "FROM player_gw_stats WHERE player_id=? ORDER BY gw DESC LIMIT 5",
                (player_id,))]


def _projection(conn, player_id, next_gw):
    return [{"gw": r["gw"], "xp": round(r["xp"], 2)}
            for r in conn.execute(
                "SELECT gw, xp FROM xp WHERE player_id=? AND model_version='v2' "
                "AND gw BETWEEN ? AND ? ORDER BY gw",
                (player_id, next_gw, next_gw + 5))]


def _fixtures(conn, team_id, next_gw):
    rows = conn.execute(
        "SELECT gw, home_team_id, away_team_id FROM fixtures "
        "WHERE gw BETWEEN ? AND ? ORDER BY gw", (next_gw, next_gw + 5)).fetchall()
    out = []
    for r in rows:
        if r["home_team_id"] != team_id and r["away_team_id"] != team_id:
            continue
        opp_id = r["away_team_id"] if r["home_team_id"] == team_id else r["home_team_id"]
        venue = "H" if r["home_team_id"] == team_id else "A"
        fdr = conn.execute(
            "SELECT fdr_attack, fdr_defense FROM fdr WHERE team_id=? AND gw=?",
            (team_id, r["gw"])).fetchone()
        if fdr is None:
            continue
        out.append({"gw": r["gw"], "opponent": _team_short(conn, opp_id), "venue": venue,
                    "fdr_attack": fdr["fdr_attack"], "fdr_defense": fdr["fdr_defense"]})
    return out


def _data_limits(conn, player_id, prior, gws):
    limits = []
    if not gws:
        limits.append("no current-season minutes yet (pre-season; no player_gw_stats rows)")
    if prior is None:
        limits.append("no understat data matched for this player")
    else:
        current_season = conn.execute(
            "SELECT id FROM gameweeks ORDER BY id DESC LIMIT 1").fetchone()
        if current_season is not None and prior["season"] != str(current_season["id"]):
            limits.append(f"prior-season xG/xA only (understat {prior['season']})")
    return limits


def build_player_digest(conn, player_id, next_gw=None):
    """Build the closed-shape digest dict, or None for an unknown player."""
    row = _player_row(conn, player_id)
    if row is None:
        return None
    if next_gw is None:
        next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    prior = _prior_season(conn, player_id)
    gws = _current_gw_stats(conn, player_id)
    return {
        "player": {"web_name": row["web_name"], "position": row["position"],
                   "team": _team_short(conn, row["team_id"]),
                   "price": round(row["price"], 1) if row["price"] is not None else None,
                   "status": row["status"], "ownership_pct": row["ownership"],
                   "form": round(row["form"], 1) if row["form"] is not None else None},
        "prior_season": prior,
        "current_season_gws": gws,
        "projection": _projection(conn, player_id, next_gw),
        "fixtures": _fixtures(conn, row["team_id"], next_gw),
        "data_limits": _data_limits(conn, player_id, prior, gws),
    }
