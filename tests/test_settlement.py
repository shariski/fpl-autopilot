"""S-G T0: settlement subsystem tests.

The settlement job backfills player_gw_stats from FPL's event/{id}/live/ endpoint.
These are the deterministic tests for the data path; AI/audit logic lives in later tasks.
"""
from src.data import settlement
from src.data.db import connect, init_db


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _seed_gameweeks(conn, finished_gws, unfinished_gws=()):
    rows = [(gw, f"2026-01-{gw:02d}T11:30:00Z", 1, 0, 1) for gw in finished_gws]
    rows += [(gw, f"2026-01-{gw:02d}T11:30:00Z", 0, 1, 0) for gw in unfinished_gws]
    conn.executemany(
        "INSERT INTO gameweeks (id, deadline_utc, finished, is_next, is_current) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


class StubFPLClient:
    """Returns canned event/{id}/live/ payloads keyed by gw."""
    def __init__(self, payloads, *, raises_on=None):
        self.payloads = payloads
        self.raises_on = raises_on or set()
        self.calls = []

    def event_live(self, event_id):
        self.calls.append(event_id)
        if event_id in self.raises_on:
            raise RuntimeError(f"forced failure for gw={event_id}")
        return self.payloads[event_id]


def _live_payload(elements):
    """Build a minimal FPL event/{id}/live/ payload from a list of (player_id, fixture_id, **stats) dicts."""
    return {
        "elements": [
            {
                "id": el["player_id"],
                "stats": {
                    "minutes": el.get("minutes", 0),
                    "goals_scored": el.get("goals_scored", 0),
                    "assists": el.get("assists", 0),
                    "clean_sheets": el.get("clean_sheets", 0),
                    "bonus": el.get("bonus", 0),
                    "total_points": el.get("total_points", 0),
                },
                "explain": [{"fixture": el["fixture_id"], "stats": []}]
                if el.get("fixture_id") is not None else [],
            }
            for el in elements
        ]
    }


def test_event_live_returns_expected_shape():
    """FPLClient.event_live returns a dict with `elements` containing per-player stats."""
    from src.data.fpl_client import FPLClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {"elements": [{"id": 1, "stats": {"minutes": 90, "goals_scored": 1,
                                  "assists": 0, "clean_sheets": 0, "bonus": 2,
                                  "total_points": 9},
                                  "explain": [{"fixture": 42, "stats": []}]}]}
        def raise_for_status(self): pass

    class FakeSession:
        headers = {}
        def get(self, url, params=None, timeout=None):
            assert "event/3/live" in url
            return FakeResp()

    client = FPLClient(session=FakeSession(), sleep=lambda _: None, monotonic=lambda: 0.0)
    out = client.event_live(3)
    assert "elements" in out
    assert out["elements"][0]["id"] == 1
    assert out["elements"][0]["stats"]["total_points"] == 9
    assert out["elements"][0]["explain"][0]["fixture"] == 42


def test_settlement_writes_player_gw_stats():
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3])
    client = StubFPLClient({3: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "goals_scored": 1,
         "assists": 0, "clean_sheets": 0, "bonus": 2, "total_points": 9},
        {"player_id": 2, "fixture_id": 42, "minutes": 65, "goals_scored": 0,
         "assists": 1, "clean_sheets": 0, "bonus": 1, "total_points": 5},
    ])})

    written = settlement.settlement_run(conn, client)
    assert written == 2

    rows = list(conn.execute(
        "SELECT player_id, gw, fixture_id, total_points FROM player_gw_stats ORDER BY player_id"))
    assert len(rows) == 2
    assert rows[0]["player_id"] == 1 and rows[0]["total_points"] == 9
    assert rows[1]["player_id"] == 2 and rows[1]["total_points"] == 5


def test_settlement_is_idempotent():
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3])
    client = StubFPLClient({3: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 9},
    ])})

    first = settlement.settlement_run(conn, client)
    second = settlement.settlement_run(conn, client)

    assert first == 1
    assert second == 0
    assert client.calls == [3]  # only one API call — the second run sees GW3 already in player_gw_stats


def test_settlement_only_runs_for_finished_gws():
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3], unfinished_gws=[4, 5])
    client = StubFPLClient({3: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 9},
    ])})

    settlement.settlement_run(conn, client)

    assert client.calls == [3]


def test_settlement_handles_dgw():
    """DGW: same (player_id, gw) with two fixture_ids → two rows."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[18])
    # Player 1 played two fixtures in GW18 (a DGW)
    client = StubFPLClient({18: {
        "elements": [{
            "id": 1,
            "stats": {"minutes": 180, "goals_scored": 1, "assists": 1,
                      "clean_sheets": 0, "bonus": 3, "total_points": 13},
            "explain": [
                {"fixture": 100, "stats": []},
                {"fixture": 101, "stats": []},
            ],
        }]
    }})

    written = settlement.settlement_run(conn, client)
    assert written == 2

    rows = list(conn.execute(
        "SELECT fixture_id FROM player_gw_stats WHERE player_id=1 AND gw=18 ORDER BY fixture_id"))
    assert [r["fixture_id"] for r in rows] == [100, 101]


def test_settlement_swallows_per_gw_errors():
    """One GW raises, others still settle."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3, 4, 5])
    client = StubFPLClient(
        payloads={
            3: _live_payload([{"player_id": 1, "fixture_id": 42, "total_points": 5}]),
            5: _live_payload([{"player_id": 2, "fixture_id": 50, "total_points": 7}]),
        },
        raises_on={4},
    )

    written = settlement.settlement_run(conn, client)
    assert written == 2  # gw3 + gw5

    gws_settled = set(r["gw"] for r in conn.execute("SELECT DISTINCT gw FROM player_gw_stats"))
    assert gws_settled == {3, 5}
