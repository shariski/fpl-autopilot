import pytest
import requests
from src.data.understat_client import UnderstatClient
from src.data.models import UnderstatPlayersResponse

OK_BODY = {"success": True, "players": []}


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, items):
        self.headers = {}
        self._items = list(items)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(items, sleeps=None, times=None):
    sleeps = sleeps if sleeps is not None else []
    times = times if times is not None else iter(range(0, 10_000_000, 100))
    return UnderstatClient(session=FakeSession(items), sleep=sleeps.append, monotonic=lambda: next(times))


def test_user_agent_and_xrw_headers():
    session = FakeSession([])
    UnderstatClient(session=session)
    assert "Mozilla" in session.headers["User-Agent"]
    assert session.headers["X-Requested-With"] == "XMLHttpRequest"


def test_players_stats_posts_correct_body_and_parses():
    session = FakeSession([FakeResponse(200, OK_BODY)])
    client = UnderstatClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    result = client.players_stats("2025")
    assert isinstance(result, UnderstatPlayersResponse)
    url, data = session.calls[0]
    assert url.endswith("/main/getPlayersStats/")
    assert data == {"league": "EPL", "season": "2025"}


def test_retries_on_5xx_then_succeeds():
    sleeps = []
    client = _client([FakeResponse(500), FakeResponse(200, OK_BODY)], sleeps=sleeps)
    result = client.players_stats("2025")
    assert isinstance(result, UnderstatPlayersResponse)
    assert sleeps == [1]


def test_no_retry_on_404():
    session = FakeSession([FakeResponse(404)])
    client = UnderstatClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    with pytest.raises(requests.HTTPError):
        client.players_stats("2025")
    assert len(session.calls) == 1
