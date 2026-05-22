import time
import requests
from .models import UnderstatPlayersResponse

BASE_URL = "https://understat.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAYS = (1, 5, 30)
MIN_INTERVAL = 1.0  # <= 1 req/s (B6)
TIMEOUT = 15


class UnderstatClient:
    def __init__(self, session=None, sleep=time.sleep, monotonic=time.monotonic):
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = None

    def _rate_limit(self):
        if self._last_request_at is not None:
            wait = MIN_INTERVAL - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _post(self, path, data):
        url = BASE_URL + path
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._rate_limit()
            try:
                resp = self._session.post(url, data=data, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
                else:
                    resp.raise_for_status()
            if attempt < len(RETRY_DELAYS):
                self._sleep(RETRY_DELAYS[attempt])
        raise last_exc

    def players_stats(self, season="2025"):
        data = self._post("/main/getPlayersStats/", {"league": "EPL", "season": season})
        return UnderstatPlayersResponse.model_validate(data)
