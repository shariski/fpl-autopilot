from src.data import repository
from src.analytics import ratings

GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
STATUS_MULT = {"a": 1.0, "d": 0.5, "i": 0.0, "s": 0.0, "u": 0.0}
FDR_ATTACK_MULT = {1: 1.20, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.80}
CS_PROB = {1: 0.55, 2: 0.45, 3: 0.35, 4: 0.22, 5: 0.12}
MODEL_VERSION = "v1"

# xP v2 constants (decision-engine.md v0.13; B4 — do not change without a log entry)
MODEL_VERSION_V2 = "v2"
FA_BOOST = 1.38            # calibrated 24-25/25-26: weighted assists/xA 1.375/1.382
BONUS_PER_START = 0.29     # calibrated: bonus/start 0.286-0.288
SUB_RATIO = 0.30           # Mn/Sub ÷ Mn/St league constant
CS_BIAS = 0.04             # Poisson over-dispersion correction (P(0) under-predicted)
TWOG_BIAS = 0.045          # Poisson over-dispersion correction (P(>=2) under-predicted)
VENUE_ATTACK = {"H": 1.15, "A": 0.87}     # goal + assist components
VENUE_DEFENSE = {"H": 0.88, "A": 1.12}    # CS + 2+GC (+ bonus for GK/DEF)
VENUE_SAVES = {"H": 0.86, "A": 1.14}      # GK saves (home GKs face fewer shots)
VENUE_STARTS = {"H": 1.0, "A": 1.0}


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
    # Stale-row cleanup: players who lost their understat link (season rollover /
    # rematch) must not keep ghost xp rows — upsert alone would serve projections
    # from another player's data (observed 2026-08-14: Heaton kept xP with no match).
    joined_ids = {pl["player_id"] for pl in players}
    if joined_ids:
        placeholders = ",".join("?" * len(joined_ids))
        conn.execute(
            f"DELETE FROM xp WHERE model_version=? AND gw BETWEEN ? AND ? "
            f"AND player_id NOT IN ({placeholders})",
            (MODEL_VERSION, next_gw, last_gw, *sorted(joined_ids)))
    else:
        conn.execute(
            "DELETE FROM xp WHERE model_version=? AND gw BETWEEN ? AND ?",
            (MODEL_VERSION, next_gw, last_gw))
    conn.commit()
    repository.upsert_xp(conn, rows)
    return len(rows)


# ---------- xP v2 (v0.13): 11-component model ----------

def _twogc(position, lam):
    """Expected 2+ goals-conceded penalty: P(>=2 | lambda) x -1, GK/DEF only (bias-corrected)."""
    if position not in ("GKP", "DEF"):
        return 0.0
    import math
    p = min(1.0, (1 - math.exp(-lam) * (1 + lam)) + TWOG_BIAS)
    return -p


def compute_player_xp_v2(position, status, chance_of_playing, starts, squads_made,
                         xg_per_start, xa_per_start, dc_hit_rate, saves_per_90,
                         yc_per_90, rc_per_90, p60, team_xgc90,
                         xg_ratio=1.0, xgc_ratio=1.0, dc_ratio=1.0, venue="H"):
    """xP v2 for one player in one fixture (decision-engine.md v0.13).

    Returns {p_start, xminutes, xgoals, xassists, xcs, xbonus, xdc, xcs_lambda, xp}.
    All rates are per-start (per-90 for saves/YC/RC); ratios are damped FDR v2 multipliers.
    """
    import math
    raw_start = min(1.0, chance_of_playing * starts / squads_made) if squads_made else 0.0
    p_start = raw_start * STATUS_MULT.get(status, 1.0)
    lam = team_xgc90 * xg_ratio
    if p_start <= 0:
        return {"p_start": 0.0, "xminutes": 0.0, "xgoals": 0.0, "xassists": 0.0,
                "xcs": 0.0, "xbonus": 0.0, "xdc": 0.0, "xcs_lambda": round(lam, 4), "xp": 0.0}

    venue_a = VENUE_ATTACK.get(venue, 1.0)
    venue_d = VENUE_DEFENSE.get(venue, 1.0)
    venue_s = VENUE_SAVES.get(venue, 1.0)

    saves = saves_per_90 * xg_ratio * venue_s if position == "GKP" else 0.0
    yc = yc_per_90
    rc = rc_per_90
    defensive_pos = position in ("GKP", "DEF")
    bonus = BONUS_PER_START * (xg_ratio if defensive_pos else xgc_ratio) \
        * (venue_d if defensive_pos else venue_a)
    xassists = xa_per_start * xgc_ratio * FA_BOOST * 3 * venue_a
    xgoals = xg_per_start * xgc_ratio * GOAL_PTS[position] * venue_a
    xcs = min(1.0, math.exp(-lam) + CS_BIAS) * CS_PTS[position] * venue_d
    twogc = _twogc(position, lam) * venue_d
    xdc = dc_hit_rate * dc_ratio * 2

    base = yc + rc + bonus + xassists + xgoals + twogc
    xp_total = p_start * (1.0 + p60 + saves + xcs + xdc + base) \
        + (1.0 - p_start) * base * SUB_RATIO

    return {
        "p_start": round(p_start, 4),
        "xminutes": round(p_start * 90, 2),
        "xgoals": round(xgoals * p_start, 3),
        "xassists": round(xassists * p_start, 3),
        "xcs": round(xcs * p_start, 3),
        "xbonus": round(bonus * p_start, 3),
        "xdc": round(xdc * p_start, 3),
        "xcs_lambda": round(lam, 4),
        "xp": round(xp_total, 2),
    }


