"""Deterministic candidate pool for the AI squad builder.

The AI never sees all 587 players — this module narrows to a budget-flexible,
legal prefiltered pool (~90-100) so the LLM's judgment is applied to a
tractable set. Every field here lands in the digest the AI is grounded against.
"""


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def build_candidate_pool(conn, next_gw=None):
    if next_gw is None:
        next_gw = _next_gw(conn)
    if next_gw is None:
        return []
    rows = conn.execute(
        """SELECT p.id AS player_id, p.web_name, p.position, p.price, p.status,
                  p.ownership, p.form,
                  t.short_name AS team_short,
                  x1.xp AS xp_next,
                  SUM(x.xp) AS xp_6gw
           FROM players p
           JOIN teams t ON t.id = p.team_id
           JOIN xp x ON x.player_id = p.id AND x.model_version = 'v1'
                AND x.gw BETWEEN ? AND ?
           JOIN xp x1 ON x1.player_id = p.id AND x1.model_version = 'v1' AND x1.gw = ?
           WHERE p.status IN ('a', 'd') AND p.price >= 4.0
           GROUP BY p.id
        """, (next_gw, next_gw + 5, next_gw)).fetchall()
    players = []
    for r in rows:
        xp6 = round(r["xp_6gw"], 2)
        price = r["price"]
        players.append({
            "player_id": r["player_id"], "web_name": r["web_name"],
            "team_short": r["team_short"], "position": r["position"],
            "price": price, "status": r["status"],
            "xp_next": round(r["xp_next"], 2) if r["xp_next"] is not None else None,
            "xp_6gw": xp6,
            "value": round(xp6 / price, 4) if price else None,
            "ownership_pct": round(r["ownership"], 1) if r["ownership"] is not None else None,
            "form": round(r["form"], 1) if r["form"] is not None else None,
        })
    pool = []
    for pos in ("GKP", "DEF", "MID", "FWD"):
        pos_players = [p for p in players if p["position"] == pos]
        by_xp = sorted(pos_players, key=lambda p: p["xp_6gw"], reverse=True)[:15]
        by_val = sorted((p for p in pos_players if p["value"] is not None),
                        key=lambda p: p["value"], reverse=True)[:10]
        seen = set()
        for p in by_xp + by_val:
            if p["player_id"] not in seen:
                seen.add(p["player_id"])
                pool.append(p)
    pool.sort(key=lambda p: p["xp_6gw"], reverse=True)
    return pool
