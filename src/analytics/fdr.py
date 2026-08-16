from src.data import repository
from src.analytics import ratings


def quintile_bucket(value, distribution):
    """Rank `value` against `distribution`, returning 1-5 (5 = highest). Ties -> lower bucket."""
    below = sum(1 for x in distribution if x < value)
    return min(below * 5 // len(distribution) + 1, 5)


def compute_fdr(teams, fixtures):
    """Pure: teams = dicts with id + 4 strength columns; fixtures = dicts with gw/home_team_id/away_team_id.
    Returns one row per team per fixture: {team_id, gw, fdr_attack, fdr_defense}."""
    by_id = {t["id"]: t for t in teams}
    def_home = [t["strength_defence_home"] for t in teams]
    def_away = [t["strength_defence_away"] for t in teams]
    att_home = [t["strength_attack_home"] for t in teams]
    att_away = [t["strength_attack_away"] for t in teams]

    rows = []
    for fx in fixtures:
        h, a = by_id[fx["home_team_id"]], by_id[fx["away_team_id"]]
        gw = fx["gw"]
        rows.append({"team_id": h["id"], "gw": gw,
                     "fdr_attack": quintile_bucket(a["strength_defence_away"], def_away),
                     "fdr_defense": quintile_bucket(a["strength_attack_away"], att_away)})
        rows.append({"team_id": a["id"], "gw": gw,
                     "fdr_attack": quintile_bucket(h["strength_defence_home"], def_home),
                     "fdr_defense": quintile_bucket(h["strength_attack_home"], att_home)})
    return rows


def compute_and_store(conn, horizon=6):
    """Compute FDR for the next `horizon` GWs (from the first unfinished GW) and persist. Returns row count."""
    teams = [dict(r) for r in conn.execute(
        "SELECT id, strength_attack_home, strength_attack_away, "
        "strength_defence_home, strength_defence_away FROM teams")]
    if not teams:
        return 0
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    next_gw = nxt["gw"] if nxt else None
    if next_gw is None:
        return 0
    fixtures = [dict(r) for r in conn.execute(
        "SELECT gw, home_team_id, away_team_id FROM fixtures WHERE gw BETWEEN ? AND ?",
        (next_gw, next_gw + horizon - 1))]
    rows = compute_fdr(teams, fixtures)
    repository.upsert_fdr(conn, rows)
    return len(rows)


# ---------- FDR v2 (v0.12): xG-based continuous multipliers ----------

def compute_fdr_v2(ratings_map, league_avg, fixtures, overrides=None, min_gws=None):
    """Per (team, gw) continuous multipliers from opponent ratings (damped ratios).

    fdr_attack_mult  = damp(opponent xGC/90 ÷ LA xGC/90)   (opponent defense, for attackers)
    fdr_defense_mult = damp(opponent xG/90 ÷ LA xG/90)     (opponent attack, for defenders)
    Promoted teams without enough databank history use `overrides[team_id] = (xg90, xgc90)`.
    """
    overrides = overrides or {}
    min_gws = min_gws if min_gws is not None else ratings.MIN_GWS_FOR_RATING
    rows = []
    for fx in fixtures:
        gw = fx["gw"]
        h, a = fx["home_team_id"], fx["away_team_id"]
        for team_id, opp_id in ((h, a), (a, h)):
            opp = ratings.opponent_rating(ratings_map, overrides, opp_id)
            if opp is None:
                continue
            rows.append({
                "team_id": team_id, "gw": gw,
                "fdr_attack_mult": round(ratings.damp(opp.xgc90 / league_avg.xgc90), 4),
                "fdr_defense_mult": round(ratings.damp(opp.xg90 / league_avg.xg90), 4),
            })
    return rows


def compute_and_store_v2(conn, horizon=6):
    """FDR v2 for the next `horizon` GWs from the databank ratings. Returns row count."""
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    next_gw = nxt["gw"] if nxt else None
    if next_gw is None:
        return 0
    fixtures = [dict(r) for r in conn.execute(
        "SELECT gw, home_team_id, away_team_id FROM fixtures WHERE gw BETWEEN ? AND ?",
        (next_gw, next_gw + horizon - 1))]
    if not fixtures:
        return 0
    ratings_map, league_avg = ratings.compute_team_ratings(conn)
    overrides = ratings.promoted_overrides(conn)
    rows = compute_fdr_v2(ratings_map, league_avg, fixtures, overrides)
    if not rows:
        return 0
    repository.upsert_fdr_v2(conn, rows)
    return len(rows)
