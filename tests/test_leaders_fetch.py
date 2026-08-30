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
        return {"standings": {"results": self._standings[page - 1]}}

    def entry_history(self, entry_id):
        self.calls.append(("history", entry_id))
        return self._histories[entry_id]

    def entry_picks(self, entry_id, gw):
        self.calls.append(("picks", entry_id, gw))
        return {"picks": [{"element": 7, "position": 1, "multiplier": 2,
                           "is_captain": True, "is_vice_captain": False},
                          {"element": 8, "position": 2, "multiplier": 1,
                           "is_captain": False, "is_vice_captain": False},
                          {"element": 9, "position": 3, "multiplier": 1,
                           "is_captain": False, "is_vice_captain": True}]}


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1),(2,'GW2',0)")
    conn.commit()
    return conn


def _standings(per_page):
    return [[{"entry": i, "rank": i, "player_name": f"P{i}", "entry_name": f"E{i}",
              "total": 200 - i} for i in range(offset + 1, offset + per_page + 1)]
            for offset in (0, per_page)]


def test_fetch_snapshots_latest_settled_gw():
    conn = _db()
    conn.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                     "VALUES (?,?,?,?,?, 'a')",
                     [(7, "Rogers", "Rogers", 1, "MID"), (8, "Palmer", "Palmer", 1, "MID"),
                      (9, "Wissa", "Wissa", 2, "FWD")])
    conn.commit()
    hist = {i: {"current": [{"event": 1, "points": 90, "total_points": 200,
                             "overall_rank": i, "bank": 10, "value": 1000,
                             "event_transfers": 1, "event_transfers_cost": 0}],
                "past": [{"season_name": "2025/26", "rank": 1000000, "total_points": 2000}],
                "chips": [{"name": "3xc", "event": 1}]} for i in range(1, 7)}
    client = StubClient(_standings(3), hist)
    n_e, n_s = leaders.fetch_leader_snapshot(conn, client, pages=2)
    assert n_e == 6 and n_s == 6
    row = conn.execute("SELECT * FROM leader_gw_snapshots WHERE entry_id=1").fetchone()
    assert row["gw"] == 1 and row["chip_played"] == "3xc" and row["points"] == 90
    pk = conn.execute("SELECT * FROM leader_gw_picks WHERE entry_id=1 AND gw=1").fetchone()
    assert pk["captain_id"] == 7 and pk["vice_id"] == 9
    assert pk["formation"] == "0-2-1"  # starters (slots 1-3): MID MID FWD
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
