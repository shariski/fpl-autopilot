"""Settlement: backfill actual GW points into player_gw_stats from FPL's event/{id}/live/.

Runs on the hourly refresh after the existing xp recompute. Idempotent — a settled GW is
detected by its presence in player_gw_stats and skipped on subsequent runs. Per-GW failures are
swallowed and logged; one bad GW does not block the others.
"""
import logging

log = logging.getLogger(__name__)


def settlement_run(conn, client):
    """For each finished GW that hasn't been settled yet, fetch live data and write player_gw_stats.

    Returns the total rows written (inserted + backfilled) across all GWs in this run.
    """
    from . import repository

    unsettled_gws = [r["id"] for r in conn.execute(
        """SELECT id FROM gameweeks
           WHERE finished=1
             AND id NOT IN (SELECT DISTINCT gw FROM player_gw_stats)
           ORDER BY id""")]

    total_written = 0
    for gw in unsettled_gws:
        try:
            payload = client.event_live(gw)
            written = repository.upsert_player_gw_stats(conn, gw, payload)
            total_written += written
        except Exception:
            log.exception(f"settlement failed for gw={gw}")

    # v0.23: backfill the full stat set for GWs settled before the columns existed
    # (GW1 heals on the first refresh after deploy). Per-GW failures are isolated.
    for gw in [r["gw"] for r in conn.execute(
            "SELECT DISTINCT gw FROM player_gw_stats WHERE starts IS NULL ORDER BY gw")]:
        try:
            payload = client.event_live(gw)
            n = repository.backfill_player_gw_stats(conn, gw, payload)
            total_written += n
            log.info("settlement.backfill gw=%s rows=%s", gw, n)
        except Exception:
            log.exception(f"settlement backfill failed for gw={gw}")
    return total_written
