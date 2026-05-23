import json
import random
from collections import Counter

from src.decisions import transfers
from src.decisions.transfers import HORIZON, MAX_PER_CLUB  # used by the test helpers below


def _p(pid, pos, team, price, status, xp5):
    """Build a player dict for the pure-function tests."""
    return {"player_id": pid, "web_name": f"P{pid}", "position": pos,
            "team_id": team, "price": price, "status": status, "xp_5gw": xp5}


def _pick_valid_squad(market, rng):
    """Greedily pick a legal 15-man squad (2 GKP, 5 DEF, 5 MID, 3 FWD, <=3/club) from `market`."""
    need = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    chosen, chosen_ids, club = [], set(), {}
    pool = market[:]
    rng.shuffle(pool)
    for pos, n in need.items():
        got = 0
        for p in pool:
            if got == n:
                break
            if p["position"] != pos or p["player_id"] in chosen_ids:
                continue
            if club.get(p["team_id"], 0) >= MAX_PER_CLUB:
                continue
            chosen.append(p)
            chosen_ids.add(p["player_id"])
            club[p["team_id"]] = club.get(p["team_id"], 0) + 1
            got += 1
        assert got == n, f"market too small for {pos}"
    return chosen


def _random_market_and_squad(seed):
    """Deterministic random market (160 players, 20 clubs) + a legal squad + bank."""
    rng = random.Random(seed)
    market, pid = [], 1
    for pos, count in (("GKP", 20), ("DEF", 40), ("MID", 40), ("FWD", 20)):
        for _ in range(count):
            market.append(_p(pid, pos, rng.randint(1, 20),
                             round(rng.uniform(4.0, 13.0), 1),
                             rng.choice(["a", "a", "a", "i", "d"]),
                             round(rng.uniform(0.0, 40.0), 2)))
            pid += 1
    squad = _pick_valid_squad(market, rng)
    return market, squad, round(rng.uniform(0.0, 5.0), 1)


def _seed_db(db, players, squad_ids, bank, next_gw=1):
    """Seed gameweeks/players/xp/my_team for the reader integration tests.
    `players` are player dicts ({id, web_name, position, team_id, price, status, xp5});
    each player's xp5 is spread evenly across the 5-GW window."""
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (?, 'GW', 0)", (next_gw,))
    for p in players:
        db.execute(
            "INSERT INTO players (id, web_name, team_id, position, price, status) VALUES (?,?,?,?,?,?)",
            (p["id"], p["web_name"], p["team_id"], p["position"], p["price"], p["status"]))
        for g in range(next_gw, next_gw + HORIZON):
            db.execute(
                "INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs,"
                " computed_at) VALUES (?,?, 'v1', ?, 0, 0, 0, 0, 't')",
                (p["id"], g, p["xp5"] / HORIZON))
    picks = json.dumps([{"element": pid, "position": i + 1, "multiplier": 1,
                         "is_captain": False, "is_vice_captain": False}
                        for i, pid in enumerate(squad_ids)])
    db.execute("INSERT INTO my_team (gw, picks_json, bank, snapshot_at) VALUES (?,?,?,'t')",
               (next_gw, picks, bank))
    db.commit()


# ── Task 1: xp_5gw_by_player ──────────────────────────────────────────────────

def test_xp_5gw_sums_five_gws():
    rows = [{"player_id": 1, "gw": g, "xp": 2.0} for g in range(10, 15)]  # gw 10..14 -> 5 rows
    rows.append({"player_id": 1, "gw": 15, "xp": 99.0})                   # outside the window
    rows.append({"player_id": 2, "gw": 10, "xp": 1.0})
    out = transfers.xp_5gw_by_player(rows, 10)
    assert out[1] == 10.0   # 5 * 2.0; gw 15 excluded
    assert out[2] == 1.0
    assert 99.0 not in out.values()


# ── Task 2: hit_cost + is_worth_hit ───────────────────────────────────────────

def test_hit_cost_thresholds():
    assert transfers.hit_cost(1, 1) == 0
    assert transfers.hit_cost(2, 1) == -4
    assert transfers.hit_cost(3, 1) == -8
    assert transfers.hit_cost(2, 2) == 0          # 2 FT covers 2 transfers
    # is_worth_hit: ep_delta must exceed the absolute hit
    assert transfers.is_worth_hit(5.0, -4) is True    # 5 > 4
    assert transfers.is_worth_hit(3.0, -4) is False   # 3 < 4
    assert transfers.is_worth_hit(0.1, 0) is True     # free transfer, any positive gain
    assert transfers.is_worth_hit(0.0, 0) is False    # free transfer, no gain


