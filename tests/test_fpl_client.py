import pytest
import requests
from src.data.fpl_client import FPLClient
from src.data.models import BootstrapStatic

EMPTY_BOOTSTRAP = {"events": [], "teams": [], "elements": [], "element_types": []}


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
        self._items = list(items)  # FakeResponse or Exception instances
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(items, sleeps=None, times=None):
    sleeps = sleeps if sleeps is not None else []
    times = times if times is not None else iter(range(0, 10_000_000, 100))
    return FPLClient(
        session=FakeSession(items),
        sleep=sleeps.append,
        monotonic=lambda: next(times),
    )


def test_user_agent_is_realistic():
    session = FakeSession([])
    FPLClient(session=session)
    assert "Mozilla" in session.headers["User-Agent"]


def test_parses_bootstrap_into_model():
    client = _client([FakeResponse(200, EMPTY_BOOTSTRAP)])
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)


def test_retries_on_5xx_then_succeeds():
    sleeps = []
    client = _client(
        [FakeResponse(500), FakeResponse(503), FakeResponse(200, EMPTY_BOOTSTRAP)],
        sleeps=sleeps,
    )
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)
    assert sleeps == [1, 5]  # two backoffs before the third call succeeds


def test_retries_on_connection_error():
    sleeps = []
    client = _client(
        [requests.ConnectionError("boom"), FakeResponse(200, EMPTY_BOOTSTRAP)],
        sleeps=sleeps,
    )
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)
    assert sleeps == [1]


def test_no_retry_on_404():
    session = FakeSession([FakeResponse(404)])
    client = FPLClient(session=session, sleep=lambda s: None, monotonic=lambda: 0.0)
    with pytest.raises(requests.HTTPError):
        client.entry(999999999)
    assert len(session.calls) == 1


def test_rate_limit_sleeps_between_calls():
    sleeps = []
    client = FPLClient(
        session=FakeSession([FakeResponse(200, EMPTY_BOOTSTRAP),
                             FakeResponse(200, EMPTY_BOOTSTRAP)]),
        sleep=sleeps.append,
        monotonic=lambda: 0.0,  # no time passes -> must wait ~1s before 2nd call
    )
    client.bootstrap_static()
    client.bootstrap_static()
    assert 1.0 in sleeps


def test_retries_on_429_then_succeeds():
    sleeps = []
    client = _client(
        [FakeResponse(429), FakeResponse(200, EMPTY_BOOTSTRAP)],
        sleeps=sleeps,
    )
    result = client.bootstrap_static()
    assert isinstance(result, BootstrapStatic)
    assert sleeps == [1]


def test_raises_after_exhausting_retries():
    # 4 attempts (initial + 3 retries), all 500 -> must raise after the last
    session = FakeSession([FakeResponse(500), FakeResponse(500),
                           FakeResponse(500), FakeResponse(500)])
    client = FPLClient(
        session=session,
        sleep=lambda s: None,
        monotonic=lambda: 0.0,
    )
    with pytest.raises(requests.HTTPError):
        client.bootstrap_static()
    assert len(session.calls) == 4
