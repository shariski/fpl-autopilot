from datetime import datetime, timezone

_STATUS_PENALTY = {"a": 0, "d": 15}  # default 30 for i/s/u/n/unknown/empty


def _status_penalty(status):
    return _STATUS_PENALTY.get(status, 30)


def score(*, staleness_hours, statuses, gap):
    c = 75
    if staleness_hours is None or staleness_hours > 24:
        c -= 30
    elif staleness_hours > 6:
        c -= 10
    c -= max((_status_penalty(s) for s in statuses), default=0)
    if gap is None or gap > 2:
        c -= 0
    elif gap >= 1:
        c -= 5
    elif gap >= 0.5:
        c -= 15
    else:
        c -= 25
    return max(0, min(100, c))


def hours_since_refresh(conn, resource="bootstrap-static"):
    row = conn.execute("SELECT last_fetched_utc FROM cache_meta WHERE resource=?",
                       (resource,)).fetchone()
    if row is None:
        return None
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(row["last_fetched_utc"])
    return delta.total_seconds() / 3600.0
