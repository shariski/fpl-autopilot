import json
from statistics import median

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


def is_worth_hit(ep_delta, hit_cost):
    """True when the EP gain beats the (absolute) points hit.

    When `hit_cost` is 0 (free), this reduces to `ep_delta > 0`.
    """
    return ep_delta > abs(hit_cost)


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
