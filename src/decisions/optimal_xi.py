"""Optimal-XI selection (v0.26).

From the user's existing 15 picks, choose the 10 starters with the
highest next-GW xP that form a valid FPL formation (1 GK + 3-5 DEF +
3-5 MID + 1-3 FWD = 7 valid formations). No transfers, no budget
changes, no chip decisions (B3).

Captain/vice = top-2 xP among the chosen 10 starters. Bench = the 5
leftover players, ordered by xP desc, with the leftover GK anchoring
slot 12.

Reads `xp_v2` only — no new model. Returns ``None`` when the squad
cannot form any valid XI (fewer than 10 outfield players, no GK, etc.),
so the caller behaves as before.
"""
from itertools import product

from src.analytics.xp import MODEL_VERSION_V2 as MODEL_VERSION
from src.decisions.transfers import _next_gw


_VALID_DEF = (3, 4, 5)
_VALID_MID = (3, 4, 5)
_VALID_FWD = (1, 2, 3)
# Valid formations: DEF + MID + FWD must sum to 10 outfield slots
# (the GK is fixed at 1). 3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-3-2, 5-4-1.
_VALID_FORMATIONS = [(d, m, f) for d, m, f in product(_VALID_DEF, _VALID_MID, _VALID_FWD)
                     if d + m + f == 10]


def _xp(conn, gw, element):
    row = conn.execute(
        "SELECT xp FROM xp WHERE player_id=? AND gw=? AND model_version=?",
        (element, gw, MODEL_VERSION)).fetchone()
    return row["xp"] if row else 0.0


def can_form_xi(squad):
    """Quick sanity check: does the squad have the position counts to form
    any valid XI? Used by the caller to short-circuit before scoring."""
    counts = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for p in squad:
        counts[p["position"]] += 1
    return (counts["GKP"] >= 1 and counts["DEF"] >= 3 and counts["MID"] >= 3
            and counts["FWD"] >= 1)


def _best_formation(squad_with_xp):
    """Pick the formation (D,M,F) whose top-D/top-M/top-F + GK has the
    highest total xP. squad_with_xp = [(element, position, xp)].

    Returns (formation_tuple, total_xp) or None when no formation fits.
    """
    groups = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for element, pos, xp in squad_with_xp:
        groups[pos].append((element, xp))
    for pos in groups:
        groups[pos].sort(key=lambda x: -x[1])
    if not groups["GKP"]:
        return None
    best = None
    for d, m, f in _VALID_FORMATIONS:
        if len(groups["DEF"]) < d or len(groups["MID"]) < m or len(groups["FWD"]) < f:
            continue
        starters = [groups["GKP"][0]] + groups["DEF"][:d] + groups["MID"][:m] + groups["FWD"][:f]
        total = sum(xp for _, xp in starters)
        if best is None or total > best[1]:
            best = ((d, m, f), total)
    return best


def select(conn, squad):
    """Pick the optimal XI from the squad.

    Returns a dict:
        {
          "xi": [element, ...]            # 10 starters, ordered GK + DEF + MID + FWD
          "formation": "D-M-F",
          "captain_id": element,
          "vice_id": element,
          "bench": [element, ...]         # 5 bench, GK first (will go to slot 12)
          "bench_slots": {element: slot}  # element -> 12..15
          "starter_slots": {element: slot} # element -> 1..11
          "total_xp": float,
        }
    or None when the squad can't form a valid XI.
    """
    gw = _next_gw(conn)
    if gw is None:
        return None
    squad_with_xp = [(p["element"], p["position"], _xp(conn, gw, p["element"]))
                     for p in squad]
    best = _best_formation(squad_with_xp)
    if best is None:
        return None
    (d, m, f), total = best
    groups = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for element, pos, xp in squad_with_xp:
        groups[pos].append((element, xp))
    for pos in groups:
        groups[pos].sort(key=lambda x: -x[1])
    starters = ([groups["GKP"][0]] + groups["DEF"][:d]
                + groups["MID"][:m] + groups["FWD"][:f])
    starter_ids = [eid for eid, _ in starters]
    starter_set = set(starter_ids)
    # captain/vice = top-2 xP among starters (the captain must start; if
    # they wouldn't make the top-XI, they get force-included below — but
    # they were chosen by `_best_formation` so they're always starters).
    captain_id = starter_ids[0]
    # vice_id = the second-highest xP starter that isn't the captain
    sorted_by_xp = sorted(starters, key=lambda x: -x[1])
    vice_id = next(eid for eid, _ in sorted_by_xp if eid != captain_id)
    # bench = the 5 leftover players, ordered GK first then by xP desc.
    # GK first so the sub-GK anchors slot 12 (FPL's auto-sub convention).
    bench_with_meta = [(eid, pos, xp) for eid, pos, xp in squad_with_xp
                       if eid not in starter_set]
    bench_with_meta.sort(key=lambda x: (0 if x[1] == "GKP" else 1, -x[2]))
    bench_sorted = [eid for eid, _, _ in bench_with_meta]
    bench_slots = {eid: 12 + i for i, eid in enumerate(bench_sorted)}
    # Starter slots: GK=1, DEF=2..1+d, MID=2+d..1+d+m, FWD=2+d+m..1+d+m+f.
    pos_to_slot_lo = {"GKP": 1, "DEF": 2, "MID": 2 + d, "FWD": 2 + d + m}
    pos_to_count = {"GKP": 1, "DEF": d, "MID": m, "FWD": f}
    starter_slots = {}
    counts = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for eid in starter_ids:
        pos = next(p for e, p, _ in squad_with_xp if e == eid)
        starter_slots[eid] = pos_to_slot_lo[pos] + counts[pos]
        counts[pos] += 1
    return {
        "xi": starter_ids,
        "formation": f"{d}-{m}-{f}",
        "captain_id": captain_id,
        "vice_id": vice_id,
        "bench": bench_sorted,
        "bench_slots": bench_slots,
        "starter_slots": starter_slots,
        "total_xp": total,
    }
