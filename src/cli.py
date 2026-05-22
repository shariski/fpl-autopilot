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


def _init_master_password_cli(salt_path=None, verify_path=None):
    import getpass
    from .auth import master
    kw = {}
    if salt_path is not None:
        kw["salt_path"] = salt_path
    if verify_path is not None:
        kw["verify_path"] = verify_path
    if master.is_initialized(**kw):
        if input("Master password already set. Overwrite (orphans existing creds)? [y/N]: ").strip().lower() != "y":
            print("Aborted.")
            return
    pw = getpass.getpass("Enter master password (min 12 chars): ")
    if len(pw) < 12:
        print("Password too short (min 12 characters).")
        return
    if pw != getpass.getpass("Confirm master password: "):
        print("Passwords do not match. Aborted.")
        return
    master.init_master_password(pw, **kw)
    print("Master password set; salt + verification token written.")
    print("IMPORTANT: this password is UNRECOVERABLE. Store it in your password manager NOW.")
    print("If lost, stored credentials become unreadable and you must re-run init-fpl after a reset.")


def _init_fpl_cli(conn=None, login_fn=None, salt_path=None, verify_path=None):
    import os
    import json
    import getpass
    from .auth import master, crypto, fpl_login
    from .data import repository
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    email = os.getenv("FPL_EMAIL") or input("FPL email: ")
    password = os.getenv("FPL_PASSWORD") or getpass.getpass("FPL password: ")
    login_fn = login_fn or fpl_login.login
    try:
        result = login_fn(email, password, expected_team_id=cfg_team_id())
    except fpl_login.FPLLoginError as exc:
        print(f"FPL login failed: {exc}")
        return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    repository.set_encrypted(conn, "fpl_email_encrypted", crypto.encrypt(key, email))
    repository.set_encrypted(conn, "fpl_password_encrypted", crypto.encrypt(key, password))
    repository.set_encrypted(conn, "session_cookie_encrypted",
                             crypto.encrypt(key, json.dumps(result.cookies)))
    repository.set_encrypted(conn, "csrf_token_encrypted", crypto.encrypt(key, result.csrf or ""))
    repository.touch_session_refreshed(conn)
    if owns_conn:
        conn.close()
    print(f"Authenticated as entry {result.entry_id}; session stored.")


def serve(host="0.0.0.0", port=None, scheduler=True):
    import os
    import uvicorn
    port = port or int(os.getenv("PORT", "8000"))
    sched = None
    if scheduler:
        from .scheduler import build_scheduler
        sched = build_scheduler()
        sched.start()
    try:
        uvicorn.run("src.interface.api:app", host=host, port=port)
    finally:
        if sched is not None:
            sched.shutdown(wait=False)


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
    p_serve.add_argument("--no-scheduler", action="store_true",
                         help="run the API without the background scheduler")
    sub.add_parser("scheduler", help="run the background refresh scheduler (blocking)")
    sub.add_parser("init-master-password", help="set the master password that encrypts stored credentials")
    sub.add_parser("init-fpl", help="log in to FPL and store the encrypted session")
    args = parser.parse_args(argv)
    if args.command == "refresh":
        sources = (args.source,) if args.source else ("fpl", "understat")
        refresh(full=args.full, sources=sources)
    elif args.command == "serve":
        serve(host=args.host, port=args.port, scheduler=not args.no_scheduler)
    elif args.command == "scheduler":
        from .scheduler import run_scheduler_blocking
        run_scheduler_blocking()
    elif args.command == "init-master-password":
        _init_master_password_cli()
    elif args.command == "init-fpl":
        _init_fpl_cli()


if __name__ == "__main__":
    main()
