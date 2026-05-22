import argparse
import pathlib
import yaml
from .config import load_config, team_id as cfg_team_id, db_path as cfg_db_path
from .data.db import connect, init_db
from .data.fpl_client import FPLClient
from .data.understat_client import UnderstatClient
from .data import repository, cache, name_resolver

NAME_RESOLUTION_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "name_resolution.yaml"


def _current_gw_from_db(conn):
    row = conn.execute("SELECT id FROM gameweeks WHERE is_current=1").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks WHERE finished=1").fetchone()
    if row and row["id"] is not None:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks").fetchone()
    return row["id"] if row else None


def _load_name_overrides():
    if not NAME_RESOLUTION_PATH.exists():
        return {}
    data = yaml.safe_load(NAME_RESOLUTION_PATH.read_text()) or {}
    if not isinstance(data, dict):  # a list/other shape -> treat as no overrides
        return {}
    return {str(k): int(v) for k, v in data.items()}


def _refresh_fpl(conn, client, tid, full):
    if full or cache.is_stale(conn, "bootstrap-static"):
        bs = client.bootstrap_static()
        repository.upsert_teams(conn, bs.teams)
        repository.upsert_players(conn, bs.elements, bs.element_types)
        repository.upsert_gameweeks(conn, bs.events)
        cache.mark_fetched(conn, "bootstrap-static")
        print(f"bootstrap-static OK ({len(bs.elements)} players, {len(bs.teams)} teams)")
    if full or cache.is_stale(conn, "fixtures"):
        fx = client.fixtures()
        repository.upsert_fixtures(conn, fx)
        cache.mark_fetched(conn, "fixtures")
        print(f"fixtures OK ({len(fx)} fixtures)")
    gw = _current_gw_from_db(conn)
    if gw is not None and (full or cache.is_stale(conn, "my_team")):
        picks = client.picks(tid, gw)
        repository.snapshot_my_team(conn, gw, picks)
        cache.mark_fetched(conn, "my_team")
        print(f"my_team OK (GW{gw}, {len(picks.picks)} picks)")


def _refresh_understat(conn, understat_client, cfg, full):
    # Supplementary data: a failure must NOT break the FPL refresh (R2).
    try:
        if not (full or cache.is_stale(conn, "understat")):
            return
        season = cfg.get("understat", {}).get("season", "2025")
        resp = understat_client.players_stats(season)
        fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
        fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
        res = name_resolver.resolve_players(fpl_players, fpl_teams, resp.players, _load_name_overrides())
        repository.upsert_understat_players(conn, resp.players, res, season)
        cache.mark_fetched(conn, "understat")
        print(f"understat OK (matched {len(res.matched)}/{len(resp.players)}, "
              f"{len(res.unmatched)} unmatched, {len(res.unmapped_teams)} unmapped teams)")
    except Exception as exc:  # noqa: BLE001 - supplementary source degrades gracefully
        print(f"WARNING: understat refresh failed ({exc}); keeping last data")


def refresh(full=False, cfg=None, conn=None, client=None, understat_client=None, sources=None):
    cfg = cfg or load_config()
    if sources is None:  # explicit: an empty tuple means "no sources", not "both"
        sources = ("fpl", "understat")
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)

    if "fpl" in sources:
        _refresh_fpl(conn, client or FPLClient(), cfg_team_id(cfg), full)
    if "understat" in sources:
        _refresh_understat(conn, understat_client or UnderstatClient(), cfg, full)

    if owns_conn:
        conn.close()


def serve(host="0.0.0.0", port=None):
    import os
    import uvicorn
    port = port or int(os.getenv("PORT", "8000"))
    uvicorn.run("src.interface.api:app", host=host, port=port)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fpl-autopilot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_refresh = sub.add_parser("refresh", help="fetch FPL + Understat data into the local DB")
    p_refresh.add_argument("--full", action="store_true", help="ignore cache, fetch everything")
    p_refresh.add_argument("--source", choices=["fpl", "understat"], default=None,
                           help="restrict to one source (default: both)")
    p_serve = sub.add_parser("serve", help="run the FastAPI server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "refresh":
        sources = (args.source,) if args.source else ("fpl", "understat")
        refresh(full=args.full, sources=sources)
    elif args.command == "serve":
        serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
