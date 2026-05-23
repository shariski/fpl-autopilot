from datetime import datetime, timedelta, timezone
from src.decisions import confidence


def test_score_all_clear():
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=3.0) == 75


def test_score_staleness_tiers():
    assert confidence.score(staleness_hours=12.0, statuses=["a"], gap=3.0) == 65
    assert confidence.score(staleness_hours=30.0, statuses=["a"], gap=3.0) == 45
    assert confidence.score(staleness_hours=None, statuses=["a"], gap=3.0) == 45


def test_score_status_tiers():
    assert confidence.score(staleness_hours=0.0, statuses=["d"], gap=3.0) == 60
    assert confidence.score(staleness_hours=0.0, statuses=["i"], gap=3.0) == 45
    assert confidence.score(staleness_hours=0.0, statuses=["a", "d"], gap=3.0) == 60
    assert confidence.score(staleness_hours=0.0, statuses=["x"], gap=3.0) == 45


def test_score_gap_tiers():
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=1.5) == 70
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=0.7) == 60
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=0.2) == 50
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=None) == 75


def test_score_clamps_at_zero():
    assert confidence.score(staleness_hours=30.0, statuses=["i"], gap=0.1) == 0    # 75-30-30-25 -> clamp 0
    assert confidence.score(staleness_hours=0.0, statuses=["a"], gap=10.0) == 75


def test_hours_since_refresh(db):
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO cache_meta (resource, last_fetched_utc) VALUES (?, ?)",
               ("bootstrap-static", (now - timedelta(hours=12)).isoformat()))
    db.commit()
    h = confidence.hours_since_refresh(db)
    assert 11.5 < h < 12.5


def test_hours_since_refresh_missing_row(db):
    assert confidence.hours_since_refresh(db) is None
