"""v0.27: leaders snapshot fetch — standings + histories → DB."""
import pytest

from src.data import leaders
from src.data.db import connect, init_db


class StubClient:
    def __init__(self, standings, histories):
        self._standings = standings
        self._histories = histories
        self.calls = []

    def leagues_classic(self, league_id, page=1):
        self.calls.append(("standings", page))
        return {"standings": {"results": self._standings[page]}}

    def entry_history(self, entry_id):
        self.calls.append(("history", entry_id))
        return self._histories[entry_id]


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1),(2,'GW2',0)")
    conn.commit()
    return conn


def _standings(n):
    return [[{"entry": i, "rank": i, "player_name": f"P{i}", "entry_name": f"E{i}",
              "total": 200 - i} for i in range(1, n + 1)] for _ in range(2)]


def test_fetch_snapshots_latest_settled_gw():
    conn = _db()
    hist = {i: {"current": [{"event": 1, "points": 90, "total_points": 200,
                             "overall_rank": i, "bank": 10, "value": 1000,
                             "event_transfers": 1, "event_transfers_cost": 0}],
                "past": [{"season_name": "2025/26", "rank": 1000000, "total_points": 2000}],
                "chips": [{"name": "3xc", "event": 1}]} for i in range(1, 4)}
    client = StubClient(_standings(3), hist)
    n_e, n_s = leaders.fetch_leader_snapshot(conn, client, pages=2)
    assert n_e == 3 and n_s == 3
    row = conn.execute("SELECT * FROM leader_gw_snapshots WHERE entry_id=1").fetchone()
    assert row["gw"] == 1 and row["chip_played"] == "3xc" and row["points"] == 90
    ent = conn.execute("SELECT * FROM leader_entries WHERE entry_id=1").fetchone()
    assert ent["past_season_rank"] == 1000000 and ent["last_rank"] == 1
    # idempotent: re-run snapshots nothing new (gw2 unfinished)
    assert leaders.fetch_leader_snapshot(conn, client, pages=2) == (0, 0)


def test_fetch_skips_when_no_settled_gw():
    conn = connect(":memory:")
    init_db(conn)
    assert leaders.fetch_leader_snapshot(conn, StubClient([[]], {}), pages=2) == (0, 0)


def test_fetch_schema_drift_fails_loudly():
    conn = _db()

    class BadClient:
        def leagues_classic(self, league_id, page=1):
            return {"standings": {}}

        def entry_history(self, entry_id):
            return {}

    with pytest.raises(ValueError):
        leaders.fetch_leader_snapshot(conn, BadClient(), pages=2)