def compute_and_store_v2(conn, horizon=6):
    """Compute xP v2 for the next `horizon` GWs (B5 parallel-run alongside v1). Returns row count."""
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    next_gw = nxt["gw"] if nxt else None
    if next_gw is None:
        return 0
    last_gw = next_gw + horizon - 1

    rates = ratings.compute_player_rates(conn)
    if not rates:
        conn.execute(
            "DELETE FROM xp WHERE model_version=? AND gw BETWEEN ? AND ?",
            (MODEL_VERSION_V2, next_gw, last_gw))
        conn.commit()
        return 0
    team_ratings, la = ratings.compute_team_ratings(conn)
    overrides = ratings.promoted_overrides(conn)

    fdr_map = {
        (r["team_id"], r["gw"]): (r["fdr_attack_mult"], r["fdr_defense_mult"])
        for r in conn.execute(
            "SELECT team_id, gw, fdr_attack_mult, fdr_defense_mult FROM fdr "
            "WHERE gw BETWEEN ? AND ?", (next_gw, last_gw))
    }
    # opponent + venue per (team, gw); one fixture per GW assumed (DGW: first fixture wins — same
    # approximation as chips v1; documented in decision-engine.md)
    opp_map, venue_map = {}, {}
    for fx in conn.execute(
            "SELECT gw, home_team_id, away_team_id FROM fixtures WHERE gw BETWEEN ? AND ?",
            (next_gw, last_gw)):
        gw, h, a = fx["gw"], fx["home_team_id"], fx["away_team_id"]
        opp_map.setdefault((h, gw), a)
        opp_map.setdefault((a, gw), h)
        venue_map.setdefault((h, gw), "H")
        venue_map.setdefault((a, gw), "A")

    players = conn.execute(
        """SELECT id AS player_id, position, status, team_id, chance_of_playing
           FROM players""").fetchall()

    def _dc_ratio(team_id, gw):
        opp = opp_map.get((team_id, gw))
        if opp is None or la.dc90 <= 0:
            return 1.0
        opp_r = team_ratings.get(opp)
        if opp_r is None:
            ov = overrides.get(opp)
            return ratings.damp(ov[1] / la.dc90) if ov else 1.0
        return ratings.damp(opp_r.dc90 / la.dc90)

    rows = []
    for pl in players:
        pr = rates.get(pl["player_id"])
        if pr is None:
            continue
        team_xgc90 = team_ratings.get(pl["team_id"], None)
        if team_xgc90 is None:
            ov = overrides.get(pl["team_id"])
            team_xgc90 = ov[1] if ov else la.xgc90
        else:
            team_xgc90 = team_xgc90.xgc90
        for gw in range(next_gw, last_gw + 1):
            fdr = fdr_map.get((pl["team_id"], gw), (1.0, 1.0))  # missing FDR v2 -> neutral
            venue = venue_map.get((pl["team_id"], gw))
            if venue is None:
                continue  # no fixture this GW: skip (v1 skips missing FDR too)
            res = compute_player_xp_v2(
                pl["position"], pl["status"], pl["chance_of_playing"] or 1.0,
                pr.starts, pr.squads_made, pr.xg_per_start, pr.xa_per_start,
                pr.dc_hit_rate, pr.saves_per_90, pr.yc_per_90, pr.rc_per_90, pr.p60,
                team_xgc90, xg_ratio=fdr[1], xgc_ratio=fdr[0],
                dc_ratio=_dc_ratio(pl["team_id"], gw), venue=venue)
            rows.append({"player_id": pl["player_id"], "gw": gw,
                         "model_version": MODEL_VERSION_V2, **res})
    joined_ids = set(rates)
    placeholders = ",".join("?" * len(joined_ids))
    conn.execute(
        f"DELETE FROM xp WHERE model_version=? AND gw BETWEEN ? AND ? "
        f"AND player_id NOT IN ({placeholders})",
        (MODEL_VERSION_V2, next_gw, last_gw, *sorted(joined_ids)))
    conn.commit()
    repository.upsert_xp(conn, rows)
    return len(rows)
