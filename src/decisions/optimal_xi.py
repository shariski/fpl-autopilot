"""Optimal-XI selection (v0.26, captain scoring v0.28).

From the user's existing 15 picks, choose the 10 starters with the
highest next-GW xP that form a valid FPL formation (1 GK + 3-5 DEF +
3-5 MID + 1-3 FWD = 7 valid formations). No transfers, no budget
changes, no chip decisions (B3).

Captain/vice = top-2 among the chosen 10 starters by the captain
ranker's adjusted score (`captain._score` — xP + ceiling term, minus
the pre-season defensive penalty), NOT raw xP. This keeps the
armband consistent with the `captain` command: a clean-sheet-heavy
defender/GK no longer takes the armband over an attacker with a
modest xP edge (v0.28; live GW3 26/27 evidence). Bench = the 5
leftover players, ordered by xP desc, with the leftover GK anchoring
slot 12.

Reads `xp_v2` only — no new model. Returns ``None`` when the squad
cannot form any valid XI (fewer than 10 outfield players, no GK, etc.),
so the caller behaves as before.
"""
from itertools import product

from src.analytics import ratings
from src.analytics.xp import MODEL_VERSION_V2 as MODEL_VERSION
from src.decisions import captain as captain_mod
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
    if row is None:
        return 0.0
    return row["xp"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]


def _captain_score(conn, gw, element, pos, xp, pre_season):
    """Ranker-adjusted captain score for a starter (v0.28).

    Mirrors the `captain` command's score — xP + ceiling term
    (0.15 × goal involvement), minus the pre-season GKP/DEF penalty
    while the SF window is not majority-live. Constants come from
    `captain_mod` (single source of truth). This is the ONLY thing that
    differs from raw-xP ordering; XI/bench selection stays pure xP.
    """
    row = conn.execute(
        "SELECT xgoals, xassists FROM xp WHERE player_id=? AND gw=? AND model_version=?",
        (element, gw, MODEL_VERSION)).fetchone()
    if row is None:
        xgoals = xassists = 0.0
    elif isinstance(row, dict) or hasattr(row, "keys"):
        xgoals, xassists = row["xgoals"] or 0.0, row["xassists"] or 0.0
    else:
        xgoals, xassists = row[0] or 0.0, row[1] or 0.0
    return captain_mod._score({"position": pos, "xp": xp,
                               "xgoals": xgoals, "xassists": xassists},
                              pre_season=pre_season)


def can_form_xi(conn, squad):
    """Quick sanity check: does the squad have the position counts to form
    any valid XI? Resolves player positions via the players table."""
    ids = [p["element"] for p in squad]
    if not ids:
        return False
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        "SELECT position FROM players WHERE id IN (" + placeholders + ")",
        ids)
    counts = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for row in cur.fetchall():
        pos = row["position"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        counts[pos] += 1
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

    Accepts the raw `fetch_current_picks` shape — picks carry an integer
    `position` (slot 1-15), not a player-position string. Player
    positions are resolved via the players table.

    Returns a dict:
        {
          "xi": [element, ...]            # 11 starters (10 outfield + GK)
          "formation": "D-M-F",
          "captain_id": element,
          "vice_id": element,
          "bench": [element, ...]         # 4 bench, GK first (slot 12)
          "bench_slots": {element: slot}  # element -> 12..15
          "starter_slots": {element: slot} # element -> 1..11
          "total_xp": float,
        }
    or None when the squad can't form a valid XI.
    """
    gw = _next_gw(conn)
    if gw is None:
        return None
    # Resolve each player's position (DEF/MID/FWD/GKP) from the players
    # table. The squad's own `position` field is the squad slot (1-15),
    # not the player's role — don't trust it for selection logic.
    ids = [p["element"] for p in squad]
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        "SELECT id, position FROM players WHERE id IN (" + placeholders + ")",
        ids)
    pos_map = {}
    for row in cur.fetchall():
        eid = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        pos = row["position"] if isinstance(row, dict) or hasattr(row, "keys") else row[1]
        pos_map[eid] = pos
    # Drop players whose position isn't a valid FPL role (defensive —
    # shouldn't happen with real FPL data but possible in tests).
    squad_with_xp = []
    for p in squad:
        eid = p["element"]
        pos = pos_map.get(eid)
        if pos not in ("GKP", "DEF", "MID", "FWD"):
            continue
        squad_with_xp.append((eid, pos, _xp(conn, gw, eid)))
    if not squad_with_xp:
        return None
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
    # captain/vice = top-2 by the captain ranker's ADJUSTED score among
    # starters (v0.28). The captain was chosen by `_best_formation`
    # (top-xP GK, then top-xP at each position), so they always start;
    # same for vice.
    pre_season = ratings.sf_live_pairs(conn) < ratings.SF_LIVE_MIN
    def _score_starter(eid):
        pos = next(p for e, p, _ in squad_with_xp if e == eid)
        xp = next(x for e, _, x in squad_with_xp if e == eid)
        return _captain_score(conn, gw, eid, pos, xp, pre_season)
    sorted_starters = sorted(starter_ids, key=_score_starter, reverse=True)
    captain_id = sorted_starters[0]
    vice_id = sorted_starters[1]
    bench_with_meta = [(eid, pos, xp) for eid, pos, xp in squad_with_xp
                       if eid not in starter_set]
    bench_with_meta.sort(key=lambda x: (0 if x[1] == "GKP" else 1, -x[2]))
    bench_sorted = [eid for eid, _, _ in bench_with_meta]
    bench_slots = {eid: 12 + i for i, eid in enumerate(bench_sorted)}
    pos_to_slot_lo = {"GKP": 1, "DEF": 2, "MID": 2 + d, "FWD": 2 + d + m}
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
