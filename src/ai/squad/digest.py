"""Squad-level digest: the candidate pool + per-player context the AI is
grounded against. Deterministic, closed-shape, no LLM."""


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def build_squad_digest(conn, pool=None, next_gw=None):
    if next_gw is None:
        next_gw = _next_gw(conn)
    if next_gw is None:
        return {"next_gw": None, "budget": 100, "players": []}
    from src.decisions.squad_builder import build_candidate_pool
    pool = pool if pool is not None else build_candidate_pool(conn, next_gw)
    players = []
    for p in pool:
        prior = conn.execute(
            "SELECT xg_per_90, xa_per_90 FROM understat_players "
            "WHERE fpl_player_id=? ORDER BY season DESC LIMIT 1",
            (p["player_id"],)).fetchone()
        team_row = conn.execute("SELECT team_id FROM players WHERE id=?",
                                (p["player_id"],)).fetchone()
        team_id = team_row["team_id"] if team_row else None
        fxs = []
        if team_id is not None:
            for r in conn.execute(
                    "SELECT gw, home_team_id, away_team_id FROM fixtures "
                    "WHERE gw BETWEEN ? AND ? ORDER BY gw", (next_gw, next_gw + 2)):
                if r["home_team_id"] != team_id and r["away_team_id"] != team_id:
                    continue
                opp_id = r["away_team_id"] if r["home_team_id"] == team_id else r["home_team_id"]
                venue = "H" if r["home_team_id"] == team_id else "A"
                opp = conn.execute("SELECT short_name FROM teams WHERE id=?",
                                   (opp_id,)).fetchone()
                fdr = conn.execute("SELECT fdr_attack, fdr_defense FROM fdr "
                                   "WHERE team_id=? AND gw=?", (team_id, r["gw"])).fetchone()
                if opp is None or fdr is None:
                    continue
                fxs.append({"opponent": opp["short_name"], "venue": venue,
                            "fdr_attack": fdr["fdr_attack"], "fdr_defense": fdr["fdr_defense"]})
        players.append({
            "player_id": p["player_id"], "web_name": p["web_name"],
            "team": p["team_short"], "position": p["position"], "price": p["price"],
            "xp_next": p["xp_next"], "xp_6gw": p["xp_6gw"],
            "xg90": round(prior["xg_per_90"], 3) if prior else None,
            "xa90": round(prior["xa_per_90"], 3) if prior else None,
            "ownership_pct": p["ownership_pct"], "form": p["form"],
            "fixtures_3": fxs,
        })
    return {"next_gw": next_gw, "budget": 100, "players": players}
