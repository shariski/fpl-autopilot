"""Re-ingest a databank season from local CSVs and recompute FDR + xP.

Use after the element-id remap fix (src/cli.py `_remap_databank_elements`):
rows ingested before the fix are mis-attributed because FPL reuses element ids
across seasons (e.g. 25-26 "Cole Palmer" element 235 landed on today's id 235).
The live Vaastav GitHub CSVs can also be truncated, so this tool reads the last
known-good per-GW CSVs from `data/databank/` (fetched 2026-08-16, full 692-row
sets; see AGENTS.md "Vaastav databank ingestion").

It DELETES the stored season rows first, then re-ingests with the fixed name
remap, then recomputes FDR v1/v2 + xP v1/v2 exactly like the scheduler does
(scheduler.py runs fdr v1, fdr v2, xp v1, xp v2 in that order).

Usage:
    .venv/bin/python docs/research/calibration/reingest_databank.py [season ...]
    # default seasons: config.yaml databank.seasons

    # On jumbo (CSVs + this script bind-mounted, DB stays in the named volume):
    docker compose --project-directory /opt/fpl-autopilot run --rm -T \
      -v /opt/fpl-autopilot/data/databank:/app/data/databank:ro \
      -v /opt/fpl-autopilot/reingest_databank.py:/app/reingest_databank.py:ro \
      app python /app/reingest_databank.py
"""
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = Path.cwd()  # repo root locally; /app in the container (src is pip-installed there)
sys.path.insert(0, str(REPO_ROOT))

from src.analytics import fdr, xp  # noqa: E402
from src.cli import refresh  # noqa: E402
from src.config import databank_seasons, load_config  # noqa: E402
from src.data.databank_client import BASE_URL, DatabankClient  # noqa: E402
from src.data.db import connect, init_db  # noqa: E402


class DiskDatabankClient(DatabankClient):
    """DatabankClient that reads the per-GW CSVs from disk instead of GitHub."""

    def __init__(self, base_dir):
        super().__init__()
        self._base_dir = Path(base_dir)

    def _get(self, url):
        # url: .../data/{season}/gws/gw{gw}.csv
        parts = url.split("data/")[-1].split("/")
        season, gw = parts[0], parts[-1].removeprefix("gw").removesuffix(".csv")
        path = self._base_dir / season / "gws" / f"gw{gw}.csv"
        if not path.exists():
            resp = requests.Response()
            resp.status_code = 404
            resp.url = url
            raise requests.exceptions.HTTPError(f"404 for {url}", response=resp)
        return path.read_text()


def _sanity_report(conn):
    gw = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    if not gw or gw["gw"] is None:
        print("no upcoming gameweek — sanity report skipped")
        return
    gw = gw["gw"]
    print(f"\n== xP v2 sanity at GW{gw} (model inputs after re-ingest) ==")
    for pos in ("GKP", "DEF", "MID", "FWD"):
        rows = conn.execute(
            """SELECT p.web_name, p.price, x.xp FROM xp x JOIN players p ON p.id=x.player_id
               WHERE x.model_version='v2' AND x.gw=? AND p.position=?
               ORDER BY x.xp DESC LIMIT 3""", (gw, pos)).fetchall()
        print(f"  top {pos}: " + ", ".join(f"{r['web_name']} ({r['price']}m, {r['xp']})" for r in rows))
    print("  premiums: " + ", ".join(
        f"{r['web_name']} ({r['price']}m, {r['xp']})" for r in conn.execute(
            """SELECT p.web_name, p.price, x.xp FROM xp x JOIN players p ON p.id=x.player_id
               WHERE x.model_version='v2' AND x.gw=? AND p.price>=9.0 ORDER BY x.xp DESC""",
            (gw,)).fetchall()))


def main():
    cfg = load_config()
    db_path = Path(os.environ.get("FPL_AUTOPILOT_DB") or cfg["storage"]["db_path"])
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path  # CWD is /app on jumbo, repo root locally
    databank_dir = db_path.parent / "databank"
    seasons = sys.argv[1:] or databank_seasons(cfg)

    conn = connect(str(db_path))
    init_db(conn)
    for season in seasons:
        n = conn.execute("DELETE FROM player_stats WHERE source=?",
                         (f"fpl_databank:{season}",)).rowcount
        print(f"deleted {n} stored rows for {season}")
    conn.commit()

    # Ingest the REQUESTED seasons directly (not cli.refresh, which reads the
    # configured databank.seasons — a season frozen out of config would be
    # deleted here and never re-ingested).
    from src.cli import _refresh_databank_season
    for season in seasons:
        n, unmatched = _refresh_databank_season(
            conn, DiskDatabankClient(databank_dir), season, full=True)
        print(f"re-ingested {season}: {n} rows, {unmatched} unmatched")

    print("recomputing FDR v1/v2 + xP v1/v2 ...")
    fdr.compute_and_store(conn)
    fdr.compute_and_store_v2(conn)
    xp.compute_and_store(conn)
    xp.compute_and_store_v2(conn)
    _sanity_report(conn)
    conn.close()
    print("\ndone.")


if __name__ == "__main__":
    main()
