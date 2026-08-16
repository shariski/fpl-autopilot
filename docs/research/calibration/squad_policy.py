"""Squad-composition policy backtest: does DEF-heavy 2-5-5-3 beat MID-heavy builds?

Reuses the backtest harness machinery (progressive scratch DB, no leakage, v2 xP).
For each GW of 25-26: selects a 15 under each position policy (top-xP per position,
plus a budget-constrained greedy variant ≤100m, ≤3/club), then scores the ACTUAL
points of the selected players that GW. Also scores a starting-XI proxy (top-11 by xP
under each formation shape).

Policies: A = 2-5-5-3 (current legal minimums, max DEFs)
          B = 2-3-7-3 (heavy midfield)
          C = 2-4-6-3 (balanced lean)
Starting-XI shapes: A: 1-4-4-2, B: 1-3-5-2, C: 1-4-5-1? -> use 1-3-4-3 for C.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import (  # noqa: E402
    _LocalSession, _season_team_maps, _canonicalize_rows, build_scratch,
    upsert_rows, fixtures_for_gw)
from src.data.databank_client import DatabankClient  # noqa: E402
from src.analytics import ratings, fdr, xp  # noqa: E402

SEASON = "2025-26"
MIN_GW = 3  # need >= 2 GWs of history for stable rates

POLICIES = {"A": {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3},
            "B": {"GKP": 2, "DEF": 3, "MID": 7, "FWD": 3},
            "C": {"GKP": 2, "DEF": 4, "MID": 6, "FWD": 3}}
XIS = {"A": (1, 4, 4, 2), "B": (1, 3, 5, 2), "C": (1, 3, 4, 3)}  # GK-DEF-MID-FWD


def select_top(pool, caps):
    """Top-xP per position within caps. pool: list of dicts {pid, pos, xp, actual}."""
    by_pos = defaultdict(list)
    for p in pool:
        by_pos[p["pos"]].append(p)
    picks = []
    for pos, n in caps.items():
        picks.extend(sorted(by_pos[pos], key=lambda p: -p["xp"])[:n])
    return picks


def select_budget(pool, caps, budget=100.0):
    """Greedy by xP: fits budget (<=100), <=3 per club. pool items carry price/team."""
    picks, used = [], 0.0
    clubs = Counter()
    slots = dict(caps)
    for p in sorted(pool, key=lambda p: -p["xp"]):
        pos = p["pos"]
        if slots.get(pos, 0) <= 0:
            continue
        if used + p["price"] > budget + 1e-9:
            continue
        if clubs[p["team"]] >= 3:
            continue
        picks.append(p)
        used += p["price"]
        slots[pos] -= 1
        clubs[p["team"]] += 1
    return picks


def select_xi(pool, shape):
    gk, d, m, f = shape
    by_pos = defaultdict(list)
    for p in pool:
        by_pos[p["pos"]].append(p)
    out = sorted(by_pos["GKP"], key=lambda p: -p["xp"])[:gk]
    out += sorted(by_pos["DEF"], key=lambda p: -p["xp"])[:d]
    out += sorted(by_pos["MID"], key=lambda p: -p["xp"])[:m]
    out += sorted(by_pos["FWD"], key=lambda p: -p["xp"])[:f]
    return out


def main():
    client = DatabankClient(session=_LocalSession())
    season_maps, canonical = _season_team_maps()
    sc = build_scratch(canonical)
    _, id_to_name = season_maps[SEASON]

    gw_results = {k: [] for k in POLICIES}
    xi_results = {k: [] for k in XIS}
    budget_results = {k: [] for k in POLICIES}

    for gw in range(1, 39):
        rows = _canonicalize_rows(SEASON, client.fetch_gw(SEASON, gw), id_to_name, canonical)
        team_ratings, la = ratings.compute_team_ratings(sc.conn)
        fixtures, _ = fixtures_for_gw(sc, rows)
        mults = {x["team_id"]: x for x in fdr.compute_fdr_v2(team_ratings, la, fixtures, {})}
        player_rates = ratings.compute_player_rates(sc.conn)

        pool = []
        for r in rows:
            tid = canonical.get(r["team"])
            if tid is None or tid not in mults:
                continue
            pr = player_rates.get(r["element"])
            if pr is None:
                continue
            opp_id = None
            for fx in fixtures:
                if fx["home_team_id"] == tid:
                    opp_id = fx["away_team_id"]
                elif fx["away_team_id"] == tid:
                    opp_id = fx["home_team_id"]
            if opp_id is None:
                continue
            opp_r = team_ratings.get(opp_id)
            dc_ratio = ratings.damp(opp_r.dc90 / la.dc90) if opp_r and la.dc90 else 1.0
            txc = team_ratings.get(tid).xgc90 if tid in team_ratings else la.xgc90
            res = xp.compute_player_xp_v2(
                pr.position, "a", 1.0, pr.starts, pr.squads_made,
                pr.xg_per_start, pr.xa_per_start, pr.dc_hit_rate, pr.saves_per_90,
                pr.yc_per_90, pr.rc_per_90, pr.p60, txc,
                xg_ratio=mults[tid]["fdr_defense_mult"],
                xgc_ratio=mults[tid]["fdr_attack_mult"],
                dc_ratio=dc_ratio, venue="H" if r["was_home"] else "A")
            pool.append({"pid": r["element"], "pos": pr.position, "xp": res["xp"],
                         "actual": int(r["total_points"]), "price": r["value"],
                         "team": r["team"]})

        if gw >= MIN_GW and pool:
            for k, caps in POLICIES.items():
                picks = select_top(pool, caps)
                gw_results[k].append(sum(p["actual"] for p in picks))
                bpicks = select_budget(pool, caps)
                budget_results[k].append(sum(p["actual"] for p in bpicks))
            for k, shape in XIS.items():
                picks = select_xi(pool, shape)
                xi_results[k].append(sum(p["actual"] for p in picks))

        upsert_rows(sc, SEASON, gw, rows)

    print(f"=== Squad-policy backtest {SEASON} (GW{MIN_GW}-38, "
          f"actual points of selected players) ===")
    print(f"{'policy':24s} {'squad15':>8s} {'budget<=100':>12s} {'starting XI':>12s}")
    for k in POLICIES:
        r = gw_results[k]
        b = budget_results[k]
        xi = xi_results[k]
        print(f"{k + ' (' + str(POLICIES[k]) + ')':24s} "
              f"{sum(r)/len(r):8.2f} {sum(b)/len(b):12.2f} {sum(xi)/len(xi):12.2f}")
    print("\nper-GW deltas vs policy A (squad15 actuals):")
    base = gw_results["A"]
    for k in ("B", "C"):
        diff = [a - b for a, b in zip(base, gw_results[k])]
        wins = sum(1 for d in diff if d > 0)
        print(f"  {k}: avg diff {sum(diff)/len(diff):+.2f} pts/GW, beats A in {wins}/{len(diff)} GWs")


if __name__ == "__main__":
    main()