# ── Task 3: sell_candidates ───────────────────────────────────────────────────

def test_sell_candidate_below_median_or_flagged():
    # FWD market xp_5gw = [10, 20, 30, 25] -> median 22.5
    market = [
        _p(1, "FWD", 1, 8.0, "a", 10.0),   # below median -> sell
        _p(2, "FWD", 2, 8.0, "a", 20.0),
        _p(3, "FWD", 3, 8.0, "a", 30.0),   # above median, available -> keep
        _p(4, "FWD", 4, 8.0, "i", 25.0),   # flagged -> sell regardless of xp
    ]
    squad = [market[0], market[2], market[3]]
    sell_ids = {p["player_id"] for p in transfers.sell_candidates(squad, market)}
    assert 1 in sell_ids     # below median
    assert 4 in sell_ids     # flagged status
    assert 3 not in sell_ids # above median and available


# ── Task 4: buy_candidates ────────────────────────────────────────────────────

def test_buy_respects_budget():
    sell = _p(1, "MID", 1, 7.0, "a", 10.0)
    market = [
        sell,
        _p(2, "MID", 2, 7.5, "a", 20.0),   # 7.5 <= 7.0 + 1.0 -> allowed
        _p(3, "MID", 3, 9.0, "a", 30.0),   # 9.0 > 8.0 -> excluded
    ]
    ids = {p["player_id"] for p in transfers.buy_candidates(sell, market, [sell], bank=1.0)}
    assert 2 in ids
    assert 3 not in ids


def test_buy_respects_3_per_club():
    squad = [
        _p(1, "DEF", 5, 5.0, "a", 10.0),
        _p(2, "DEF", 5, 5.0, "a", 10.0),
        _p(3, "DEF", 5, 5.0, "a", 10.0),   # club 5 already at 3
        _p(4, "DEF", 9, 5.0, "a", 5.0),    # the sell (different club)
    ]
    market = squad + [
        _p(10, "DEF", 5, 5.0, "a", 40.0),  # would be a 4th from club 5
        _p(11, "DEF", 7, 5.0, "a", 35.0),  # club 7 -> fine
    ]
    ids = {p["player_id"] for p in transfers.buy_candidates(squad[3], market, squad, bank=2.0)}
    assert 10 not in ids   # selling a club-9 player does not free a club-5 slot
    assert 11 in ids
    # but selling a club-5 player DOES free a slot: 3 - 1 + 1 = 3 is legal
    ids2 = {p["player_id"] for p in transfers.buy_candidates(squad[0], market, squad, bank=2.0)}
    assert 10 in ids2


def test_buy_respects_budget_property():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        for sell in squad:
            for buy in transfers.buy_candidates(sell, market, squad, bank):
                assert buy["price"] <= sell["price"] + bank + 1e-9
                assert buy["position"] == sell["position"]
                assert buy["status"] == "a"


def test_buy_respects_3_per_club_property():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        squad_ids = {p["player_id"] for p in squad}
        for sell in squad:
            for buy in transfers.buy_candidates(sell, market, squad, bank):
                assert buy["player_id"] not in squad_ids
                new_squad = [p for p in squad if p["player_id"] != sell["player_id"]] + [buy]
                counts = Counter(p["team_id"] for p in new_squad)
                assert max(counts.values()) <= MAX_PER_CLUB


# ── Task 5: suggest_transfers ─────────────────────────────────────────────────

def test_suggest_orders_by_ep_delta_and_caps_at_three():
    # All four squad FWDs are below the FWD market median (8.45) -> all are sell candidates.
    # Every sell's best legal buy is the same top FWD (P11, xp 30, affordable, distinct club),
    # so pairs rank by sell.xp_5gw ascending (lower-xp sell -> bigger delta). Top 3 of 4 kept.
    squad = [
        _p(1, "FWD", 1, 8.0, "a", 2.0),   # delta 28.0 -> rank 1
        _p(2, "FWD", 2, 8.0, "a", 3.0),   # delta 27.0 -> rank 2
        _p(3, "FWD", 3, 8.0, "a", 4.0),   # delta 26.0 -> rank 3
        _p(4, "FWD", 4, 8.0, "a", 6.9),   # delta 23.1 -> 4th, dropped by the top-3 cap
    ]
    buys = [
        _p(11, "FWD", 11, 8.0, "a", 30.0),   # the single best buy for every sell
        _p(12, "FWD", 12, 8.0, "a", 25.0),
        _p(13, "FWD", 13, 8.0, "a", 20.0),
        _p(14, "FWD", 14, 8.0, "a", 10.0),
    ]
    market = squad + buys
    pairs = transfers.suggest_transfers(squad, market, bank=0.0)
    assert len(pairs) == 3                                       # capped at top 3
    deltas = [pr["ep_delta_5gw"] for pr in pairs]
    assert deltas == sorted(deltas, reverse=True)                # descending by delta
    assert pairs[0]["out"]["player_id"] == 1 and pairs[0]["in"]["player_id"] == 11
    assert {pr["out"]["player_id"] for pr in pairs} == {1, 2, 3} # P4 (smallest delta) dropped
    assert all(pr["in"]["player_id"] == 11 for pr in pairs)      # shared best buy
    assert all(pr["hit_cost"] == 0 for pr in pairs)              # v1 single free transfer


