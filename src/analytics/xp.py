from src.data import repository

GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
STATUS_MULT = {"a": 1.0, "d": 0.5, "i": 0.0, "s": 0.0, "u": 0.0}
FDR_ATTACK_MULT = {1: 1.20, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.80}
CS_PROB = {1: 0.55, 2: 0.45, 3: 0.35, 4: 0.22, 5: 0.12}
MODEL_VERSION = "v1"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def compute_player_xp(position, status, xg90, xa90, minutes, games, fdr_attack, fdr_defense):
    """Pure xP v1 for one player in one fixture. Returns {xminutes, xgoals, xassists, xcs, xp}."""
    xmin = min(minutes / games, 90.0) * STATUS_MULT.get(status, 1.0) if games else 0.0
    p_appear = _clamp(xmin / 20, 0.0, 1.0)
    p60 = _clamp((xmin - 30) / 30, 0.0, 1.0)
    appearance = p_appear + p60
    amult = FDR_ATTACK_MULT[fdr_attack]
    xgoals = xg90 * (xmin / 90) * amult
    xassists = xa90 * (xmin / 90) * amult
    xcs = CS_PROB[fdr_defense] * p60
    xp_total = appearance + xgoals * GOAL_PTS[position] + xassists * 3 + xcs * CS_PTS[position]
    return {
        "xminutes": round(xmin, 2),
        "xgoals": round(xgoals, 3),
        "xassists": round(xassists, 3),
        "xcs": round(xcs, 3),
        "xp": round(xp_total, 2),
    }


def compute_and_store(conn, horizon=6):
    """Compute xP v1 for the next `horizon` GWs (from the first unfinished GW) and persist. Returns row count."""
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    next_gw = nxt["gw"] if nxt else None
    if next_gw is None:
        return 0
    last_gw = next_gw + horizon - 1
    fdr_map = {
        (r["team_id"], r["gw"]): (r["fdr_attack"], r["fdr_defense"])
        for r in conn.execute(
            "SELECT team_id, gw, fdr_attack, fdr_defense FROM fdr WHERE gw BETWEEN ? AND ?",
            (next_gw, last_gw))
    }
    if not fdr_map:
        return 0
    players = conn.execute(
        """SELECT p.id AS player_id, p.position, p.status, p.team_id,
                  u.xg_per_90, u.xa_per_90, u.minutes, u.games
           FROM players p JOIN understat_players u ON u.fpl_player_id = p.id""").fetchall()
    rows = []
    for pl in players:
        for gw in range(next_gw, last_gw + 1):
            fdr = fdr_map.get((pl["team_id"], gw))
            if fdr is None:
                continue
            res = compute_player_xp(pl["position"], pl["status"], pl["xg_per_90"], pl["xa_per_90"],
                                    pl["minutes"], pl["games"], fdr[0], fdr[1])
            rows.append({"player_id": pl["player_id"], "gw": gw, "model_version": MODEL_VERSION, **res})
    repository.upsert_xp(conn, rows)
    return len(rows)
