import json
from src.config import load_config


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def get_status(conn):
    cur = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_current=1").fetchone()
    nxt = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_next=1").fetchone()
    fresh = conn.execute("SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()
    mode = load_config().get("mode", {}).get("current", "manual")
    deadline_src = nxt or cur
    return {
        "current_gw": cur["id"] if cur else None,
        "next_gw": nxt["id"] if nxt else None,
        "deadline_utc": deadline_src["deadline_utc"] if deadline_src else None,
        "mode": mode,
        "data_fresh_as_of_utc": fresh["m"] if fresh else None,
        "banners": [],
    }


def get_squad(conn):
    snap = conn.execute(
        "SELECT gw, picks_json, bank, team_value, free_transfers FROM my_team ORDER BY gw DESC LIMIT 1"
    ).fetchone()
    if snap is None:
        return {"gw": None, "bank": None, "team_value": None, "free_transfers": None, "players": []}
    next_gw = _next_gw(conn)
    players = []
    for pk in json.loads(snap["picks_json"]):
        pid = pk["element"]
        p = conn.execute(
            "SELECT p.web_name, p.position, p.price, p.status, t.short_name AS team_short "
            "FROM players p JOIN teams t ON t.id = p.team_id WHERE p.id=?", (pid,)).fetchone()
        if p is None:
            continue
        xpn = conn.execute(
            "SELECT xp FROM xp WHERE player_id=? AND gw=? AND model_version='v1'", (pid, next_gw)).fetchone()
        xp5 = conn.execute(
            "SELECT SUM(xp) AS s FROM xp WHERE player_id=? AND model_version='v1' AND gw BETWEEN ? AND ?",
            (pid, next_gw, (next_gw + 4) if next_gw else 0)).fetchone()
        players.append({
            "id": pid, "web_name": p["web_name"], "position": p["position"],
            "team_short": p["team_short"], "price": p["price"], "status": p["status"],
            "is_captain": bool(pk["is_captain"]), "is_vice_captain": bool(pk["is_vice_captain"]),
            "multiplier": pk["multiplier"],
            "xp_next": xpn["xp"] if xpn else None,
            "xp_next5": round(xp5["s"], 2) if xp5 and xp5["s"] is not None else None,
        })
    return {"gw": snap["gw"], "bank": snap["bank"], "team_value": snap["team_value"],
            "free_transfers": snap["free_transfers"], "players": players}


def get_fixtures_planner(conn, horizon=5):
    next_gw = _next_gw(conn)
    if next_gw is None:
        return {"horizon": [], "rows": []}
    gws = list(range(next_gw, next_gw + horizon))
    snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if snap is None:
        return {"horizon": gws, "rows": []}
    team_short = {r["id"]: r["short_name"] for r in conn.execute("SELECT id, short_name FROM teams")}
    rows = []
    for pk in json.loads(snap["picks_json"]):
        pid = pk["element"]
        p = conn.execute("SELECT web_name, position, team_id FROM players WHERE id=?", (pid,)).fetchone()
        if p is None:
            continue
        team = p["team_id"]
        cells = []
        for g in gws:
            fx = conn.execute(
                "SELECT home_team_id, away_team_id FROM fixtures "
                "WHERE gw=? AND (home_team_id=? OR away_team_id=?) LIMIT 1", (g, team, team)).fetchone()
            if fx is None:
                cells.append(None)
                continue
            home = fx["home_team_id"] == team
            opp = fx["away_team_id"] if home else fx["home_team_id"]
            fd = conn.execute(
                "SELECT fdr_attack, fdr_defense FROM fdr WHERE team_id=? AND gw=?", (team, g)).fetchone()
            cells.append({
                "gw": g, "opponent_short": team_short.get(opp), "home": home,
                "fdr_attack": fd["fdr_attack"] if fd else None,
                "fdr_defense": fd["fdr_defense"] if fd else None,
            })
        rows.append({"player_id": pid, "web_name": p["web_name"], "position": p["position"],
                     "team_short": team_short.get(team), "cells": cells})
    return {"horizon": gws, "rows": rows}


def get_activity(conn, limit=20):
    rows = conn.execute(
        "SELECT ts_utc, gw, mode, decision_type, action_taken, executed "
        "FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"entries": [dict(r) for r in rows]}
