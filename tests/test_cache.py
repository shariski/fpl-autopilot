from datetime import datetime, timezone, timedelta
from src.data import cache


def test_is_stale_when_never_fetched(db):
    assert cache.is_stale(db, "bootstrap-static") is True


def test_not_stale_immediately_after_mark(db):
    cache.mark_fetched(db, "bootstrap-static")
    assert cache.is_stale(db, "bootstrap-static") is False


def test_stale_after_ttl_elapsed(db):
    cache.mark_fetched(db, "bootstrap-static")
    future = datetime.now(timezone.utc) + timedelta(hours=7)  # TTL is 6h
    assert cache.is_stale(db, "bootstrap-static", now=future) is True
