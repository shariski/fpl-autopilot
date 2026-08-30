"""Deterministic cross-check for user speculation insights (v0.26).

The user's qualitative reads (new manager, cohesion, player traits) are verified
against the system's own numbers. Club data ALWAYS comes from the DB — real-world
priors are never assumed (observed 2026-08-26: Wissa mis-attributed to Brentford
by contamination; the DB says Newcastle).
"""
from src.data import repository
from src.analytics import ratings


def _player_checks(conn, player_id):
    g1 = conn.execute(
        """SELECT minutes, starts, expected_goals, total_points
           FROM player_gw_stats WHERE player_id=? AND gw=1""", (player_id,)).fetchone()
    live = conn.execute(
        """SELECT COUNT(DISTINCT gw) AS gws, COALESCE(SUM(starts),0) AS st
           FROM player_gw_stats WHERE player_id=? AND starts IS NOT NULL""",
        (player_id,)).fetchone()
    xp = conn.execute(
        """SELECT xp FROM xp WHERE player_id=? AND model_version='v2'
           AND gw=(SELECT MIN(id) FROM gameweeks WHERE finished=0)""",
        (player_id,)).fetchone()
    us = conn.execute("SELECT xg_per_90 FROM understat_players WHERE fpl_player_id=?",
                      (player_id,)).fetchone()
    return {
        "gw1_minutes": g1["minutes"] if g1 else None,
        "gw1_starts": g1["starts"] if g1 else None,
        "gw1_xg": g1["expected_goals"] if g1 else None,
        "gw1_points": g1["total_points"] if g1 else None,
        "live_gws": live["gws"] if live else 0,
        "live_starts": live["st"] if live else 0,
        "xp_next": xp["xp"] if xp else None,
        "xg_per_90": us["xg_per_90"] if us else None,
    }


def _team_checks(conn, team_id):
    last = conn.execute(
        """SELECT th.short_name h, ta.short_name a, f.home_score, f.away_score
           FROM fixtures f JOIN teams th ON th.id=f.home_team_id
           JOIN teams ta ON ta.id=f.away_team_id
           WHERE f.finished=1 AND (f.home_team_id=? OR f.away_team_id=?)
           ORDER BY f.gw DESC LIMIT 1""", (team_id, team_id)).fetchone()
    result = None
    if last is not None:
        result = (f"{last['h']} {last['home_score']}-{last['away_score']} {last['a']}"
                  if last["home_score"] is not None else f"{last['h']} v {last['a']}")
    next3 = [dict(r) for r in conn.execute(
        """SELECT f.gw, th.short_name h, ta.short_name a
           FROM fixtures f JOIN teams th ON th.id=f.home_team_id
           JOIN teams ta ON ta.id=f.away_team_id
           WHERE f.gw >= (SELECT MIN(id) FROM gameweeks WHERE finished=0)
             AND (f.home_team_id=? OR f.away_team_id=?)
           ORDER BY f.gw LIMIT 3""", (team_id, team_id))]
    tr, _la = ratings.compute_team_ratings(conn)
    team = tr.get(team_id)
    return {"last_result": result,
            "next3": [f"{r['h']} v {r['a']}" for r in next3],
            "xg90": team.xg90 if team else None}


def _verdict(checks):
    """Heuristic, deterministic, no AI: a player-scoped note contradicts data when
    the player has live GWs but zero starts; everything else is neutral."""
    c = checks.get("player")
    if c and c.get("live_gws") and c.get("live_starts") == 0:
        return "contradicts"
    return "neutral"


def build_theses(conn):
    """{note_id, note, team_short, player_name, verdict, checks} per active note."""
    out = []
    for n in repository.list_speculation_notes(conn):
        checks = {}
        if n["player_id"]:
            checks["player"] = _player_checks(conn, n["player_id"])
        elif n["team_id"]:
            checks["team"] = _team_checks(conn, n["team_id"])
        out.append({"note_id": n["id"], "note": n["note"],
                    "team_short": n["team_short"], "player_name": n["player_name"],
                    "verdict": _verdict(checks), "checks": checks})
    return out
