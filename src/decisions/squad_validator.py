"""The law of the AI squad builder — deterministic, always enforced.

The AI proposes; this module guarantees the squad is legal (formation,
budget, 3-per-club, uniqueness) or explains exactly why not, so the runner can
retry with feedback. optimize_squad is the deterministic fallback that always
produces a legal squad.
"""
from collections import Counter

SLOTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
SLOT_NAMES = [f"{pos}{n}" for pos, n in SLOTS.items() for n in range(1, n + 1)]
MAX_BUDGET = 100.0
MAX_PER_CLUB = 3
EPS = 1e-9


def _by_id(pool):
    return {p["player_id"]: p for p in pool}


def validate_squad(picks, pool):
    problems = []
    players = _by_id(pool)
    if len(picks) != 15:
        return [f"expected 15 picks, got {len(picks)}"]
    used_slots, used_ids = [], []
    for pick in picks:
        pid = pick.get("player_id")
        slot = pick.get("slot")
        if slot not in SLOT_NAMES:
            problems.append(f"unknown slot {slot!r}")
        if pid in used_ids:
            problems.append(f"duplicate player {pid}")
        used_ids.append(pid)
        p = players.get(pid)
        if p is None:
            problems.append(f"unknown player {pid}")
            continue
        expected_pos = slot[:3] if slot else None
        if slot and expected_pos != p["position"]:
            problems.append(f"position mismatch: slot {slot} expects {expected_pos}, "
                            f"player {pid} is {p['position']}")
        if slot in used_slots:
            problems.append(f"slot {slot} used twice")
        used_slots.append(slot)
    total = sum(players[p["player_id"]]["price"] for p in picks
                if p["player_id"] in players)
    if total > MAX_BUDGET + EPS:
        problems.append(f"budget exceeded: {total:.1f}m > {MAX_BUDGET}m")
    clubs = Counter(players[p["player_id"]]["team_short"] for p in picks
                    if p["player_id"] in players)
    for club, n in clubs.items():
        if n > MAX_PER_CLUB:
            problems.append(f"club limit: {n} players from {club}; max is {MAX_PER_CLUB}")
    return problems


def optimize_squad(pool):
    """Greedy fill by xp_6gw desc (value as tiebreak); always legal when the
    pool can fill each slot. Realistic: best projected players, not cheapest."""
    def _key(p):
        return (p["xp_6gw"], p["value"] or 0)

    by_pos = {pos: sorted([p for p in pool if p["position"] == pos],
                          key=_key, reverse=True)
              for pos in SLOTS}
    picked, clubs, budget = [], Counter(), 0.0
    for pos, n in SLOTS.items():
        for slot_n in range(1, n + 1):
            chosen = None
            picked_ids = {x["player_id"] for x in picked}
            for p in by_pos[pos]:
                if p["player_id"] in picked_ids:
                    continue
                if clubs[p["team_short"]] >= MAX_PER_CLUB:
                    continue
                if budget + p["price"] > MAX_BUDGET + EPS:
                    continue
                chosen = p
                break
            if chosen is None:
                raise ValueError(f"pool cannot fill slot {pos}{slot_n}")
            picked.append({"player_id": chosen["player_id"], "slot": f"{pos}{slot_n}"})
            clubs[chosen["team_short"]] += 1
            budget += chosen["price"]
    return picked


def repair_budget(picks, pool):
    """Deterministic budget repair for an AI squad that is otherwise legal.

    Repeatedly swaps the most expensive pick for the best same-position pool
    alternative (not already in the squad) that fits the budget, until the squad
    is legal. Returns the repaired picks, or None when the pool cannot fix it.
    Preserves the AI's structure as much as possible.
    """
    players = {p["player_id"]: p for p in pool}
    by_pos = {}
    for p in pool:
        by_pos.setdefault(p["position"], []).append(p)
    picks = [dict(pk) for pk in picks]
    for _ in range(30):  # hard cap on swaps
        problems = validate_squad(picks, pool)
        if not problems:
            return picks
        budget_problems = [pr for pr in problems if "budget" in pr]
        if not budget_problems:
            return None  # non-budget violation: not our job
        in_squad = {pk["player_id"] for pk in picks}
        total = sum(players[pk["player_id"]]["price"] for pk in picks
                    if pk["player_id"] in players)
        # most expensive pick first
        expensive = sorted(
            [pk for pk in picks if pk["player_id"] in players],
            key=lambda pk: players[pk["player_id"]]["price"], reverse=True)
        swapped = False
        for pk in expensive:
            pos = players[pk["player_id"]]["position"]
            slot = pk["slot"]
            old_price = players[pk["player_id"]]["price"]
            # candidates: same position, unused, cheaper, and the swap fits the budget
            candidates = []
            for alt in by_pos.get(pos, []):
                if alt["player_id"] in in_squad:
                    continue
                if alt["price"] >= old_price:
                    continue
                if total - old_price + alt["price"] > MAX_BUDGET + EPS:
                    continue
                candidates.append(alt)
            if not candidates:
                continue
            # try candidates best-first (projected xP); a swap must stay legal
            for best in sorted(candidates,
                               key=lambda p: (p["xp_6gw"], p["value"] or 0), reverse=True):
                new_picks = [dict(x) for x in picks]
                for x in new_picks:
                    if x["player_id"] == pk["player_id"]:
                        x["player_id"] = best["player_id"]
                if not validate_squad(new_picks, pool):
                    picks = new_picks
                    swapped = True
                    break
            if swapped:
                break
        if not swapped:
            return None
    return picks
