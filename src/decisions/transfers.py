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
