"""Vaastav FPL databank client (per-GW CSVs, raw GitHub).

The databank mirrors FPL's per-GW player stats: one CSV per gameweek, per-GW values
(not cumulative). 1 request per GW vs ~500 element-summary calls — the efficient way to
get per-GW xG/xA/xGC/DC/saves/starts history for the xP v2 model.

Unofficial, no stability guarantee (B6): required columns are asserted per fetch and
schema drift fails loudly. `defensive_contribution` is a known exception: it exists only
from 2025-26, so it parses to 0 when absent (documented historical absence, not drift).
"""
import csv
import io
import time
import requests

BASE_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/gws/gw{gw}.csv"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAYS = (1, 5, 30)
MIN_INTERVAL = 1.0  # <= 1 req/s (B6)
TIMEOUT = 30

# Columns the xP v2 model consumes. Asserted per fetch (B6).
REQUIRED_COLUMNS = {
    "element", "name", "team", "position",
    "minutes", "expected_goals", "expected_assists", "expected_goals_conceded",
    "bonus", "bps", "total_points", "saves", "starts", "yellow_cards", "red_cards", "value", "was_home",
}


class DatabankClient:
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

    def _get(self, url):
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            self._rate_limit()
            try:
                resp = self._session.get(url, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
                else:
                    resp.raise_for_status()
            if attempt < len(RETRY_DELAYS):
                self._sleep(RETRY_DELAYS[attempt])
        raise last_exc

    def fetch_gw(self, season, gw):
        """Fetch one gameweek's per-GW player rows. Returns list of dicts (B6-asserted)."""
        url = BASE_URL.format(season=season, gw=gw)
        text = self._get(url)
        reader = csv.DictReader(io.StringIO(text))
        header = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - header
        if missing:
            raise ValueError(
                f"databank {season} gw{gw} missing required columns: "
                f"{sorted(missing)} (schema drift?)")
        has_dc = "defensive_contribution" in header
        rows = []
        for raw in reader:
            rows.append({
                "element": int(raw["element"]),
                "name": raw["name"],
                "team": raw["team"],
                "position": raw["position"],
                "minutes": int(raw["minutes"] or 0),
                "expected_goals": float(raw["expected_goals"] or 0),
                "expected_assists": float(raw["expected_assists"] or 0),
                "expected_goals_conceded": float(raw["expected_goals_conceded"] or 0),
                "bonus": int(raw["bonus"] or 0),
                "bps": int(raw["bps"] or 0),
                "total_points": int(raw["total_points"] or 0),
                "saves": int(raw["saves"] or 0),
                "starts": int(raw["starts"] or 0),
                "yellow_cards": int(raw["yellow_cards"] or 0),
                "red_cards": int(raw["red_cards"] or 0),
                # defensive_contribution exists only from 2025-26 (documented absence -> 0)
                "dc": int(raw["defensive_contribution"] or 0) if has_dc else 0,
                "value": int(raw["value"] or 0) / 10.0,  # databank tenths of £m
                "was_home": str(raw["was_home"]).lower() == "true",
            })
        return rows

    def fetch_season(self, season, max_gw=38):
        """Fetch gameweeks 1..max_gw for a season. Returns {gw: [rows, ...]}."""
        out = {}
        for gw in range(1, max_gw + 1):
            out[gw] = self.fetch_gw(season, gw)
        return out
