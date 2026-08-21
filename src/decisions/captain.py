"""Captain ranker (Decision Layer). Reads Analytics output (xp) + squad/fixtures and
ranks the 15-man squad for captaincy. Suggest-only — no execution/persistence (CLAUDE.md B2)."""
import json

from src.decisions import confidence as confidence_mod

MODEL_VERSION = "v2"

# v0.20 captaincy ceiling term: rank by xP plus a small share of the player's
# goal-involvement upside (xgoals + xassists). GKP/DEF have ~zero upside, so the
# term prefers premium attackers when the xP gap is small — the backtest's
# captain-proxy showed pure max-xP underperforms premium-biased picks. Pinned
# in decision-engine.md (B4).
CEILING_WEIGHT = 0.15


def _score(c):
    return c["xp"] + CEILING_WEIGHT * (c.get("xgoals", 0.0) + c.get("xassists", 0.0))


def rank_captains(candidates):
    """Pure: rank captain candidates, build reasoning, return the top-5 picks.

    Each candidate: {player_id, web_name, position, xp, xminutes, fdr_attack,
    fixture, xgoals, xassists}. Order: ceiling-adjusted score desc (xp + 0.15×
    goal involvement), tiebreak xminutes desc, then fdr_attack asc.
    Returns up to 5 picks: {player_id, web_name, xp, fixture, reason}.
    Reason — rank 1: "Highest xP ({xp}) {fixture}. Next best {name} {xp2} —
    gap {gap}."; when the ceiling term reorders the top pick the reason says
    "ceiling-adjusted" (B9: notification layer consumes this verbatim).
    """
    ranked = sorted(candidates,
                    key=lambda c: (-_score(c), -c["xminutes"], c["fdr_attack"]))[:5]
    picks = []
    max_xp_id = max(candidates, key=lambda c: c["xp"])["player_id"] if candidates else None
    for i, c in enumerate(ranked):
        if i == 0 and len(ranked) > 1:
            s = ranked[1]
            if c["player_id"] == max_xp_id:
                reason = (f"Highest xP ({c['xp']}) {c['fixture']}. "
                          f"Next best {s['web_name']} {s['xp']} — gap {round(c['xp'] - s['xp'], 1)}.")
            else:
                ceiling = round(CEILING_WEIGHT * (c.get("xgoals", 0.0) + c.get("xassists", 0.0)), 2)
                reason = (f"Ceiling-adjusted top pick: {c['web_name']} {c['xp']} xP "
                          f"+{ceiling} upside {c['fixture']}. Next best {s['web_name']} "
                          f"{s['xp']} — gap {round(c['xp'] - s['xp'], 1)}.")
        elif i == 0:
            reason = f"Highest xP ({c['xp']}) {c['fixture']}."
        else:
            reason = f"xP {c['xp']} {c['fixture']}."
        picks.append({"player_id": c["player_id"], "web_name": c["web_name"],
                      "xp": c["xp"], "fixture": c["fixture"], "reason": reason})
    return picks


def _next_gw(conn):
    row = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row else None


def _squad_element_ids(conn):
    """Elements of the current squad IN STARTING POSITIONS (1-11).

    FPL requires captain and vice to be in the starting XI — proposing a
    benched player is rejected with 'squad_vice_captain_invalid_position'
    (observed 2026-08-20 on a live execute-lineup submit).
    """
    row = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if row is None:
        return []
    picks = json.loads(row["picks_json"])
    return [p["element"] for p in picks if int(p.get("position", 1)) <= 11]


def _fixture_and_fdr(conn, team_id, team_short, gw):
    fx = conn.execute(
        """SELECT f.home_team_id, th.short_name AS home_short, ta.short_name AS away_short
           FROM fixtures f
           JOIN teams th ON th.id = f.home_team_id
           JOIN teams ta ON ta.id = f.away_team_id
           WHERE f.gw = ? AND (f.home_team_id = ? OR f.away_team_id = ?)
           LIMIT 1""", (gw, team_id, team_id)).fetchone()
    fdr_row = conn.execute(
        "SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (team_id, gw)).fetchone()
    fdr_attack = fdr_row["fdr_attack"] if fdr_row else 5
    if fx is None:
        return "—", fdr_attack
    if fx["home_team_id"] == team_id:
        return f"{team_short} v {fx['away_short']} (H)", fdr_attack
    return f"{team_short} v {fx['home_short']} (A)", fdr_attack


def _build_candidate(conn, player_id, gw):
    pl = conn.execute(
        """SELECT p.id AS player_id, p.web_name, p.position, p.team_id,
                  t.short_name AS team_short
           FROM players p JOIN teams t ON t.id = p.team_id
           WHERE p.id = ?""", (player_id,)).fetchone()
    if pl is None:
        return None
    xp_row = conn.execute(
        "SELECT xp, xminutes, xgoals, xassists FROM xp WHERE player_id=? AND gw=? AND model_version=?",
        (player_id, gw, MODEL_VERSION)).fetchone()
    if xp_row is None:
        return {"player_id": pl["player_id"], "web_name": pl["web_name"],
                "position": pl["position"], "xp": 0.0, "xminutes": 0.0,
                "xgoals": 0.0, "xassists": 0.0,
                "fdr_attack": 5, "fixture": "—"}
    fixture, fdr_attack = _fixture_and_fdr(conn, pl["team_id"], pl["team_short"], gw)
    return {"player_id": pl["player_id"], "web_name": pl["web_name"],
            "position": pl["position"], "xp": xp_row["xp"], "xminutes": xp_row["xminutes"],
            "xgoals": xp_row["xgoals"] or 0.0, "xassists": xp_row["xassists"] or 0.0,
            "fdr_attack": fdr_attack, "fixture": fixture}


def get_captain_picks(conn):
    """Reader: returns the /api/captain payload {picks, vice_player_id, confidence} for the next GW."""
    gw = _next_gw(conn)
    if gw is None:
        return {"picks": [], "vice_player_id": None, "confidence": None}
    candidates = [c for c in (_build_candidate(conn, pid, gw)
                              for pid in _squad_element_ids(conn)) if c is not None]
    picks = rank_captains(candidates)
    if not picks:
        return {"picks": [], "vice_player_id": None, "confidence": None}
    vice = picks[1]["player_id"] if len(picks) > 1 else None
    ids = [picks[0]["player_id"]] + ([vice] if vice is not None else [])
    rows = conn.execute(
        f"SELECT status FROM players WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    statuses = [r["status"] for r in rows]
    gap = picks[0]["xp"] - picks[1]["xp"] if len(picks) > 1 else None
    conf = confidence_mod.score(staleness_hours=confidence_mod.hours_since_refresh(conn),
                                statuses=statuses, gap=gap)
    return {"picks": picks, "vice_player_id": vice, "confidence": conf}
