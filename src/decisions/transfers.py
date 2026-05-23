import json
from statistics import median

from src.decisions import confidence as confidence_mod

POSITIONS = ("GKP", "DEF", "MID", "FWD")
MAX_PER_CLUB = 3
HORIZON = 5
EMPTY_REASON = "No transfers worth making this GW."
_EPS = 1e-9


def xp_5gw_by_player(xp_rows, start_gw, horizon=HORIZON):
    """Sum each player's xp over GWs [start_gw, start_gw + horizon - 1].

    `xp_rows`: iterable of mappings with player_id, gw, xp (already filtered to model v1).
    Returns {player_id: summed_xp} rounded to 2dp. Players with no rows are absent;
    callers default a missing player to 0.0.
    """
    end_gw = start_gw + horizon - 1
    sums = {}
    for r in xp_rows:
        if start_gw <= r["gw"] <= end_gw:
            sums[r["player_id"]] = sums.get(r["player_id"], 0.0) + r["xp"]
    return {pid: round(v, 2) for pid, v in sums.items()}


def hit_cost(num_transfers, free_transfers=1):
    """Points cost of `num_transfers` given `free_transfers`, as a non-positive int.

    max(0, num_transfers - free_transfers) transfers cost 4 points each: e.g. 2 transfers
    with 1 FT -> -4. Returns 0 when free transfers cover them.
    """
    return -max(0, num_transfers - free_transfers) * 4


def is_worth_hit(ep_delta, points_hit):
    """True when the EP gain beats the (absolute) points hit.

    `points_hit` is a hit_cost(...) result. When it is 0 (free), this reduces to `ep_delta > 0`.
    """
    return ep_delta > abs(points_hit)


def _median_by_position(all_players):
    """Median xp_5gw per position across the whole market (not just the squad)."""
    meds = {}
    for pos in POSITIONS:
        vals = [p["xp_5gw"] for p in all_players if p["position"] == pos]
        meds[pos] = median(vals) if vals else 0.0
    return meds


def sell_candidates(squad_players, all_players):
    """Squad players worth selling: xp_5gw below the position's market median, or non-clear status."""
    meds = _median_by_position(all_players)
    return [p for p in squad_players
            if p["status"] != "a" or p["xp_5gw"] < meds.get(p["position"], 0.0)]


def _club_counts(players):
    counts = {}
    for p in players:
        counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
    return counts


def buy_candidates(sell, all_players, squad, bank):
    """Legal replacements for `sell`, ranked by xp_5gw desc.

    A buy must be: not already in the squad, the same position as `sell`, status 'a',
    affordable (price <= sell.price + bank), and keep <= 3 players per club after the swap.
    """
    squad_ids = {p["player_id"] for p in squad}
    counts = _club_counts(squad)
    budget = sell["price"] + bank
    out = []
    for p in all_players:
        if p["player_id"] in squad_ids:
            continue
        if p["position"] != sell["position"] or p["status"] != "a":
            continue
        if p["price"] > budget + _EPS:
            continue
        after = counts.get(p["team_id"], 0) - (1 if sell["team_id"] == p["team_id"] else 0) + 1
        if after > MAX_PER_CLUB:
            continue
        out.append(p)
    out.sort(key=lambda x: x["xp_5gw"], reverse=True)
    return out


def suggest_transfers(squad_players, all_players, bank, top_n=3):
    """Top `top_n` sell->buy pairs by EP delta over the 5-GW horizon.

    For each sell candidate, take its best legal buy; keep only positive EP deltas; sort all
    pairs by delta desc; return the top `top_n`. v1 assumes a single free transfer, so every
    suggested transfer is free (hit_cost 0). `out`/`in` carry the full player dicts so callers
    can inspect/apply the swap; the reader projects them to the API shape.
    """
    pairs = []
    for sell in sell_candidates(squad_players, all_players):
        buys = buy_candidates(sell, all_players, squad_players, bank)
        if not buys:
            continue
        buy = buys[0]
        ep_delta = buy["xp_5gw"] - sell["xp_5gw"]
        if ep_delta <= 0:
            continue
        pairs.append({"out": sell, "in": buy,
                      "ep_delta_5gw": round(ep_delta, 2), "hit_cost": 0})
    pairs.sort(key=lambda pr: pr["ep_delta_5gw"], reverse=True)
    return pairs[:top_n]


def _next_gw(conn):
    row = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row else None


def _latest_squad(conn):
    """Latest my_team snapshot -> (element_ids, bank), or None when there is no snapshot."""
    row = conn.execute("SELECT picks_json, bank FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if row is None:
        return None
    ids = [pick["element"] for pick in json.loads(row["picks_json"])]
    bank = row["bank"] if row["bank"] is not None else 0.0
    return ids, bank


def get_transfer_suggestions(conn):
    """Reader: build the /api/transfers payload from current DB state (Analytics output + squad).

    Returns {"suggestions": [...up to 3...], "empty_reason": str | None}. `confidence` is out of
    scope this slice and is returned as null. No persistence, no execution (Phase 1).
    """
    next_gw = _next_gw(conn)
    squad = _latest_squad(conn)
    if next_gw is None or squad is None:
        return {"suggestions": [], "empty_reason": EMPTY_REASON}
    squad_ids, bank = squad

    xp_rows = conn.execute(
        "SELECT player_id, gw, xp FROM xp WHERE model_version='v1' AND gw BETWEEN ? AND ?",
        (next_gw, next_gw + HORIZON - 1)).fetchall()
    xp5 = xp_5gw_by_player(xp_rows, next_gw)

    all_players = [
        {"player_id": r["id"], "web_name": r["web_name"], "position": r["position"],
         "team_id": r["team_id"], "price": r["price"], "status": r["status"],
         "xp_5gw": xp5.get(r["id"], 0.0)}
        for r in conn.execute(
            "SELECT id, web_name, position, team_id, price, status FROM players")
    ]
    squad_set = set(squad_ids)
    squad_players = [p for p in all_players if p["player_id"] in squad_set]

    pairs = suggest_transfers(squad_players, all_players, bank)
    staleness = confidence_mod.hours_since_refresh(conn)
    suggestions = []
    for i, pr in enumerate(pairs):
        gap = pr["ep_delta_5gw"] - pairs[i + 1]["ep_delta_5gw"] if i + 1 < len(pairs) else None
        conf = confidence_mod.score(staleness_hours=staleness,
                                    statuses=[pr["in"]["status"], pr["out"]["status"]], gap=gap)
        suggestions.append(
            {"out": {k: pr["out"][k] for k in ("player_id", "web_name", "price")},
             "in":  {k: pr["in"][k] for k in ("player_id", "web_name", "price")},
             "ep_delta_5gw": pr["ep_delta_5gw"], "hit_cost": pr["hit_cost"], "confidence": conf})
    return {"suggestions": suggestions, "empty_reason": None if suggestions else EMPTY_REASON}
