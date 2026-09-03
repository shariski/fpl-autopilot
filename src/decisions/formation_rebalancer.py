"""Cohort-formation rebalance (v0.25).

Pure slot-swap layer: given the user's current 15 picks + the upcoming GW,
look at the top-100 leaders cohort's modal formation for that GW and, if
it differs from the user's current XI shape and the squad can fill it,
swap the lowest-xP starter of the surplus position with the highest-xP
bench player of the deficit position.

No transfers, no budget changes, no chip decisions (B3). Captain/vice
stay with their player id. Fallback (cohort too small, modal tie, illegal
shape, missing xP) returns ``None`` — caller behaves as before.

Reads existing analytics output (xp v2) — does not compute anything new.
"""
from collections import Counter

from src.analytics.xp import MODEL_VERSION_V2 as MODEL_VERSION
from src.decisions.transfers import _next_gw


COHORT_FORMATION_MIN = 20     # below this, modal signal is noise
MODAL_TIE_MARGIN = 1          # modal within 1 vote of runner-up => ambiguous
POSITION_ORDER = ("GKP", "DEF", "MID", "FWD")
FORMATION_LEN = 3             # DEF-MID-FWD (GK is fixed at 1)

XI_SLOTS = set(range(1, 12))  # slots 1..11 = starting XI
SWAP_BENCH_SLOTS = {13, 14, 15}  # slot 12 = bench GK anchor, never touched


def _cohort_formation(conn, gw):
    """Return (cohort_size, modal_formation_or_None) for the upcoming GW.

    Formation stored as 'D-M-F' (e.g. '4-4-2'); modal = the formation with
    the most votes. Returns (0, None) when no picks are stored for this GW.
    """
    rows = conn.execute(
        "SELECT formation, COUNT(*) AS c FROM leader_gw_picks "
        "WHERE gw=? AND formation IS NOT NULL GROUP BY formation",
        (gw,)).fetchall()
    cohort = conn.execute(
        "SELECT COUNT(*) AS n FROM leader_gw_picks WHERE gw=?",
        (gw,)).fetchone()["n"]
    if cohort < COHORT_FORMATION_MIN or not rows:
        return cohort, None
    rows.sort(key=lambda r: -r["c"])
    modal, top_votes = rows[0]["formation"], rows[0]["c"]
    if len(rows) > 1 and (top_votes - rows[1]["c"]) <= MODAL_TIE_MARGIN:
        return cohort, None  # ambiguous
    return cohort, modal


def _parse_formation(formation):
    """'4-4-2' -> {'DEF': 4, 'MID': 4, 'FWD': 2}. GK is always 1."""
    parts = formation.split("-")
    if len(parts) != FORMATION_LEN:
        return None
    try:
        d, m, f = (int(x) for x in parts)
    except ValueError:
        return None
    if min(d, m, f) < 0:
        return None
    return {"DEF": d, "MID": m, "FWD": f}


def _current_shape(picks, meta):
    """Position counts among the starting XI (slots 1..11)."""
    c = Counter()
    for p in picks:
        if p["position"] in XI_SLOTS:
            pos = meta.get(p["element"], {}).get("position")
            if pos in ("DEF", "MID", "FWD"):
                c[pos] += 1
    return dict(c)


def _xp(conn, gw, element):
    row = conn.execute(
        "SELECT xp FROM xp WHERE player_id=? AND gw=? AND model_version=?",
        (element, gw, MODEL_VERSION)).fetchone()
    return row["xp"] if row else 0.0


def _pick_role(picks, element):
    for p in picks:
        if p["element"] == element:
            return p["position"]
    return None


def rebalance(conn, picks, *, captain_id, vice_id):
    """Return a position-override dict (element -> new slot) or None.

    Same shape as `executor.build_lineup_payload(pos_override=...)`. None
    means: no swap, caller behaves as before.
    """
    gw = _next_gw(conn)
    if gw is None:
        return None
    cohort, modal = _cohort_formation(conn, gw)
    if modal is None:
        return None
    target = _parse_formation(modal)
    if target is None:
        return None
    ids = [p["element"] for p in picks]
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT p.id AS element, p.position FROM players p WHERE p.id IN ("
        + placeholders + ")", ids).fetchall()
    meta = {r["element"]: {"position": r["position"]} for r in rows}
    current = _current_shape(picks, meta)
    if current == target:
        return None  # already aligned
    diff = {pos: target.get(pos, 0) - current.get(pos, 0)
            for pos in ("DEF", "MID", "FWD")}
    # need at least one surplus (negative diff) and one deficit (positive diff),
    # and the squad must be able to fill the target shape in total
    if not (min(diff.values()) < 0 and max(diff.values()) > 0):
        return None
    # bail if the squad doesn't actually have the position counts needed
    squad_counts = Counter(meta[e]["position"] for e in ids
                           if meta.get(e, {}).get("position") in POSITION_ORDER)
    if any(squad_counts.get(pos, 0) < target.get(pos, 0) for pos in ("DEF", "MID", "FWD")):
        return None
    surplus = [pos for pos, d in diff.items() if d < 0]
    deficit = [pos for pos, d in diff.items() if d > 0]
    # find lowest-xP starter of any surplus position
    starter_candidates = [(p["element"], _xp(conn, gw, p["element"]))
                          for p in picks
                          if p["position"] in XI_SLOTS
                          and meta.get(p["element"], {}).get("position") in surplus]
    if not starter_candidates:
        return None
    out_id, _ = min(starter_candidates, key=lambda x: x[1])
    # find highest-xP bench player of any deficit position.
    # Slot 12 is the bench GK anchor — never swap a starter into it or
    # pull from it (FPL auto-subs from slot 12 first; rearranging it
    # changes auto-sub semantics, which belongs to rank_bench, not here).
    bench_candidates = [(p["element"], _xp(conn, gw, p["element"]))
                        for p in picks
                        if p["position"] in SWAP_BENCH_SLOTS
                        and meta.get(p["element"], {}).get("position") in deficit]
    if not bench_candidates:
        return None
    in_id, _ = max(bench_candidates, key=lambda x: x[1])
    # captain/vice must stay in XI — guard against both directions
    if out_id in (captain_id, vice_id) or in_id in (captain_id, vice_id):
        # if the swap would bench the captain or vice, fall back rather
        # than re-assign flags (re-assignment belongs to the ranker, B4)
        return None
    out_slot = _pick_role(picks, out_id)
    in_slot = _pick_role(picks, in_id)
    if out_slot is None or in_slot is None:
        return None
    return {out_id: in_slot, in_id: out_slot}


def formation_info(conn, picks):
    """Diagnostic info for printing in the dry-run banner.

    Returns {'cohort': int, 'modal': str|None, 'current': str, 'swap': [(out_id, in_id)]}.
    """
    gw = _next_gw(conn)
    cohort, modal = (_cohort_formation(conn, gw) if gw is not None
                     else (0, None))
    cur = Counter()
    ids = [p["element"] for p in picks]
    if ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            "SELECT p.id AS element, p.position FROM players p WHERE p.id IN ("
            + placeholders + ")", ids).fetchall()
        meta = {r["element"]: r["position"] for r in rows}
        for p in picks:
            if p["position"] in XI_SLOTS:
                pos = meta.get(p["element"])
                if pos in ("DEF", "MID", "FWD"):
                    cur[pos] += 1
    current = f"{cur['DEF']}-{cur['MID']}-{cur['FWD']}"
    return {"cohort": cohort, "modal": modal, "current": current, "gw": gw}
