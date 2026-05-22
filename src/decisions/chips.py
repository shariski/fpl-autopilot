import json
from src.analytics import dgw
from src.analytics.xp import compute_player_xp

PREMIUM_PRICE = 9.5
BENCH_BOOST_THRESHOLD = 4.0
TRIPLE_CAPTAIN_XP = 12.0
FREE_HIT_COVERAGE = 8
WILDCARD_SWING_PLAYERS = 3
WILDCARD_SWING_DELTA = 2

# FPL chip codes -> our names (for chips_used suppression).
FPL_CHIP = {"wildcard": "wildcard", "freehit": "free_hit", "bboost": "bench_boost", "3xc": "triple_captain"}


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def _squad(conn):
    snap = conn.execute(
        "SELECT picks_json, chips_used_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if snap is None:
        return [], [], set()
    picks = json.loads(snap["picks_json"])
    chips_used_raw = json.loads(snap["chips_used_json"]) if snap["chips_used_json"] else []
    chips_used = {FPL_CHIP.get(c, c) for c in chips_used_raw}
    rows = []
    for pk in picks:
        p = conn.execute(
            "SELECT p.id, p.web_name, p.position, p.status, p.team_id, p.price, "
            "u.xg_per_90, u.xa_per_90, u.minutes, u.games "
            "FROM players p LEFT JOIN understat_players u ON u.fpl_player_id = p.id WHERE p.id=?",
            (pk["element"],)).fetchone()
        if p is None:
            continue
        d = dict(p)
        d["pick_position"] = pk["position"]
        rows.append(d)
    return picks, rows, chips_used


def _player_gw_xp(conn, r, gw):
    if r["xg_per_90"] is None:  # unmatched player, no Understat rates
        return 0.0
    n = dgw.team_fixture_count(conn, r["team_id"], gw)
    if n == 0:
        return 0.0
    fd = dgw.team_gw_fdr(conn, r["team_id"], gw)
    if fd is None:
        return 0.0
    one = compute_player_xp(r["position"], r["status"], r["xg_per_90"], r["xa_per_90"],
                            r["minutes"], r["games"], fd["fdr_attack"], fd["fdr_defense"])["xp"]
    return round(n * one, 2)


def _gw_has_fixtures(conn, gw):
    """Return True only if at least one fixture exists for this GW in the DB."""
    r = conn.execute("SELECT COUNT(*) AS n FROM fixtures WHERE gw=?", (gw,)).fetchone()
    return r and r["n"] > 0


def free_hit_trigger(conn, squad, gws):
    for gw in gws:
        if not _gw_has_fixtures(conn, gw):
            continue  # GW not yet populated; not a known blank
        covered = sum(1 for r in squad if dgw.team_fixture_count(conn, r["team_id"], gw) >= 1)
        if covered < FREE_HIT_COVERAGE:
            return f"Blank GW{gw}: only {covered} of 15 squad players have a fixture."
    return None


def bench_boost_trigger(conn, squad, gws):
    for gw in gws:
        # Require a true DGW: every squad player has at least 2 fixtures.
        if all(dgw.team_fixture_count(conn, r["team_id"], gw) >= 2 for r in squad):
            bench = [r for r in squad if r["pick_position"] >= 12]
            bench_xp = round(sum(_player_gw_xp(conn, r, gw) for r in bench), 1)
            if bench_xp > BENCH_BOOST_THRESHOLD:
                return f"GW{gw}: all 15 have fixtures; bench xP {bench_xp} (> {BENCH_BOOST_THRESHOLD})."
    return None


def triple_captain_trigger(conn, squad, gws):
    for gw in gws:
        for r in squad:
            if r["price"] is None or r["price"] < PREMIUM_PRICE:
                continue
            if dgw.team_fixture_count(conn, r["team_id"], gw) != 2:
                continue
            fd = dgw.team_gw_fdr(conn, r["team_id"], gw)
            if fd is None or fd["fdr_attack"] > 2:
                continue
            x = _player_gw_xp(conn, r, gw)
            if x >= TRIPLE_CAPTAIN_XP:
                return f"GW{gw} DGW: {r['web_name']} DGW-xP {x} (>= {TRIPLE_CAPTAIN_XP}), FDR {fd['fdr_attack']}."
    return None


def wildcard_trigger(conn, squad, next_gw):
    worsening = 0
    for r in squad:
        a = conn.execute("SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (r["team_id"], next_gw)).fetchone()
        b = conn.execute("SELECT fdr_attack FROM fdr WHERE team_id=? AND gw=?", (r["team_id"], next_gw + 3)).fetchone()
        if a and b and (b["fdr_attack"] - a["fdr_attack"]) >= WILDCARD_SWING_DELTA:
            worsening += 1
    if worsening >= WILDCARD_SWING_PLAYERS:
        return f"{worsening} squad players face FDR worsening by {WILDCARD_SWING_DELTA}+ over the next 3 GW."
    return None


def recommend_chip(conn, horizon=6):
    next_gw = _next_gw(conn)
    _, squad, chips_used = _squad(conn)
    if next_gw is None or not squad:
        return {"recommendation": None}
    gws = list(range(next_gw, next_gw + horizon))
    candidates = [
        ("triple_captain", triple_captain_trigger(conn, squad, gws)),
        ("bench_boost", bench_boost_trigger(conn, squad, gws)),
        ("free_hit", free_hit_trigger(conn, squad, gws)),
        ("wildcard", wildcard_trigger(conn, squad, next_gw)),
    ]
    for chip, reason in candidates:
        if reason and chip not in chips_used:
            return {"recommendation": {"chip": chip, "reason": reason}}
    return {"recommendation": None}
