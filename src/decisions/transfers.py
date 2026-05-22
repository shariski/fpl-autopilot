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
