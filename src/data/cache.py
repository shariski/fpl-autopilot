from datetime import datetime, timezone, timedelta

DEFAULT_TTL = {
    "bootstrap-static": timedelta(hours=6),
    "fixtures": timedelta(hours=6),
    "my_team": timedelta(hours=1),
    "understat": timedelta(hours=6),
}


def _now():
    return datetime.now(timezone.utc)


def is_stale(conn, resource, now=None):
    now = now or _now()
    row = conn.execute(
        "SELECT last_fetched_utc FROM cache_meta WHERE resource=?", (resource,)
    ).fetchone()
    if row is None:
        return True
    last = datetime.fromisoformat(row["last_fetched_utc"])
    ttl = DEFAULT_TTL.get(resource, timedelta(0))
    return (now - last) >= ttl


def mark_fetched(conn, resource, now=None):
    now = now or _now()
    conn.execute(
        """INSERT INTO cache_meta (resource, last_fetched_utc) VALUES (?,?)
           ON CONFLICT(resource) DO UPDATE SET last_fetched_utc=excluded.last_fetched_utc""",
        (resource, now.isoformat()),
    )
    conn.commit()
