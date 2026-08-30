"""v0.27: leaders pattern analysis — deterministic statistics."""
import pytest

from src.analytics import leaders as la
from src.data import repository


def _seed(db, gws=(1, 2)):
    for eid in (1, 2, 3):
        repository.upsert_leader_entry(db, eid, f"P{eid}", f"E{eid}",
                                       past_rank=(50000 if eid == 1 else 9000000),
                                       past_pts=2000, first_gw=1, rank=eid, total=100)
    for eid in (1, 2, 3):
        for gw in gws:
            repository.upsert_leader_snapshot(db, eid, gw,
                points=60 if gw == 1 else 30, total_points=100 + 30 * gw,
                overall_rank=eid * 10 + gw, bank=5, value=1000 + gw,
                transfers=1 if gw == 1 else 0, hit_cost=4 if (eid == 1 and gw == 1) else 0,
                chip_played="3xc" if (eid == 1 and gw == 1) else None)


def test_chip_timing_cluster(db):
    _seed(db)
    out = la.chip_timing(db)
    row = next(r for r in out["rows"] if r["chip"] == "3xc" and r["gw"] == 1)
    assert row["count"] == 1
    assert out["first_chip"]["3xc"]["gw"] == 1


def test_transfer_discipline(db):
    _seed(db)
    out = la.transfer_discipline(db)
    # 6 leader-GWs: transfers = [1,0,1,0,1,0]; hits: one gw with cost 4
    assert out["mean_per_gw"] == 0.5
    assert out["median_per_gw"] == 0.5
    assert out["hit_freq"] == pytest.approx(1 / 6, abs=0.001)
    assert out["mean_hit_cost"] == 4.0
    assert {h["transfers"]: h["count"] for h in out["histogram"]} == {0: 3, 1: 3}


def test_bank_value_trajectories(db):
    _seed(db)
    out = la.bank_value(db)
    assert out["bank"][0]["gw"] == 1 and out["bank"][0]["mean"] == 5.0
    assert out["value"][1]["mean"] == 1002.0


def test_rank_momentum_and_sustained_elite(db):
    _seed(db)
    out = la.rank_momentum(db)
    assert out["sustained_elite"] == [1]          # only entry 1 has past rank <= 250000
    assert len(out["top_movers"]) == 3


def test_cohort_stats(db):
    _seed(db)
    out = la.cohort_stats(db)
    assert len(out) == 3
    e1 = next(x for x in out if x["entry_id"] == 1)
    assert e1["chips_used"] == ["3xc"] and e1["past_rank"] == 50000


def test_analyze_empty_db_guards(db):
    out = la.analyze(db)
    assert out["cohort"] == [] and out["patterns"]["chip_timing"]["rows"] == []
    assert out["patterns"]["transfers"]["mean_per_gw"] is None
