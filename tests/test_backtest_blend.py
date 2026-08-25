"""v0.23 blend simulation (backtest.py run_simulation) on a synthetic scratch DB.

Frozen slice: 8 prior GWs (2024-25) + 6 live GWs (2025-26), 4 teams, no leakage.
The databank CSVs are gitignored, so CI runs this synthetic slice instead of the
full 38-GW CSV run (which stays a manual invocation).
"""
import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKTEST = ROOT / "docs" / "research" / "calibration" / "backtest.py"


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("calib_backtest", BACKTEST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calib_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


TEAMS = {"Alpha": 1, "Beta": 2, "Gamma": 3, "Delta": 4}
TEAM_NAMES = {1: "Alpha", 2: "Beta", 3: "Gamma", 4: "Delta"}
# two matches per GW: Alpha v Beta, Gamma v Delta (home team alternates by gw parity)
PRIOR_PLAYERS = [(i, t) for i, t in enumerate([1, 1, 2, 2, 3, 3, 4, 4], start=1)]


def _row(element, team, gw, *, xg=0.3, xa=0.1, tp=5, minutes=90, starts=1,
         dc=2, saves=0, yc=0, rc=0, home=None):
    opp = 2 if team == 1 else (1 if team == 2 else (4 if team == 3 else 3))
    # odd-id teams home on odd GWs, even-id home on even GWs -> exactly one home per pair
    is_home = (home if home is not None else (gw % 2) == (team % 2))
    return {"element": element, "name": f"P{element}", "team": TEAM_NAMES[team],
            "position": "MID",
            "minutes": minutes, "expected_goals": xg, "expected_assists": xa,
            "expected_goals_conceded": 1.4, "dc": dc, "saves": saves, "starts": starts,
            "bps": 20, "yellow_cards": yc, "red_cards": rc, "was_home": is_home,
            "value": 5.0, "bonus": 0, "total_points": tp, "opponent_team": opp}


def _gw_rows(gw, players):
    """One full GW of rows for the given players (2 per team, 1 per match pair)."""
    return [_row(pid, team, gw) for pid, team in players]


def _scratch(bt):
    sc = bt.build_scratch(TEAMS)
    for gw in range(1, 9):
        bt.upsert_rows(sc, "2024-25", gw, _gw_rows(gw, PRIOR_PLAYERS))
    return sc


def test_run_simulation_no_leakage_and_adaptation(bt):
    sc = _scratch(bt)
    live_players = dict(PRIOR_PLAYERS)
    live_players[9] = 4   # new signing appears from GW2

    def rows_for_gw(gw):
        if gw > 6:
            raise bt._GwMissing
        out = [dict(r) for r in _gw_rows(gw, [(p, t) for p, t in live_players.items()])]
        for r in out:
            r["expected_goals"] = 1.0 if r["element"] == 1 else r["expected_goals"]
        return out

    blend = bt.run_simulation(sc, "2024-25", "2025-26", rows_for_gw, max_gw=38, feed_live=True)

    # 6 live GWs simulated; all metrics finite (no NaN)
    assert len(blend) == 6
    for r in blend:
        assert math.isfinite(r.mae_v2) and math.isfinite(r.bias_v2)
        assert r.n > 0

    # no leakage: GW1 sees no live rows -> identical to pure prior
    sc2 = bt.build_scratch(TEAMS)
    for gw in range(1, 9):
        bt.upsert_rows(sc2, "2024-25", gw, _gw_rows(gw, PRIOR_PLAYERS))
    prior = bt.run_simulation(sc2, "2024-25", "2025-26", rows_for_gw, max_gw=38, feed_live=False)
    assert blend[0].mae_v2 == pytest.approx(prior[0].mae_v2)
    assert blend[0].bias_v2 == pytest.approx(prior[0].bias_v2)
    # adaptation: once live GWs accumulate, blend predictions diverge from pure prior
    assert blend[5].mae_v2 != pytest.approx(prior[5].mae_v2, abs=1e-9) or \
        blend[5].bias_v2 != pytest.approx(prior[5].bias_v2, abs=1e-9)


def test_run_simulation_rates_adapt_to_live_season(bt):
    """Drives the production functions directly: after 3 live GWs the role-change
    player's xg_per_start moved from 0.3 toward 1.0; the new signing (present from
    live GW2, 2 live GWs by GW3) is shrunk toward the MID position average."""
    from src.analytics import ratings
    from src.data import repository
    sc = _scratch(bt)
    base_players = dict(PRIOR_PLAYERS)
    for gw in (1, 2, 3):
        players = dict(base_players)
        if gw >= 2:
            players[9] = 4                     # new signing debuts in live GW2
        rows = _gw_rows(gw, [(p, t) for p, t in players.items()])
        for r in rows:
            r["expected_goals"] = 1.0 if r["element"] in (1, 9) else r["expected_goals"]
        bt.upsert_players_only(sc, "2025-26", gw, rows)
        repository.upsert_player_gw_stats(sc.conn, gw, bt._live_payload_rows(rows, gw))
    rates = ratings.compute_player_rates(sc.conn, live_season="2025-26")
    assert rates[1].xg_per_start > 0.35     # moved toward 1.0 (was 0.3)
    assert 9 in rates                        # new signing present
    # 2 live GWs -> w = 2/3: 2/3*1.0 + 1/3*pooled-MID(~0.32) ~= 0.77
    assert 0.5 < rates[9].xg_per_start < 0.95
    assert all(math.isfinite(getattr(rates[p], "xg_per_start")) for p in rates)
