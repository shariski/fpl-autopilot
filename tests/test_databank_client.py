import pytest
import requests
from src.data.databank_client import DatabankClient

GW1_CSV = """name,position,team,xP,assists,bonus,bps,clean_sheets,creativity,element,expected_assists,expected_goal_involvements,expected_goals,expected_goals_conceded,fixture,goals_conceded,goals_scored,ict_index,influence,kickoff_time,minutes,modified,opponent_team,own_goals,penalties_missed,penalties_saved,red_cards,round,saves,selected,starts,team_a_score,team_h_score,threat,total_points,transfers_balance,transfers_in,transfers_out,value,was_home,yellow_cards
Erling Haaland,FWD,Man City,6.0,1,3,35,1,4.5,217,0.50,0.60,1.40,0.35,1,1,1,3.1,44.1,2026-08-15T14:00:00Z,90,False,15,0,0,0,0,1,2,1,2,1,0,9.0,13,0,0,0,130,True,0
David Raya,GK,Arsenal,5.5,0,0,12,1,0.0,154,0.01,0.00,0.00,0.80,2,0,0,1.0,10.0,2026-08-15T19:00:00Z,90,False,7,0,0,0,0,2,4,1,1,0,1,8.0,7,0,0,0,55,False,0
"""

GW1_24_25 = """name,position,team,xP,assists,bonus,bps,clean_sheets,creativity,element,expected_assists,expected_goal_involvements,expected_goals,expected_goals_conceded,fixture,goals_conceded,goals_scored,ict_index,influence,kickoff_time,minutes,modified,opponent_team,own_goals,penalties_missed,penalties_saved,red_cards,round,saves,selected,starts,team_a_score,team_h_score,threat,total_points,transfers_balance,transfers_in,transfers_out,value,was_home,yellow_cards
Erling Haaland,FWD,Man City,5.0,0,2,30,0,3.5,217,0.40,0.50,1.10,0.30,1,0,1,2.5,38.0,2025-08-15T14:00:00Z,90,False,15,0,0,0,0,0,1,1,2,1,0,7.0,9,0,0,0,125,True,0
"""

MISSING_COL = """name,position,team,xP,assists,bonus,bps,element,minutes,total_points,value,was_home
Erling Haaland,FWD,Man City,5.0,0,2,30,217,90,9,125,True
"""


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, items):
        self.headers = {}
        self._items = list(items)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(items, sleeps=None, times=None, min_gw_rows=0):
    sleeps = sleeps if sleeps is not None else []
    times = times if times is not None else iter(range(0, 10_000_000, 100))
    return DatabankClient(
        session=FakeSession(items),
        sleep=sleeps.append,
        monotonic=lambda: next(times),
        min_gw_rows=min_gw_rows,
    )


def test_user_agent_is_realistic():
    session = FakeSession([])
    DatabankClient(session=session)
    assert "Mozilla" in session.headers["User-Agent"]


def test_parses_gw_csv_rows():
    client = _client([FakeResponse(200, GW1_CSV)])
    rows = client.fetch_gw("2025-26", 1)
    assert len(rows) == 2
    haaland = rows[0]
    assert haaland["element"] == 217
    assert haaland["minutes"] == 90
    assert haaland["expected_goals"] == pytest.approx(1.40)
    assert haaland["expected_goals_conceded"] == pytest.approx(0.35)
    assert haaland["starts"] == 2
    assert haaland["dc"] == 0          # column present but Haaland had none
    assert haaland["value"] == pytest.approx(13.0)   # databank 130 (tenths) -> £13.0
    assert haaland["was_home"] is True
    assert haaland["team"] == "Man City"
    assert haaland["opponent_team"] == 15


def test_missing_dc_column_defaults_zero_for_old_season():
    client = _client([FakeResponse(200, GW1_24_25)])
    rows = client.fetch_gw("2024-25", 1)
    assert rows[0]["dc"] == 0


def test_schema_drift_missing_column_raises():
    client = _client([FakeResponse(200, MISSING_COL)])
    with pytest.raises(ValueError, match="expected_goals"):
        client.fetch_gw("2025-26", 1)


def test_retries_on_5xx_then_succeeds():
    sleeps = []
    client = _client([
        FakeResponse(500),
        FakeResponse(500),
        FakeResponse(200, GW1_CSV),
    ], sleeps=sleeps)
    rows = client.fetch_gw("2025-26", 1)
    assert len(rows) == 2
    assert sleeps == [1, 5]  # backoff


def test_raises_after_exhausting_retries():
    sleeps = []
    client = _client([FakeResponse(500)] * 4, sleeps=sleeps)
    with pytest.raises(requests.HTTPError):
        client.fetch_gw("2025-26", 1)
    assert len(sleeps) == 3


def test_fetch_season_loops_over_gws():
    client = _client([FakeResponse(200, GW1_CSV)] * 3)
    out = client.fetch_season("2025-26", max_gw=3)
    assert sorted(out.keys()) == [1, 2, 3]
    assert all(len(v) == 2 for v in out.values())
    assert client._session.calls[0].endswith("/data/2025-26/gws/gw1.csv")
    assert client._session.calls[2].endswith("/data/2025-26/gws/gw3.csv")
