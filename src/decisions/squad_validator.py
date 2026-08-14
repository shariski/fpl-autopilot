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

# Speculative input signal (decision-engine.md §S-B): the LLM labels spike/drop
# levels; the optimizer adds/subtracts these fixed bonuses to xP. Deterministic
# constants — changing them needs a decision-engine.md entry (B4).
SPIKE_BONUS = {"high": 1.5, "medium": 0.75}
DROP_BONUS = {"high": -1.5, "medium": -0.75}


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


def optimize_squad(pool, bonus_map=None):
    """Greedy fill by (xp_6gw + speculation bonus) desc, budget-aware: a pick is
    only taken when the remaining budget still covers the cheapest legal option
    for every slot yet to fill. bonus_map: player_id -> xp adjustment from the
    AI speculation layer (SPIKE_BONUS/DROP_BONUS); None/{} = no speculation.
    Always legal when the pool can fill each slot."""
    bonus_map = bonus_map or {}

    def _key(p):
        return (p["xp_6gw"] + bonus_map.get(p["player_id"], 0.0), p["value"] or 0)

    by_pos = {pos: sorted([p for p in pool if p["position"] == pos],
                          key=_key, reverse=True)
              for pos in SLOTS}

    def _cheapest_unused(pos, picked_ids):
        cheapest = None
        for p in by_pos[pos]:
            if p["player_id"] in picked_ids:
                continue
            if cheapest is None or p["price"] < cheapest:
                cheapest = p["price"]
        return cheapest

    order = [(pos, n) for pos, n in SLOTS.items()]
    remaining_per_pos = dict(SLOTS)
    picked, clubs, budget = [], Counter(), 0.0
    for pos, n in order:
        for slot_n in range(1, n + 1):
            chosen = None
            picked_ids = {x["player_id"] for x in picked}
            # minimum budget to reserve for every slot that comes after this one
            reserve = 0.0
            for pos2, n2 in order:
                need = remaining_per_pos[pos2] - (1 if pos2 == pos else 0)
                if need > 0:
                    c = _cheapest_unused(pos2, picked_ids)
                    reserve += (c if c is not None else 4.0) * need
            for p in by_pos[pos]:
                if p["player_id"] in picked_ids:
                    continue
                if clubs[p["team_short"]] >= MAX_PER_CLUB:
                    continue
                if budget + p["price"] > MAX_BUDGET + EPS:
                    continue
                if budget + p["price"] + reserve > MAX_BUDGET + EPS:
                    continue  # would strand a later slot
                chosen = p
                break
            if chosen is None:
                raise ValueError(f"pool cannot fill slot {pos}{slot_n}")
            picked.append({"player_id": chosen["player_id"], "slot": f"{pos}{slot_n}"})
            clubs[chosen["team_short"]] += 1
            budget += chosen["price"]
            remaining_per_pos[pos] -= 1
    return picked


def optimize_squad(pool, bonus_map=None):
    """Greedy fill by (xp_6gw + speculation bonus) desc, budget-aware: a pick is
    only taken when the remaining budget still covers the cheapest legal option
    for every slot yet to fill. bonus_map: player_id -> xp adjustment from the
    AI speculation layer (SPIKE_BONUS/DROP_BONUS); None/{} = no speculation.
    Always legal when the pool can fill each slot."""
    bonus_map = bonus_map or {}

    def _key(p):
        return (p["xp_6gw"] + bonus_map.get(p["player_id"], 0.0), p["value"] or 0)

    by_pos = {pos: sorted([p for p in pool if p["position"] == pos],
                          key=_key, reverse=True)
              for pos in SLOTS}

    def _cheapest_unused(pos, picked_ids):
        cheapest = None
        for p in by_pos[pos]:
            if p["player_id"] in picked_ids:
                continue
            if cheapest is None or p["price"] < cheapest:
                cheapest = p["price"]
        return cheapest

    order = [(pos, n) for pos, n in SLOTS.items()]
    remaining_per_pos = dict(SLOTS)
    picked, clubs, budget = [], Counter(), 0.0
    for pos, n in order:
        for slot_n in range(1, n + 1):
            chosen = None
            picked_ids = {x["player_id"] for x in picked}
            # minimum budget to reserve for every slot that comes after this one
            reserve = 0.0
            for pos2, n2 in order:
                need = remaining_per_pos[pos2] - (1 if pos2 == pos else 0)
                if need > 0:
                    c = _cheapest_unused(pos2, picked_ids)
                    reserve += (c if c is not None else 4.0) * need
            for p in by_pos[pos]:
                if p["player_id"] in picked_ids:
                    continue
                if clubs[p["team_short"]] >= MAX_PER_CLUB:
                    continue
                if budget + p["price"] > MAX_BUDGET + EPS:
                    continue
                if budget + p["price"] + reserve > MAX_BUDGET + EPS:
                    continue  # would strand a later slot
                chosen = p
                break
            if chosen is None:
                raise ValueError(f"pool cannot fill slot {pos}{slot_n}")
            picked.append({"player_id": chosen["player_id"], "slot": f"{pos}{slot_n}"})
            clubs[chosen["team_short"]] += 1
            budget += chosen["price"]
            remaining_per_pos[pos] -= 1
    return picked


