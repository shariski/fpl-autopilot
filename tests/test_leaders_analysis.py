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


# ---------- Increment 2: squad + progression ----------

def _seed_picks(db, gw=1):
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
               "VALUES (7,'Rogers','Rogers',1,'MID','a'),"
               "(8,'Palmer','Palmer',1,'MID','a'),"
               "(9,'Wissa','Wissa',2,'FWD','a')")
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE'),(2,'Newcastle','NEW')")
    import json as _json
    for eid in (1, 2, 3):
        picks = [{"element": 7, "position": 3, "multiplier": 1, "is_captain": False,
                  "is_vice_captain": False},
                 {"element": 8, "position": 3, "multiplier": 1, "is_captain": False,
                  "is_vice_captain": False},
                 {"element": 9, "position": 4, "multiplier": 1, "is_captain": False,
                  "is_vice_captain": False}]
        captain = vice = None
        if eid == 1:
            picks[0]["multiplier"] = 2
            picks[0]["is_captain"] = True
            captain, vice = 7, 8
        repository.upsert_leader_picks(db, eid, gw, _json.dumps(picks, sort_keys=True),
                                       captain_id=captain, vice_id=vice, formation="2-0-1")
    db.commit()


def test_ownership_counts_picks(db):
    _seed(db)
    _seed_picks(db)
    out = la.ownership(db, gw=1)
    assert out["cohort"] == 3
    by_name = {r["web_name"]: r for r in out["rows"]}
    assert by_name["Rogers"]["count"] == 3 and by_name["Rogers"]["pct"] == 1.0
    assert by_name["Rogers"]["differential"] is False
    assert by_name["Wissa"]["team_short"] == "NEW"


def test_captaincy(db):
    _seed(db)
    _seed_picks(db)
    out = la.captaincy(db, gw=1)
    assert out["rows"][0]["web_name"] == "Rogers" and out["rows"][0]["count"] == 1


def test_formations(db):
    _seed(db)
    _seed_picks(db)
    out = la.formations(db, gw=1)
    assert {r["formation"]: r["count"] for r in out["rows"]} == {"2-0-1": 3}


def test_progression_series(db):
    _seed(db)
    out = la.progression(db)
    assert len(out["series"]) == 3
    s1 = next(s for s in out["series"] if s["entry_id"] == 1)
    assert s1["points"] == [{"gw": 1, "rank": 11}, {"gw": 2, "rank": 12}]


def test_retention_curve(db):
    _seed(db)
    out = la.retention(db)
    assert out["gw1_cohort"] == 3
    # ranks: entry1 11,12; entry2 21,22; entry3 31,32 -> all <= 100 -> 3/3 retained
    assert out["by_gw"][-1]["retained"] == 3 and out["by_gw"][-1]["pct"] == 1.0


def test_analyze_includes_inc2_patterns(db):
    _seed(db)
    out = la.analyze(db)
    assert set(out["patterns"]) == {"chip_timing", "transfers", "bank_value", "momentum",
                                    "ownership", "captaincy", "formations",
                                    "progression", "retention"}