def test_empty_reason_when_no_positive_delta():
    # squad players are the entire market for their position, identical xp, available -> no sells
    squad = [_p(i, "MID", i, 6.0, "a", 10.0) for i in range(1, 4)]
    assert transfers.suggest_transfers(squad, squad, bank=2.0) == []


def test_property_suggestions_leave_valid_squad():
    for seed in range(60):
        market, squad, bank = _random_market_and_squad(seed)
        squad_ids = {p["player_id"] for p in squad}
        before_pos = Counter(p["position"] for p in squad)
        pairs = transfers.suggest_transfers(squad, market, bank)
        assert len(pairs) <= 3
        for pr in pairs:
            sell, buy = pr["out"], pr["in"]
            assert sell["player_id"] in squad_ids
            assert buy["player_id"] not in squad_ids
            assert sell["position"] == buy["position"]        # same-position swap
            assert pr["ep_delta_5gw"] > 0                      # only positive deltas
            new_squad = [p for p in squad if p["player_id"] != sell["player_id"]] + [buy]
            assert len(new_squad) == 15                        # 15-man squad preserved
            assert Counter(p["position"] for p in new_squad) == before_pos
            assert max(Counter(p["team_id"] for p in new_squad).values()) <= MAX_PER_CLUB
            assert bank - (buy["price"] - sell["price"]) >= -1e-9   # within budget


# ── Task 6: get_transfer_suggestions (reader) ─────────────────────────────────

def test_get_transfer_suggestions_integration(db):
    players = [
        {"id": 1, "web_name": "Out",     "position": "FWD", "team_id": 1, "price": 8.0,  "status": "a", "xp5": 5.0},
        {"id": 2, "web_name": "KeepMid", "position": "MID", "team_id": 2, "price": 7.0,  "status": "a", "xp5": 30.0},
        {"id": 3, "web_name": "KeepDef", "position": "DEF", "team_id": 3, "price": 5.0,  "status": "a", "xp5": 25.0},
        {"id": 4, "web_name": "In",      "position": "FWD", "team_id": 4, "price": 8.0,  "status": "a", "xp5": 25.0},
        {"id": 5, "web_name": "PremFwd", "position": "FWD", "team_id": 5, "price": 12.0, "status": "a", "xp5": 40.0},
    ]
    _seed_db(db, players, squad_ids=[1, 2, 3], bank=1.0)
    out = transfers.get_transfer_suggestions(db)

    assert out["empty_reason"] is None
    assert 1 <= len(out["suggestions"]) <= 3
    s = out["suggestions"][0]
    assert s["out"]["player_id"] == 1 and s["in"]["player_id"] == 4   # p5 unaffordable (12 > 8+1)
    assert s["in"]["price"] <= s["out"]["price"] + 1.0 + 1e-9         # within budget
    assert s["hit_cost"] == 0
    assert isinstance(s["confidence"], int) and 0 <= s["confidence"] <= 100
    # exact contract shape
    assert set(s.keys()) == {"out", "in", "ep_delta_5gw", "hit_cost", "confidence"}
    assert set(s["out"].keys()) == {"player_id", "web_name", "price"}
    assert set(s["in"].keys()) == {"player_id", "web_name", "price"}


def test_get_transfer_suggestions_empty_reason(db):
    # squad players are the only players in their position, all available -> no sells
    players = [
        {"id": 1, "web_name": "A", "position": "MID", "team_id": 1, "price": 6.0, "status": "a", "xp5": 10.0},
        {"id": 2, "web_name": "B", "position": "MID", "team_id": 2, "price": 6.0, "status": "a", "xp5": 10.0},
        {"id": 3, "web_name": "C", "position": "MID", "team_id": 3, "price": 6.0, "status": "a", "xp5": 10.0},
    ]
    _seed_db(db, players, squad_ids=[1, 2, 3], bank=2.0)
    out = transfers.get_transfer_suggestions(db)
    assert out["suggestions"] == []
    assert out["empty_reason"] == "No transfers worth making this GW."
