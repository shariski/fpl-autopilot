"""v0.27: leaders cohort — repository round-trip."""
from src.data import repository


def test_leader_entry_upsert(db):
    repository.upsert_leader_entry(db, 4829085, "Harman Messi", "shadi",
                                   past_rank=12310989, past_pts=1144,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_entry(db, 4829085, "Harman Messi", "shadi",
                                   past_rank=12310989, past_pts=1144,
                                   first_gw=1, rank=2, total=265)
    row = db.execute("SELECT * FROM leader_entries WHERE entry_id=4829085").fetchone()
    assert row["last_rank"] == 2 and row["last_total"] == 265


def test_leader_snapshot_upsert_and_chip_coalesce(db):
    repository.upsert_leader_snapshot(db, 1, 2, points=38, total_points=265,
                                      overall_rank=12900, bank=4, value=1000,
                                      transfers=1, hit_cost=0, chip_played=None)
    repository.upsert_leader_snapshot(db, 1, 2, points=38, total_points=265,
                                      overall_rank=12900, bank=4, value=1000,
                                      transfers=1, hit_cost=0, chip_played="3xc")
    row = db.execute("SELECT * FROM leader_gw_snapshots WHERE entry_id=1 AND gw=2").fetchone()
    assert row["chip_played"] == "3xc"     # late chip arrival fills the slot


def test_latest_leader_gw(db):
    assert repository.latest_leader_gw(db) is None
    repository.upsert_leader_snapshot(db, 1, 1, 0, 0, 0, 0, 0, 0, 0, None)
    repository.upsert_leader_snapshot(db, 1, 3, 0, 0, 0, 0, 0, 0, 0, None)
    assert repository.latest_leader_gw(db) == 3
