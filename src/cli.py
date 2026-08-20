import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
import yaml
import requests
from . import config
from .config import load_config, team_id as cfg_team_id, db_path as cfg_db_path
from .data.db import connect, init_db
from .data.fpl_client import FPLClient
from .data.understat_client import UnderstatClient
from .data import repository, cache, name_resolver

NAME_RESOLUTION_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "name_resolution.yaml"


def _print_json(payload):
    print(json.dumps(payload, default=str))


def _json_ok(command, data):
    _print_json({"ok": True, "contract_version": "1", "command": command,
                 "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                 "data": data})


def _json_err(command, code, message, hint=None, exit_code=1):
    error = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    _print_json({"ok": False, "contract_version": "1", "command": command,
                 "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                 "error": error})
    raise SystemExit(exit_code)


def _data_basis(conn, cfg):
    fresh = conn.execute("SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()
    return {"as_of_utc": fresh["m"] if fresh else None,
            "xp_model_version": cfg.get("xp_model", {}).get("version", "v2")}


def _current_gw_from_db(conn):
    row = conn.execute("SELECT id FROM gameweeks WHERE is_next=1").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT id FROM gameweeks WHERE is_current=1").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks WHERE finished=1").fetchone()
    if row and row["id"] is not None:
        return row["id"]
    row = conn.execute("SELECT MAX(id) AS id FROM gameweeks").fetchone()
    return row["id"] if row else None


def _status_data(conn, cfg, limit_actions=5):
    from .execution import override
    cur = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_current=1").fetchone()
    nxt = conn.execute("SELECT id, deadline_utc, state FROM gameweeks WHERE is_next=1").fetchone()
    resources = ["bootstrap-static", "fixtures", "my_team", "understat"]
    freshness = {r: None for r in resources}
    for row in conn.execute(
            "SELECT resource, last_fetched_utc FROM cache_meta "
            "WHERE resource IN (%s)" % ",".join("?" * len(resources)), resources).fetchall():
        freshness[row["resource"]] = row["last_fetched_utc"]
    frozen = override.status(conn)
    auth_state = repository.get_auth_state(conn)
    now = datetime.now(timezone.utc)
    nxt_gw = None
    if nxt is not None:
        hours = None
        if nxt["deadline_utc"]:
            hours = round((datetime.fromisoformat(nxt["deadline_utc"]) - now).total_seconds() / 3600, 1)
        nxt_gw = {"id": nxt["id"], "deadline_utc": nxt["deadline_utc"],
                  "state": nxt["state"], "hours_until_deadline": hours}
    pending = [dict(r) for r in conn.execute(
        "SELECT decision_type, summary, created_at FROM pending_decisions "
        "WHERE status='pending' ORDER BY created_at")]
    actions = [dict(r) for r in conn.execute(
        "SELECT ts_utc, gw, mode, decision_type, action_taken, executed "
        "FROM activity_log ORDER BY id DESC LIMIT ?", (limit_actions,))]
    auth = None
    if auth_state is not None:
        row = conn.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
        auth = {"state": auth_state,
                "access_token_expires_at": repository.get_access_expiry(conn),
                "session_last_refreshed": row["session_last_refreshed"] if row else None,
                "relogin_failures": repository.get_relogin_failures(conn)}
    n_players = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    n_teams = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    return {
        "mode": cfg.get("mode", {}).get("current", "manual"),
        "frozen": ({"is_frozen": True, **frozen}) if frozen else {"is_frozen": False},
        "auth": auth,
        "data_freshness": freshness,
        "current_gameweek": {"id": cur["id"], "deadline_utc": cur["deadline_utc"]} if cur else None,
        "next_gameweek": nxt_gw,
        "pending_decisions": pending,
        "last_system_actions": actions,
        "health": {"db_ok": True, "players": n_players, "teams": n_teams},
        "data_basis": _data_basis(conn, cfg),
    }


def _operating_rules():
    return {
        "agent_never_live": "Agent sessions must never pass --live. All FPL writes are "
                            "human-only (R3); --live refuses non-TTY stdin.",
        "dry_run_default": "Every contract command is read-only or local-DB-only; "
                           "nothing writes to FPL.",
        "boot_ritual": ["resume --json — boot context",
                        "refresh --json — pull latest data when stale",
                        "captain/transfers/chips/squad --json — decision inputs",
                        "insight <player_id> --json / speculate --json — player analysis",
                        "propose a plan; the human executes writes (--live) via the CLI"],
        "agent_safe_commands": ["status", "resume", "log", "captain", "transfers", "chips",
                                "squad", "insight", "speculate", "refresh",
                                "freeze-status", "auth-status", "review"],
        "human_only_commands": ["execute-lineup", "execute-transfer", "apply-squad",
                                "route-gameweek", "undo-transfer", "refresh-my-team",
                                "init-master-password", "init-fpl", "freeze", "unfreeze"],
    }


def _activity_entries(conn, limit, *, gw=None, mode=None, decision_type=None):
    sql = ("SELECT ts_utc, gw, mode, decision_type, action_taken, executed, "
           "exec_outcome_json FROM activity_log")
    clauses, params = [], []
    if gw is not None:
        clauses.append("gw = ?")
        params.append(gw)
    if mode is not None:
        clauses.append("mode = ?")
        params.append(mode)
    if decision_type is not None:
        clauses.append("decision_type = ?")
        params.append(decision_type)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    entries = []
    for r in conn.execute(sql, params).fetchall():
        e = {"ts_utc": r["ts_utc"], "gw": r["gw"], "mode": r["mode"],
             "decision_type": r["decision_type"], "action_taken": r["action_taken"],
             "executed": bool(r["executed"])}
        outcome = r["exec_outcome_json"]
        if outcome is not None:
            try:
                e["outcome"] = json.loads(outcome)
            except ValueError:
                e["outcome"] = {"raw": outcome}
        entries.append(e)
    return entries


def _load_name_overrides():
    if not NAME_RESOLUTION_PATH.exists():
        return {}
    data = yaml.safe_load(NAME_RESOLUTION_PATH.read_text()) or {}
    if not isinstance(data, dict):  # a list/other shape -> treat as no overrides
        return {}
    return {str(k): int(v) for k, v in data.items()}


def _refresh_fpl(conn, client, tid, full):
    out = {"bootstrap_static": None, "fixtures": None,
           "my_team": None, "my_team_skipped": None}
    if full or cache.is_stale(conn, "bootstrap-static"):
        bs = client.bootstrap_static()
        repository.upsert_teams(conn, bs.teams)
        repository.upsert_players(conn, bs.elements, bs.element_types)
        repository.upsert_gameweeks(conn, bs.events)
        cache.mark_fetched(conn, "bootstrap-static")
        out["bootstrap_static"] = {"players": len(bs.elements), "teams": len(bs.teams)}
    if full or cache.is_stale(conn, "fixtures"):
        fx = client.fixtures()
        repository.upsert_fixtures(conn, fx)
        cache.mark_fetched(conn, "fixtures")
        out["fixtures"] = len(fx)
    gw = _current_gw_from_db(conn)
    if gw is not None and (full or cache.is_stale(conn, "my_team")):
        try:
            picks = client.picks(tid, gw)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                out["my_team_skipped"] = gw
                return out
            raise
        repository.snapshot_my_team(conn, gw, picks)
        cache.mark_fetched(conn, "my_team")
        out["my_team"] = {"gw": gw, "picks": len(picks.picks)}
    return out


def _refresh_understat(conn, understat_client, cfg, full, report=False):
    # Supplementary data: a failure must NOT break the FPL refresh (R2).
    try:
        if not (full or cache.is_stale(conn, "understat")):
            return None
        season = cfg.get("understat", {}).get("season", "2025")
        resp = understat_client.players_stats(season)
        fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
        fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
        res = name_resolver.resolve_players(fpl_players, fpl_teams, resp.players, _load_name_overrides())
        repository.upsert_understat_players(conn, resp.players, res, season)
        cache.mark_fetched(conn, "understat")
        return {"total": len(resp.players), "matched": len(res.matched),
                "unmatched": len(res.unmatched), "unmapped_teams": len(res.unmapped_teams)}
    except Exception as exc:  # noqa: BLE001 - supplementary source degrades gracefully
        if report:
            return {"warning": str(exc)}
        print(f"WARNING: understat refresh failed ({exc}); keeping last data")
        return None


def _rematch_prior_understat(conn, current_season, overrides=None):
    """Re-link stored understat rows from earlier seasons to the current players table.

    FPL player ids change every season: a row stored under 25/26 ids silently points
    at the wrong player once the players table is replaced at rollover (observed
    2026-08-14: Lewis Hall's digest showed Bruno Fernandes' 9G/21A). Re-resolve
    name+team against the live roster; unmatchable rows are nulled, never left
    dangling (B6: wrong data is worse than no data).
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT understat_id, player_name, team_title FROM understat_players "
        "WHERE season != ?", (current_season,))]
    if not rows:
        return 0
    fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
    if not fpl_players:
        return 0
    import types
    ups = [types.SimpleNamespace(id=r["understat_id"], player_name=r["player_name"],
                                 team_title=r["team_title"]) for r in rows]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, ups,
                                        overrides or _load_name_overrides())
    n = 0
    for uid, pid in res.matched.items():
        conn.execute("UPDATE understat_players SET fpl_player_id=? WHERE understat_id=?",
                     (pid, uid))
        n += 1
    unmatched_ids = [u.id for u in res.unmatched]
    if unmatched_ids:
        conn.execute(
            "UPDATE understat_players SET fpl_player_id=NULL WHERE understat_id IN (%s)"
            % ",".join("?" * len(unmatched_ids)), unmatched_ids)
    conn.commit()
    return n


def _clear_stale_season_rows(conn):
    """Season rollover hygiene: drop rows keyed by last season's player ids.

    player_gw_stats and my_team carry no season column — their player_id/gw keys
    only mean something while the players table belongs to the same season.
    Observed 2026-08-14: Lewis Hall's digest showed Bruno Fernandes' 25/26 points.

    player_gw_stats: rows settled before the current season's GW1 deadline are
    previous-season data (settlement re-populates them fresh as the season runs).

    my_team: the timestamp test alone is NOT enough — a snapshot fetched after the
    new-season bootstrap but before GW1 is current data (2026-08-16: the pre-season
    apply-squad flow lost its snapshot on every refresh). A row is stale only if it
    references a player id that no longer exists in the players table (dead ids).
    """
    row = conn.execute("SELECT MIN(deadline_utc) AS d FROM gameweeks WHERE id=1").fetchone()
    if row is None or row["d"] is None:
        return 0, 0
    gw1_deadline = row["d"]
    n_gw = conn.execute("DELETE FROM player_gw_stats WHERE settled_at < ?",
                        (gw1_deadline,)).rowcount
    n_team = conn.execute(
        """DELETE FROM my_team
           WHERE snapshot_at < ?
             AND ((picks_json IS NULL OR picks_json = '[]')
                  OR EXISTS (SELECT 1 FROM json_each(my_team.picks_json) j
                             WHERE CAST(j.value->>'element' AS INTEGER)
                                   NOT IN (SELECT id FROM players)))""",
        (gw1_deadline,)).rowcount
    conn.commit()
    return n_gw, n_team


def _remap_databank_elements(conn, rows):
    """Re-point databank rows at current player ids by NAME — never by element id.

    FPL reuses element ids across seasons/roster versions: a 25-26 CSV "element"
    usually belongs to a DIFFERENT player today (regression: 'Cole Palmer' 25-26
    element 235 landed on today's id 235 = Aznou). Every row is matched by name
    (team-agnostic, so club-movers still match); the element id is accepted only
    when its current holder's web_name and team corroborate the CSV name. Rows
    that match neither are dropped — never mis-assigned (B6).
    """
    from .data import name_resolver as nr
    if not rows:
        return rows, 0
    fpl_players = [dict(r) for r in conn.execute(
        "SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name FROM teams")]
    if not fpl_players:
        return rows, len(rows)
    overrides = _load_name_overrides()
    known = {p["id"] for p in fpl_players}
    team_by_id = {t["id"]: t["name"] for t in fpl_teams}
    by_name = {p["id"]: (set(nr._norm(p["name"] or "").split()),
                         set(nr._norm(p["web_name"] or "").split())) for p in fpl_players}

    out, dropped = [], 0
    for r in rows:
        if r["element"] in overrides:
            row = dict(r)
            row["element"] = overrides[r["element"]]
            out.append(row)
            continue
        u = set(nr._norm(r["name"]).split())
        hits = [pid for pid, (full, _web) in by_name.items()
                if full and (u <= full or full <= u)]
        pid = None
        if len(hits) == 1:
            pid = hits[0]
        elif r["element"] in known:
            holder = next(p for p in fpl_players if p["id"] == r["element"])
            _full, web = by_name[r["element"]]
            if (web and web <= u
                    and nr._norm(team_by_id[holder["team_id"]]) == nr._norm(r["team"])):
                pid = r["element"]
        if pid is None:
            dropped += 1
            continue
        row = dict(r)
        row["element"] = pid
        out.append(row)
    return out, dropped


def _refresh_databank_season(conn, databank_client, season, full):
    """Fetch missing GWs of one databank season. 404 = GW not published: cooldown, continue."""
    from .data import cache
    from .data import repository as repo
    fetched = 0
    unmatched_total = 0
    for gw in range(1, 39):
        resource = f"databank:{season}:gw{gw}"
        if not full and not cache.is_stale(conn, resource):
            continue
        try:
            rows = databank_client.fetch_gw(season, gw)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                cache.mark_fetched(conn, resource)  # not published yet; cooldown
                continue
            raise
        rows, unmatched = _remap_databank_elements(conn, rows)
        unmatched_total += unmatched
        fetched += repo.upsert_databank_stats(conn, season, gw, rows)
        cache.mark_fetched(conn, resource)
    return fetched, unmatched_total


def _refresh_databank(conn, databank_client, cfg, full, report=False):
    # Supplementary source: a failure must NOT break the FPL refresh (R2).
    try:
        fetched = {}
        for season in config.databank_seasons(cfg):
            n, unmatched = _refresh_databank_season(conn, databank_client, season, full)
            if n or unmatched:
                fetched[season] = {"rows": n, "unmatched": unmatched}
        return fetched or None
    except Exception as exc:  # noqa: BLE001 - supplementary source degrades gracefully
        if report:
            return {"warning": str(exc)}
        print(f"WARNING: databank refresh failed ({exc}); keeping last data")
        return None


def refresh(full=False, cfg=None, conn=None, client=None, understat_client=None,
            databank_client=None, sources=None, report=False):
    cfg = cfg or load_config()
    if sources is None:  # explicit: an empty tuple means "no sources", not "all"
        sources = ("fpl", "understat", "databank")
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)

    fpl_out = {}
    if "fpl" in sources:
        fpl_out = _refresh_fpl(conn, client or FPLClient(), cfg_team_id(cfg), full)
        if not report:
            bs = fpl_out["bootstrap_static"]
            if bs:
                print(f"bootstrap-static OK ({bs['players']} players, {bs['teams']} teams)")
            if fpl_out["fixtures"] is not None:
                print(f"fixtures OK ({fpl_out['fixtures']} fixtures)")
            if fpl_out["my_team"] is not None:
                print(f"my_team OK (GW{fpl_out['my_team']['gw']}, "
                      f"{fpl_out['my_team']['picks']} picks)")
            elif fpl_out["my_team_skipped"] is not None:
                print(f"my_team skipped: no squad saved yet for "
                      f"GW{fpl_out['my_team_skipped']} (404)")
    understat_out = None
    if "understat" in sources:
        understat_out = _refresh_understat(conn, understat_client or UnderstatClient(),
                                           cfg, full, report=report)
        if not report and understat_out is not None and "warning" not in understat_out:
            print(f"understat OK (matched {understat_out['matched']}/{understat_out['total']}, "
                  f"{understat_out['unmatched']} unmatched, "
                  f"{understat_out['unmapped_teams']} unmapped teams)")

    databank_out = None
    if "databank" in sources:
        from .data.databank_client import DatabankClient
        databank_out = _refresh_databank(conn, databank_client or DatabankClient(),
                                         cfg, full, report=report)
        if not report and databank_out is not None and "warning" not in databank_out:
            print("databank OK (" + ", ".join(
                f"{s}: {v['rows']} rows, {v['unmatched']} unmatched"
                for s, v in databank_out.items()) + ")")

    # Season rollover: re-link prior-season understat rows to the current players
    # table (player ids change every season; stale pointers silently feed the wrong
    # player's stats into xP and insights). Always runs — also on fpl-only refreshes.
    rematch = 0
    cleanup = {"gw_stats": 0, "my_team": 0}
    warnings = []
    try:
        current_season = cfg.get("understat", {}).get("season", "2025")
        rematch = _rematch_prior_understat(conn, current_season)
        if rematch and not report:
            print(f"understat prior rematch: {rematch} rows re-linked to current player ids")
    except Exception as exc:  # noqa: BLE001 - data hygiene must not break refresh
        if report:
            warnings.append(f"understat prior rematch failed ({exc})")
        else:
            print(f"WARNING: understat prior rematch failed ({exc})")
    try:
        n_gw, n_team = _clear_stale_season_rows(conn)
        cleanup = {"gw_stats": n_gw, "my_team": n_team}
        if (n_gw or n_team) and not report:
            print(f"season rollover cleanup: {n_gw} gw_stats rows, "
                  f"{n_team} my_team rows cleared")
    except Exception as exc:  # noqa: BLE001 - data hygiene must not break refresh
        if report:
            warnings.append(f"season rollover cleanup failed ({exc})")
        else:
            print(f"WARNING: season rollover cleanup failed ({exc})")

    if owns_conn:
        conn.close()
    if report:
        return {"fpl": fpl_out, "understat": understat_out, "databank": databank_out,
                "rematch": rematch, "cleanup": cleanup, "warnings": warnings}


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


def _init_fpl_cli(conn=None, salt_path=None, verify_path=None, refresh_session=None, me_session=None):
    import os
    import requests
    from datetime import datetime, timezone, timedelta
    from .auth import master, session as auth_session
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    refresh_token = os.getenv("FPL_REFRESH_TOKEN") or input("Paste FPL refresh token: ")
    try:
        tok = auth_session.refresh_access_token(refresh_token, session=refresh_session)
        entry = auth_session.validate_token(tok["access_token"], expected_team_id=cfg_team_id(), session=me_session)
    except auth_session.TokenRefreshError as exc:
        print(f"Refresh token rejected: {exc}")
        return
    except auth_session.SessionValidationError as exc:
        print(f"Token rejected: {exc}")
        return
    except requests.RequestException:
        print("Couldn't reach FPL; check your connection.")
        return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 28800)))
    auth_session.store_tokens(conn, key, refresh_token=tok.get("refresh_token") or refresh_token,
                              access_token=tok["access_token"], expires_at=expires_at)
    if owns_conn:
        conn.close()
    print(f"Authenticated as entry {entry}; session stored.")


def _auth_status_cli(conn=None):
    from .data import repository
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    state = repository.get_auth_state(conn)
    if state is None:
        print("No stored FPL session — run `fpl-autopilot init-fpl`.")
    else:
        row = conn.execute(
            "SELECT session_last_refreshed FROM credentials WHERE id=1"
        ).fetchone()
        print(f"auth_state: {state}")
        print(f"access_token_expires_at: {repository.get_access_expiry(conn)}")
        print(f"session_last_refreshed: {row['session_last_refreshed']}")
    from .execution import override
    fr = override.status(conn)
    print(f"frozen: {('yes (' + fr['source'] + ') — ' + fr['reason']) if fr else 'no'}")
    print(f"relogin_failures: {repository.get_relogin_failures(conn)}")
    if owns_conn:
        conn.close()


def _freeze_cli(*, reason="frozen from CLI", conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    override.freeze(conn, reason=reason, source="user")
    print("🛑 Frozen — autonomous execution (auto + deadguard) halted.")
    if owns:
        conn.close()


def _unfreeze_cli(conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    override.unfreeze(conn, source="user")
    print("▶️ Unfrozen — autonomous execution resumed.")
    if owns:
        conn.close()


def _freeze_status_cli(conn=None):
    from .execution import override
    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    st = override.status(conn)
    if st is None:
        print("not frozen")
    else:
        print(f"FROZEN since {st['since']} (source: {st['source']}) — {st['reason']}")
    if owns:
        conn.close()


def _print_status_text(data):
    nxt = data["next_gameweek"]
    print(f"mode: {data['mode']} | frozen: {data['frozen']['is_frozen']}")
    if nxt:
        print(f"next GW: {nxt['id']} (deadline {nxt['deadline_utc']}, "
              f"{nxt['hours_until_deadline']}h)")
    print(f"data fresh as of: {data['data_basis']['as_of_utc']}")
    print(f"pending decisions: {len(data['pending_decisions'])}")


def _cmd_status_cli(conn=None, cfg=None, json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = _status_data(conn, cfg)
        if json_out:
            _json_ok("status", data)
        else:
            _print_status_text(data)
    finally:
        if owns:
            conn.close()


def _cmd_resume_cli(conn=None, cfg=None, tail=10, json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = _status_data(conn, cfg)
        data["activity"] = {"entries": _activity_entries(conn, tail)}
        data["operating_rules"] = _operating_rules()
        if json_out:
            _json_ok("resume", data)
        else:
            _print_status_text(data)
            for e in data["activity"]["entries"]:
                print(f"  {e['ts_utc']} GW{e['gw']} [{e['mode']}] {e['decision_type']}: "
                      f"{e['action_taken']} ({'done' if e['executed'] else 'skip'})")
    finally:
        if owns:
            conn.close()


def _cmd_log_cli(conn=None, cfg=None, tail=10, gw=None, mode=None, decision_type=None,
                 json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        entries = _activity_entries(conn, tail, gw=gw, mode=mode,
                                    decision_type=decision_type)
        if json_out:
            _json_ok("log", {"entries": entries})
        else:
            for e in entries:
                print(f"{e['ts_utc']} GW{e['gw']} [{e['mode']}] {e['decision_type']}: "
                      f"{e['action_taken']} ({'done' if e['executed'] else 'skip'})")
    finally:
        if owns:
            conn.close()


def _cmd_captain_cli(conn=None, cfg=None):
    from .interface.queries import get_captain_picks
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_captain_picks(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("captain", data)
    finally:
        if owns:
            conn.close()


def _cmd_transfers_cli(conn=None, cfg=None):
    from .interface.queries import get_transfer_suggestions
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_transfer_suggestions(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("transfers", data)
    finally:
        if owns:
            conn.close()


def _cmd_chips_cli(conn=None, cfg=None):
    from .interface.queries import get_chip_recommendation
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_chip_recommendation(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("chips", data)
    finally:
        if owns:
            conn.close()


def _cmd_freeze_status_cli(conn=None, cfg=None, json_out=False):
    from .execution import override
    if not json_out:
        return _freeze_status_cli(conn=conn)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        frozen = override.status(conn)
        data = {"frozen": ({"is_frozen": True, **frozen}) if frozen else {"is_frozen": False}}
        _json_ok("freeze-status", data)
    finally:
        if owns:
            conn.close()


def _cmd_auth_status_cli(conn=None, cfg=None, json_out=False):
    from .data import repository
    if not json_out:
        return _auth_status_cli(conn=conn)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        state = repository.get_auth_state(conn)
        auth = None
        if state is not None:
            row = conn.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
            auth = {"state": state,
                    "access_token_expires_at": repository.get_access_expiry(conn),
                    "session_last_refreshed": row["session_last_refreshed"] if row else None,
                    "relogin_failures": repository.get_relogin_failures(conn)}
        _json_ok("auth-status", {"auth": auth})
    finally:
        if owns:
            conn.close()


def _cmd_squad_cli(conn=None, cfg=None, candidates_only=False):
    from .ai import cache as ai_cache
    from .ai.squad import runner as squad_runner
    from .decisions.squad_builder import build_candidate_pool
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        pool = build_candidate_pool(conn)
        if not pool:
            _json_err("squad", "E_NO_DATA", "no upcoming gameweek with xP data",
                      "run refresh --json first")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"] if nxt else None
        if candidates_only:
            _json_ok("squad", {"gw": gw, "count": len(pool), "pool": pool,
                               "data_basis": _data_basis(conn, cfg)})
            return
        if not config.ai_enabled():
            _json_err("squad", "E_RUNTIME", "AI disabled (config ai.enabled=false)",
                      "squad --json requires the AI squad builder")
        digest = squad_runner.build_squad_digest(conn, pool=pool)
        rec_hash = ai_cache.recommendation_hash(digest)
        hit = ai_cache.get(conn, gw, squad_runner.PANE_TYPE, rec_hash)
        if hit is not None:
            result = squad_runner.extract_json_object(hit["prose"])
            status = "cached"
        else:
            result = squad_runner.generate_squad(
                conn, provider=_ai_provider_or_err(
                    "squad", "check AI provider config and retry"),
                model_id=config.ai_deepseek_model())
            if result is None:
                _json_err("squad", "E_RUNTIME",
                          "squad builder gate rejected or provider failed",
                          "check AI provider config and retry")
            status = "generated"
        by_id = {p["player_id"]: p for p in pool}
        picks = []
        for pk in result["picks"]:
            p = by_id.get(pk["player_id"])
            if p is None:
                continue
            picks.append({"player_id": pk["player_id"], "web_name": p["web_name"],
                          "team": p["team_short"], "position": p["position"],
                          "price": p["price"], "xp_6gw": p["xp_6gw"],
                          "slot": pk["slot"], "reason": pk.get("reason", "")})
        budget_used = round(sum(p["price"] for p in picks), 1)
        spec = result.get("speculation")
        if spec:
            for kind in ("spikes", "drops", "differentials"):
                for s in spec.get(kind, []):
                    p = by_id.get(s["player_id"])
                    s["web_name"] = p["web_name"] if p else f"#{s['player_id']}"
                    s["team"] = p["team_short"] if p else None
        _json_ok("squad", {
            "status": status, "gw": gw, "source": result.get("source", "ai"),
            "picks": picks, "template_rationale": result.get("template_rationale", ""),
            "risks": result.get("risks", []), "budget_used": budget_used,
            "speculation": spec, "model_id": config.ai_deepseek_model(),
            "generated_at": hit["generated_at"] if hit is not None else None,
            "data_basis": _data_basis(conn, cfg),
        })
    finally:
        if owns:
            conn.close()


def _cmd_speculate_cli(conn=None, cfg=None):
    from .ai.squad import spikes
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        signals = spikes.generate_spike_signals(
            conn, provider=_ai_provider_or_err(
                "speculate", "retry later; the squad builder runs without speculation"),
            model_id=config.ai_deepseek_model())
        if signals is None:
            _json_err("speculate", "E_RUNTIME",
                      "speculation unavailable (provider error or gate rejected)",
                      "retry later; the squad builder runs without speculation")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"] if nxt else None
        in_squad = set()
        snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
        if snap is not None:
            in_squad = {pk["element"] for pk in json.loads(snap["picks_json"])}
        differentials = [s for s in signals.get("spikes", [])
                         if s["player_id"] not in in_squad]
        _json_ok("speculate", {"gw": gw, "signals": signals,
                               "differentials": differentials,
                               "data_basis": _data_basis(conn, cfg)})
    finally:
        if owns:
            conn.close()


def _player_identity(conn, player_id):
    row = conn.execute(
        "SELECT p.name, p.web_name, p.position, p.price, t.short_name AS team "
        "FROM players p JOIN teams t ON t.id = p.team_id WHERE p.id=?",
        (player_id,)).fetchone()
    if row is None:
        return None
    return {"name": row["name"], "web_name": row["web_name"],
            "position": row["position"], "team": row["team"], "price": row["price"]}


def _cmd_insight_cli(player_id, conn=None, cfg=None):
    from .ai import cache as ai_cache
    from .ai.insight import runner as insight_runner
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        exists = conn.execute("SELECT id FROM players WHERE id=?", (player_id,)).fetchone()
        if exists is None:
            _json_err("insight", "E_NO_DATA", f"unknown player {player_id}",
                      "look up player ids via squad --candidates --json")
        if not config.ai_enabled():
            _json_err("insight", "E_RUNTIME", "AI disabled (config ai.enabled=false)")
        digest = insight_runner.build_player_digest(conn, player_id)
        if digest is None:
            _json_err("insight", "E_NO_DATA", f"no digest for player {player_id}",
                      "run refresh --json first")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"]
        rec_hash = ai_cache.recommendation_hash(digest)
        hit = ai_cache.get(conn, gw, insight_runner.PANE_TYPE, rec_hash)
        if hit is not None:
            payload = insight_runner.extract_json_object(hit["prose"])
            status = "cached"
        else:
            payload = insight_runner.generate_player_insight(
                conn, player_id, provider=_ai_provider_or_err(
                    "insight", "retry later"),
                model_id=config.ai_deepseek_model())
            if payload is None:
                _json_err("insight", "E_RUNTIME",
                          "provider error or quality gate rejected",
                          "retry later")
            status = "generated"
        data = {
            "status": status, "player_id": player_id, "gw": gw,
            "player": _player_identity(conn, player_id),
            "insights": payload.get("insights", []),
            "summary": payload.get("summary", ""),
            "data_limits": payload.get("data_limits", []),
            "model_id": config.ai_deepseek_model(),
            "generated_at": hit["generated_at"] if hit is not None else None,
            "data_basis": _data_basis(conn, cfg),
        }
        _json_ok("insight", data)
    finally:
        if owns:
            conn.close()



def _cmd_review_cli(*, gw=None, last=4, ai_override=None, format_="text", conn=None):
    """Audit past decisions and print results. Window: --gw N (single) OR --last N (last N
    settled GWs). AI provider: ai_override ∈ {'deepseek','ollama','none', None}; None falls back
    to config."""
    import os
    from .audit import audit as audit_mod, reports as reports_mod

    owns = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        # Resolve the GW window.
        if gw is not None:
            gw_lo, gw_hi = gw, gw
        else:
            settled = [r["id"] for r in conn.execute(
                "SELECT id FROM gameweeks WHERE finished=1 ORDER BY id")]
            if not settled:
                print("No settled gameweeks — nothing to audit yet.")
                return
            gw_hi = settled[-1]
            gw_lo = settled[-min(last, len(settled))]

        # Build the provider (if any).
        provider, model_id = None, None
        provider_choice = ai_override or _resolve_audit_provider_choice()
        if provider_choice == "deepseek":
            if not os.environ.get("DEEPSEEK_API_KEY"):
                print("Error: --ai deepseek requires DEEPSEEK_API_KEY env var. Aborting.")
                return
            from .ai.provider import build_provider
            provider = build_provider(config.load_config(), conn=conn)
            model_id = config.ai_deepseek_model()
        elif provider_choice == "ollama":
            from .ai.provider import build_provider
            provider = build_provider(config.load_config())
            model_id = config.ai_ollama_model()

        # Run the audit.
        cfg = config.load_config()
        current_thresholds = {
            "thresholds.min_ep_delta_for_transfer":
                cfg.get("thresholds", {}).get("min_ep_delta_for_transfer", 2.0),
        }
        report = audit_mod.run_audit(
            conn, gw_lo=gw_lo, gw_hi=gw_hi,
            ai_provider=provider, ai_model_id=model_id,
            current_thresholds=current_thresholds)

        # Emit.
        if format_ == "json":
            print(json.dumps(reports_mod._to_jsonable(report), indent=2, default=str))
        else:
            print(reports_mod.format_text(report))
    finally:
        if owns:
            conn.close()


def _resolve_audit_provider_choice():
    """Read ai.audit.provider from config; default to 'none' if missing."""
    cfg = config.load_config()
    return cfg.get("ai", {}).get("audit", {}).get("provider", "none")


def _live_requires_tty(live):
    if live and not sys.stdin.isatty():
        print("Error: --live requires an interactive terminal (stdin TTY). "
              "Agent sessions can never pass --live (R3).", file=sys.stderr)
        raise SystemExit(2)


def _ai_provider_or_err(command, hint=None):
    """Build the AI provider; on failure emit an error envelope and exit (1)."""
    try:
        from .ai.provider import build_provider
        return build_provider(config.load_config())
    except Exception as exc:  # noqa: BLE001 - any provider start failure -> E_RUNTIME
        _json_err(command, "E_RUNTIME", f"AI provider unavailable: {exc}",
                  hint or "check DEEPSEEK_API_KEY / ai.provider config")


def _cmd_apply_squad(conn=None, salt_path=None, verify_path=None, live=False,
                     session=None, provider=None, confirm_fn=None):
    """Apply the AI-built squad (dry-run default; --live = master key + typed confirm)."""
    from .auth import master
    from .execution import squad as squad_mod
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    # The dry-run needs the session too (fetch_current_picks for the diff), so the
    # master key is unlocked regardless of --live.
    key = master.get_master_key(**mkw)
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    if provider is None:
        provider = _ai_provider_or_err(
            "apply-squad", "retry later; the squad builder runs without speculation")
    try:
        result = squad_mod.apply_squad(conn, key, live=live, confirm_fn=confirm_fn,
                                       session=session, provider=provider)
    except Exception as exc:
        print(f"Could not apply: {exc}")
        if owns_conn:
            conn.close()
        return
    for p in result.get("pairs", []):
        print(f"  OUT {p['out_name']} -> IN {p['in_name']}")
    print(f"Applied: {len(result['applied'])} | Failed: {result['failed'] or 'none'}")
    if not live:
        print("DRY-RUN — nothing was written. Re-run with --live to apply.")
    elif result["failed"]:
        print("Aborted — not all transfers applied.")
    if owns_conn:
        conn.close()


def _execute_lineup_cli(conn=None, salt_path=None, verify_path=None, live=False,
                        session=None, ranker=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import lineup as lineup_mod
    from .execution import executor as executor_mod
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned change: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        result = lineup_mod.run_lineup(conn, key, live=live, confirm_fn=confirm_fn,
                                       session=session, ranker=ranker)
    except (executor_mod.ExecutorError, SessionError) as exc:
        print(f"Could not execute: {exc}")
        if owns_conn:
            conn.close()
        return
    if live and result.dry_run:
        print("Aborted — nothing submitted.")
    elif result.dry_run:
        print("DRY-RUN — would POST:")
        print(f"  {result.request['method']} {result.request['url']}")
        print(f"  body: {result.request['body']}")
    elif result.ok:
        print(f"Submitted. HTTP {result.status}.")
        from .data import repository
        from .decisions.transfers import _next_gw
        gw = _next_gw(conn)
        if gw is not None:
            repository.touch_user_action(conn, gw)
    else:
        print(f"Submission failed (HTTP {result.status}); nothing changed.")
    if owns_conn:
        conn.close()


def _execute_transfer_cli(conn=None, salt_path=None, verify_path=None, live=False, rank=1,
                          session=None, suggester=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import transfer as transfer_mod
    from .execution import executor as executor_mod
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned transfer: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        result = transfer_mod.run_transfer(conn, key, rank=rank, live=live, confirm_fn=confirm_fn,
                                           session=session, suggester=suggester)
    except (executor_mod.ExecutorError, SessionError) as exc:
        print(f"Could not execute: {exc}")
        if owns_conn:
            conn.close()
        return
    if live and result.dry_run:
        print("Aborted — nothing submitted.")
    elif result.dry_run:
        print("DRY-RUN — would POST:")
        print(f"  {result.request['method']} {result.request['url']}")
        print(f"  body: {result.request['body']}")
    elif result.ok:
        print(f"Submitted. HTTP {result.status}.")
        from .data import repository
        from .decisions.transfers import _next_gw
        gw = _next_gw(conn)
        if gw is not None:
            repository.touch_user_action(conn, gw)
    else:
        print(f"Submission failed (HTTP {result.status}); nothing changed.")
    if owns_conn:
        conn.close()


def _undo_transfer_cli(conn=None, salt_path=None, verify_path=None, live=False,
                       session=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import executor as executor_mod
    from .interface import deadguard
    from .decisions.transfers import _next_gw
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned undo: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        gw = _next_gw(conn)
        if gw is None:
            print("No upcoming gameweek.")
            return
        try:
            result = deadguard.run_undo(conn, key, gw, live=live, confirm_fn=confirm_fn, session=session)
        except (executor_mod.ExecutorError, SessionError) as exc:
            print(f"Could not undo: {exc}")
            return
        if result is None:
            print("Nothing to undo (no deadguard transfer, already undone, or deadline passed).")
        elif result.dry_run:
            print("DRY-RUN — would POST:")
            print(f"  {result.request['method']} {result.request['url']}")
            print(f"  body: {result.request['body']}")
        elif result.ok:
            print(f"Undone. HTTP {result.status}.")
        else:
            print(f"Undo failed (HTTP {result.status}); nothing changed.")
    finally:
        if owns_conn:
            conn.close()


def _route_gameweek_cli(conn=None, salt_path=None, verify_path=None, live=False, mode=None,
                        session=None, ranker=None, suggester=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import router as router_mod
    from .execution import executor as executor_mod
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if live:
        if confirm_fn is None:
            def confirm_fn():
                return input("Execute the auto-routed decisions live on your FPL team? Type 'yes': ").strip().lower() == "yes"
        if not confirm_fn():
            print("Aborted — nothing executed.")
            return
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        plan = router_mod.route_gameweek(conn, key, live=live, mode=mode,
                                         session=session, ranker=ranker, suggester=suggester)
    except (executor_mod.ExecutorError, SessionError) as exc:
        print(f"Could not route: {exc}")
        if owns_conn:
            conn.close()
        return
    label = "LIVE" if live else "DRY-RUN"
    print(f"Mode-router plan ({label}):")
    for p in plan:
        print(f"  {p['decision']}: {p['route'].upper()} (confidence {p['confidence']})")
    if owns_conn:
        conn.close()


def refresh_my_team(*, conn=None, cfg=None):
    """Fetch /api/my-team (authed) once and snapshot it. Prompts for master password.

    Use this when not running the daemon but you want the dashboard / executor to see the
    upcoming-GW squad and real free_transfers.
    """
    import getpass
    import sys
    from .auth import master, session as auth_session
    from .execution import executor
    from .data import repository
    from .scheduler import _next_gw_id

    cfg = cfg or load_config()
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    if owns_conn:
        init_db(conn)

    try:
        key = master.get_master_key()  # MASTER_PASSWORD env var first, then getpass prompt
    except Exception as exc:
        print(f"could not unlock master key: {exc}", file=sys.stderr)
        if owns_conn:
            conn.close()
        raise SystemExit(2)

    next_gw = _next_gw_id(conn)
    if next_gw is None:
        print("no upcoming gameweek — run `refresh` first", file=sys.stderr)
        if owns_conn:
            conn.close()
        raise SystemExit(1)

    try:
        sess = auth_session.ensure_session(conn, key)
        payload = executor.fetch_my_team_authed(sess, cfg_team_id(cfg))
    except Exception as exc:
        print(f"authed my-team fetch failed (session/network): {exc}", file=sys.stderr)
        if owns_conn:
            conn.close()
        raise SystemExit(1)

    repository.snapshot_my_team_authed(conn, next_gw, payload)
    ft = payload.get("transfers", {}).get("limit")
    print(f"my_team OK (authed, GW{next_gw}, FT={ft})")

    if owns_conn:
        conn.close()


def serve(host="127.0.0.1", port=None, scheduler=True):
    import os
    import uvicorn
    port = port or int(os.getenv("PORT", "8000"))
    sched = None
    if scheduler:
        from .scheduler import build_scheduler, _maybe_load_key
        sched = build_scheduler(key=_maybe_load_key())
        sched.start()
    try:
        uvicorn.run("src.interface.api:app", host=host, port=port)
    finally:
        if sched is not None:
            sched.shutdown(wait=False)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fpl-autopilot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_refresh = sub.add_parser("refresh", help="fetch FPL + Understat + databank data into the local DB")
    p_refresh.add_argument("--full", action="store_true", help="ignore cache, fetch everything")
    p_refresh.add_argument("--source", choices=["fpl", "understat", "databank"], default=None,
                           help="restrict to one source (default: all three)")
    p_refresh.add_argument("--json", action="store_true",
                           help="output the JSON envelope (agent contract)")
    p_serve = sub.add_parser("serve", help="run the FastAPI server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--no-scheduler", action="store_true",
                         help="run the API without the background scheduler")
    sub.add_parser("scheduler", help="run the background refresh scheduler (blocking)")
    sub.add_parser("init-master-password", help="set the master password that encrypts stored credentials")
    sub.add_parser("init-fpl", help="log in to FPL and store the encrypted session")
    p_auth_status = sub.add_parser("auth-status", help="show stored FPL session state (no secrets)")
    p_auth_status.add_argument("--json", action="store_true",
                               help="output the JSON envelope (agent contract)")
    p_exec = sub.add_parser("execute-lineup", help="set captain & vice from the ranker (dry-run unless --live)")
    p_exec.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
    p_xfer = sub.add_parser("execute-transfer", help="make one free transfer from the suggestions (dry-run unless --live)")
    p_xfer.add_argument("--live", action="store_true", help="actually submit to FPL (requires typed confirmation)")
    p_xfer.add_argument("--rank", type=int, default=1, help="which suggestion to execute (1-based; default 1)")
    p_apply = sub.add_parser("apply-squad", help="apply the AI-built squad (dry-run unless --live)")
    p_apply.add_argument("--live", action="store_true", help="actually submit the transfers (requires master password + typed confirmation)")
    p_route = sub.add_parser("route-gameweek", help="route captain + transfer per mode/confidence (dry-run unless --live)")
    p_route.add_argument("--live", action="store_true", help="execute the auto-routed decisions (requires typed confirmation)")
    p_route.add_argument("--mode", choices=["auto", "manual", "hybrid"], default=None, help="override config mode for this run")
    p_undo = sub.add_parser("undo-transfer", help="revert deadguard's transfer before the deadline (dry-run unless --live)")
    p_undo.add_argument("--live", action="store_true", help="actually submit the reverse transfer (requires typed confirmation)")
    sub.add_parser("refresh-my-team", help="fetch /api/my-team (authed) and snapshot it (prompts for master password)")
    p_freeze = sub.add_parser("freeze", help="halt all autonomous FPL execution (auto + deadguard)")
    p_freeze.add_argument("--reason", default="frozen from CLI")
    sub.add_parser("unfreeze", help="resume autonomous FPL execution")
    p_freeze_status = sub.add_parser("freeze-status", help="show whether autonomous execution is frozen")
    p_freeze_status.add_argument("--json", action="store_true",
                                 help="output the JSON envelope (agent contract)")
    p_status = sub.add_parser("status", help="one-shot state: mode, frozen, freshness, next GW, pending decisions")
    p_status.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_resume = sub.add_parser("resume", help="session continuity: status + activity tail + operating rules")
    p_resume.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_resume.add_argument("--tail", type=int, default=10, help="activity entries to include (default 10)")
    p_log = sub.add_parser("log", help="filterable activity tail")
    p_log.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_log.add_argument("--tail", type=int, default=10, help="max entries (default 10)")
    p_log.add_argument("--gw", type=int, default=None, help="filter by gameweek")
    p_log.add_argument("--mode", default=None, help="filter by mode (e.g. manual, deadguard, auto)")
    p_log.add_argument("--decision-type", dest="decision_type", default=None,
                       help="filter by decision type (e.g. transfer, captain)")
    for _name, _help in (("captain", "captain ranker output (JSON)"),
                         ("transfers", "transfer suggestions (JSON)"),
                         ("chips", "chip recommendation (JSON)")):
        p = sub.add_parser(_name, help=_help)
        p.add_argument("--json", action="store_true", required=True,
                       help="output the JSON envelope (agent contract)")
    p_squad = sub.add_parser("squad", help="AI-built squad (JSON; --candidates for the pool)")
    p_squad.add_argument("--json", action="store_true", required=True,
                         help="output the JSON envelope (agent contract)")
    p_squad.add_argument("--candidates", action="store_true",
                         help="output the deterministic candidate pool instead of the built squad")
    p_speculate = sub.add_parser("speculate", help="AI spike/drop signals (JSON)")
    p_speculate.add_argument("--json", action="store_true", required=True,
                             help="output the JSON envelope (agent contract)")
    p_insight = sub.add_parser("insight", help="per-player AI deep-dive (JSON)")
    p_insight.add_argument("player_id", type=int, help="FPL player id")
    p_insight.add_argument("--json", action="store_true", required=True,
                           help="output the JSON envelope (agent contract)")
    p_review = sub.add_parser("review", help="audit past decisions vs outcomes")
    review_window = p_review.add_mutually_exclusive_group()
    review_window.add_argument("--gw", type=int, default=None,
                               help="audit a single GW")
    review_window.add_argument("--last", type=int, default=4,
                               help="audit the last N settled GWs (default 4)")
    p_review.add_argument("--ai", choices=["deepseek", "ollama", "none"], default=None,
                          help="override the AI provider for this run (default: from config)")
    p_review.add_argument("--format", choices=["text", "json"], default="text",
                          dest="format_", help="output format (default: text)")
    args = parser.parse_args(argv)
    if args.command in ("execute-lineup", "execute-transfer", "apply-squad",
                        "route-gameweek", "undo-transfer"):
        _live_requires_tty(args.live)
    if args.command == "refresh":
        sources = (args.source,) if args.source else ("fpl", "understat", "databank")
        if args.json:
            report = refresh(full=args.full, sources=sources, report=True)
            _json_ok("refresh", report)
        else:
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
    elif args.command == "auth-status":
        _cmd_auth_status_cli(json_out=args.json)
    elif args.command == "captain":
        _cmd_captain_cli()
    elif args.command == "transfers":
        _cmd_transfers_cli()
    elif args.command == "chips":
        _cmd_chips_cli()
    elif args.command == "squad":
        _cmd_squad_cli(candidates_only=args.candidates)
    elif args.command == "speculate":
        _cmd_speculate_cli()
    elif args.command == "insight":
        _cmd_insight_cli(args.player_id)
    elif args.command == "status":
        _cmd_status_cli(json_out=args.json)
    elif args.command == "resume":
        _cmd_resume_cli(tail=args.tail, json_out=args.json)
    elif args.command == "log":
        _cmd_log_cli(tail=args.tail, gw=args.gw, mode=args.mode,
                     decision_type=args.decision_type, json_out=args.json)
    elif args.command == "execute-lineup":
        _execute_lineup_cli(live=args.live)
    elif args.command == "execute-transfer":
        _execute_transfer_cli(live=args.live, rank=args.rank)
    elif args.command == "apply-squad":
        _cmd_apply_squad(live=args.live)
    elif args.command == "route-gameweek":
        _route_gameweek_cli(live=args.live, mode=args.mode)
    elif args.command == "undo-transfer":
        _undo_transfer_cli(live=args.live)
    elif args.command == "refresh-my-team":
        refresh_my_team()
    elif args.command == "freeze":
        _freeze_cli(reason=args.reason)
    elif args.command == "unfreeze":
        _unfreeze_cli()
    elif args.command == "freeze-status":
        _cmd_freeze_status_cli(json_out=args.json)
    elif args.command == "review":
        _cmd_review_cli(gw=args.gw, last=args.last,
                        ai_override=args.ai, format_=args.format_)


if __name__ == "__main__":
    main()