def normalize_squad(picks, pool):
    """Deterministic normalization of an AI proposal (observed 2026-08-14: the
    LLM invented DEF6/DEF7, mismatched positions, breached club limit and
    budget). Fixes slot names by position order, drops surplus per position,
    fills gaps from the pool, enforces the club limit, then budget-repairs.
    Returns a legal squad or None if unfixable."""
    players = {p["player_id"]: p for p in pool}
    by_pos_pool = {pos: sorted([p for p in pool if p["position"] == pos],
                               key=lambda p: (p["xp_6gw"], p["value"] or 0), reverse=True)
                   for pos in SLOTS}
    per_pos = {pos: [] for pos in SLOTS}
    for pk in picks:
        p = players.get(pk.get("player_id"))
        if p is not None:
            per_pos[p["position"]].append(pk)
    out = []
    used = set()
    for pos, n in SLOTS.items():
        group = per_pos[pos]
        if len(group) > n:
            group = sorted(group, key=lambda pk: players[pk["player_id"]]["xp_6gw"],
                           reverse=True)[:n]
        for p in by_pos_pool[pos]:
            if len(group) >= n:
                break
            if p["player_id"] in used:
                continue
            group.append({"player_id": p["player_id"], "reason": ""})
            used.add(p["player_id"])
        for i, pk in enumerate(group[:n]):
            out.append({"player_id": pk["player_id"], "slot": f"{pos}{i + 1}",
                        "reason": pk.get("reason", "")})
    if len(out) != 15:
        return None
    # club limit: for clubs with > 3, swap surplus (lowest xp first) with the
    # best same-position pool player not used, whose club has room
    clubs = Counter(players[pk["player_id"]]["team_short"] for pk in out)
    for _ in range(20):
        over = [club for club, c in clubs.items() if c > MAX_PER_CLUB]
        if not over:
            break
        club = over[0]
        surplus = sorted([pk for pk in out if players[pk["player_id"]]["team_short"] == club],
                         key=lambda pk: players[pk["player_id"]]["xp_6gw"])
        swapped = False
        for pk in surplus:
            pos = players[pk["player_id"]]["position"]
            for alt in by_pos_pool[pos]:
                if alt["player_id"] in {x["player_id"] for x in out}:
                    continue
                if clubs[alt["team_short"]] >= MAX_PER_CLUB:
                    continue
                out = [dict(x) for x in out]
                for x in out:
                    if x["player_id"] == pk["player_id"]:
                        x["player_id"] = alt["player_id"]
                        x["reason"] = ""  # reason belongs to the original player
                clubs[club] -= 1
                clubs[alt["team_short"]] += 1
                swapped = True
                break
            if swapped:
                break
        if not swapped:
            return None
    # budget is the last step — repair (which validates internally)
    return repair_budget(out, pool)


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
                        x["reason"] = ""  # reason belongs to the original player
                if not validate_squad(new_picks, pool):
                    picks = new_picks
                    swapped = True
                    break
            if swapped:
                break
        if not swapped:
            return None
    return picks
