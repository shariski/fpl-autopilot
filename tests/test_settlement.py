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
                    "starts": el.get("starts", 0),
                    "saves": el.get("saves", 0),
                    "bps": el.get("bps", 0),
                    "expected_goals": el.get("expected_goals", 0.0),
                    "expected_assists": el.get("expected_assists", 0.0),
                    "expected_goals_conceded": el.get("expected_goals_conceded", 0.0),
                    "defensive_contribution": el.get("defensive_contribution", 0),
                    "yellow_cards": el.get("yellow_cards", 0),
                    "red_cards": el.get("red_cards", 0),
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


def test_settlement_writes_full_stat_set():
    """v0.23: settlement captures the full per-GW stat set, not just points."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[3])
    client = StubFPLClient({3: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 9,
         "starts": 1, "saves": 2, "bps": 28,
         "expected_goals": 0.5, "expected_assists": 0.2,
         "expected_goals_conceded": 1.4, "defensive_contribution": 3,
         "yellow_cards": 1, "red_cards": 0},
    ])})

    written = settlement.settlement_run(conn, client)
    assert written == 1

    row = conn.execute(
        "SELECT starts, saves, bps, expected_goals, expected_assists, "
        "expected_goals_conceded, defensive_contribution, yellow_cards, red_cards "
        "FROM player_gw_stats WHERE player_id=1 AND gw=3").fetchone()
    assert row["starts"] == 1
    assert row["saves"] == 2
    assert row["bps"] == 28
    assert row["expected_goals"] == 0.5
    assert row["expected_assists"] == 0.2
    assert row["expected_goals_conceded"] == 1.4
    assert row["defensive_contribution"] == 3
    assert row["yellow_cards"] == 1
    assert row["red_cards"] == 0


def test_settlement_backfills_pre_existing_rows():
    """v0.23: a GW settled before the full-stat columns existed (starts NULL) is
    re-fetched and backfilled; existing columns stay frozen."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1])
    # old-shape row: written by pre-v0.23 code (7 columns, starts NULL)
    conn.execute(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (1, 1, 42, 90, 1, 0, 0, 2, 9, 't')""")
    conn.commit()
    # the re-fetch carries corrected stats (total_points=99 must NOT overwrite 9)
    client = StubFPLClient({1: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 99,
         "starts": 1, "saves": 2, "bps": 28,
         "expected_goals": 0.5, "expected_assists": 0.2,
         "expected_goals_conceded": 1.4, "defensive_contribution": 3,
         "yellow_cards": 1, "red_cards": 0},
    ])})

    written = settlement.settlement_run(conn, client)
    assert written == 1  # backfilled row counts

    row = conn.execute(
        "SELECT total_points, starts, expected_goals FROM player_gw_stats "
        "WHERE player_id=1 AND gw=1").fetchone()
    assert row["total_points"] == 9       # frozen
    assert row["starts"] == 1             # backfilled
    assert row["expected_goals"] == 0.5
    assert client.calls == [1]


def test_settlement_backfill_is_idempotent():
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1])
    conn.execute(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (1, 1, 42, 90, 0, 0, 0, 0, 5, 't')""")
    conn.commit()
    client = StubFPLClient({1: _live_payload([
        {"player_id": 1, "fixture_id": 42, "minutes": 90, "total_points": 5,
         "starts": 1},
    ])})

    first = settlement.settlement_run(conn, client)
    second = settlement.settlement_run(conn, client)

    assert first == 1
    assert second == 0
    assert client.calls == [1]  # second run sees starts already filled


def test_settlement_backfill_failure_is_isolated():
    """A failing backfill re-fetch does not block other GWs (same contract as settlement)."""
    conn = _db()
    _seed_gameweeks(conn, finished_gws=[1, 2])
    conn.executemany(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, settled_at)
           VALUES (?,?,?,90,0,0,0,0,5,'t')""",
        [(1, 1, 42), (2, 2, 50)])
    conn.commit()
    client = StubFPLClient(
        payloads={2: _live_payload([{"player_id": 2, "fixture_id": 50,
                                     "minutes": 90, "total_points": 5, "starts": 1}])},
        raises_on={1},
    )

    settlement.settlement_run(conn, client)

    assert sorted(client.calls) == [1, 2]   # settle pass (gw2) runs before backfill (gw1)
    assert conn.execute("SELECT starts FROM player_gw_stats WHERE gw=2").fetchone()["starts"] == 1
    assert conn.execute("SELECT starts FROM player_gw_stats WHERE gw=1").fetchone()["starts"] is None
