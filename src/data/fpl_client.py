import time
import requests
from .models import BootstrapStatic, Fixture, Entry, EntryPicks, ElementSummary

BASE_URL = "https://fantasy.premierleague.com/api/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAYS = (1, 5, 30)
MIN_INTERVAL = 1.0  # <= 1 req/s (B6)
TIMEOUT = 10


class FPLClient:
    def __init__(self, session=None, sleep=time.sleep, monotonic=time.monotonic):
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = None

    def _rate_limit(self):
        if self._last_request_at is not None:
            wait = MIN_INTERVAL - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _get(self, path, params=None):
        url = BASE_URL + path
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
                else:
                    resp.raise_for_status()  # 4xx (non-429): fail immediately
            if attempt < len(RETRY_DELAYS):
                self._sleep(RETRY_DELAYS[attempt])
        raise last_exc

    def bootstrap_static(self):
        return BootstrapStatic.model_validate(self._get("bootstrap-static/"))

    def fixtures(self, event=None):
        params = {"event": event} if event is not None else None
        return [Fixture.model_validate(f) for f in self._get("fixtures/", params=params)]

    def entry(self, team_id):
        return Entry.model_validate(self._get(f"entry/{team_id}/"))

    def picks(self, team_id, gw):
        return EntryPicks.model_validate(self._get(f"entry/{team_id}/event/{gw}/picks/"))

    def element_summary(self, player_id):
        return ElementSummary.model_validate(self._get(f"element-summary/{player_id}/"))
