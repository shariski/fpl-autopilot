"""Top-100 leaders snapshot (v0.27): standings + per-entry histories into the DB.

Runs once per settled GW (settlement-style trigger). B6: schema drift raises
ValueError (never a silent partial write); the caller swallows + logs.
"""
import json
import logging

log = logging.getLogger(__name__)

GLOBAL_LEAGUE_ID = 314
CHIP_NAMES = {"wildcard": "wildcard", "free_hit": "free_hit",
              "bench_boost": "bench_boost", "3xc": "3xc"}


def _unsettled_gw(conn):
    return conn.execute(
        """SELECT MAX(id) AS gw FROM gameweeks
           WHERE finished=1 AND id NOT IN (SELECT DISTINCT gw FROM leader_gw_snapshots)"""
    ).fetchone()["gw"]


def fetch_leader_snapshot(conn, client, pages=2, league_id=GLOBAL_LEAGUE_ID):
    """Fetch standings (top-100) + each entry's history; upsert entries + the
    settled GW's snapshot. Returns (entries_written, snapshots_written)."""
    from . import repository
    gw = _unsettled_gw(conn)
    if gw is None:
        return 0, 0
    standings = []
    for page in range(1, pages + 1):
        payload = client.leagues_classic(league_id, page)
        results = (payload.get("standings") or {}).get("results")
        if not isinstance(results, list):
            raise ValueError("leagues-classic payload missing standings.results (schema drift?)")
        standings.extend(results)
    n_e = n_s = 0
    for r in standings:
        eid = r.get("entry")
        if eid is None:
            continue
        history = client.entry_history(eid)
        current = history.get("current")
        if not isinstance(current, list):
            raise ValueError(f"entry/{eid} history missing current[] (schema drift?)")
        past = (history.get("past") or [{}])[0]
        row = next((x for x in current if x.get("event") == gw), None)
        chip = None
        for c in history.get("chips") or []:
            if c.get("event") == gw and c.get("name") in CHIP_NAMES:
                chip = CHIP_NAMES[c["name"]]
        repository.upsert_leader_entry(
            conn, eid, r.get("player_name"), r.get("entry_name"),
            past.get("rank"), past.get("total_points"), gw,
            r.get("rank"), r.get("total"))
        n_e += 1
        if row is not None:
            repository.upsert_leader_snapshot(
                conn, eid, gw, row.get("points"), row.get("total_points"),
                row.get("overall_rank"), row.get("bank"), row.get("value"),
                row.get("event_transfers"), row.get("event_transfers_cost"), chip)
            n_s += 1
        if repository.leader_picks_stored(conn, eid, gw) is False:
            _fetch_picks(conn, client, eid, gw)
    log.info("leaders.snapshot gw=%s entries=%s snapshots=%s", gw, n_e, n_s)
    return n_e, n_s


def _fetch_picks(conn, client, entry_id, gw):
    """Fetch one leader's picks for `gw` and store them (v0.27 inc2)."""
    from . import repository
    payload = client.entry_picks(entry_id, gw)
    picks = payload.get("picks")
    if not isinstance(picks, list):
        raise ValueError(f"entry/{entry_id} picks payload missing picks[] (schema drift?)")
    captain = vice = None
    for pk in picks:
        if pk.get("is_captain"):
            captain = pk.get("element")
        if pk.get("is_vice_captain"):
            vice = pk.get("element")
    pos_counts = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    for pk in picks:
        pos = pos_map.get(pk.get("position"))
        if pos:
            pos_counts[pos] += 1
    formation = f"{pos_counts['DEF']}-{pos_counts['MID']}-{pos_counts['FWD']}"
    repository.upsert_leader_picks(conn, entry_id, gw, json.dumps(picks, sort_keys=True),
                                   captain, vice, formation)
