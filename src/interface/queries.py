import json
from datetime import datetime, timezone
from src import config
from src.config import load_config


def _next_gw(conn):
    r = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return r["gw"] if r and r["gw"] is not None else None


def _read_deadguard_ai_prose(conn, gw):
    """Read the most recent cached deadguard_summary prose for this gw, or None."""
    row = conn.execute(
        "SELECT prose FROM ai_reasoning_cache "
        "WHERE gw=? AND pane_type='deadguard_summary' "
        "ORDER BY generated_at DESC LIMIT 1", (gw,)).fetchone()
    return row["prose"] if row is not None else None


def _status_banners(conn, nxt, frozen_status, cfg, now):
    banners = []
    if frozen_status is not None:
        banners.append({"level": "error",
                        "text": f"Auto-execution frozen — {frozen_status['reason']}."})
    if nxt is None:
        return banners
    state = nxt["state"]
    deadline = datetime.fromisoformat(nxt["deadline_utc"]) if nxt["deadline_utc"] else None
    if state == "DEADGUARD_EXECUTED":
        ai_prose = _read_deadguard_ai_prose(conn, nxt["id"])
        intro = ai_prose if ai_prose else "Deadguard set your team this gameweek."
        banners.append({"level": "info",
                        "text": f"{intro} Undo a transfer via Telegram or `undo-transfer` before the deadline."})
    elif (state == "PENDING" and frozen_status is None and config.deadguard_enabled(cfg)
          and deadline is not None):
        mins = (deadline - now).total_seconds() / 60
        if 0 < mins <= config.deadguard_warning_minutes(cfg):
            banners.append({"level": "warning",
                            "text": f"Deadguard will set your team in ~{int(mins)} min unless you act.",
                            "action": {"label": "Keep as is", "endpoint": "/api/deadguard/keep"}})
    return banners


def get_status(conn):
    from src.execution import override
    cur = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_current=1").fetchone()
    nxt = conn.execute("SELECT id, deadline_utc, state FROM gameweeks WHERE is_next=1").fetchone()
    fresh = conn.execute("SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()
    cfg = load_config()
    mode = cfg.get("mode", {}).get("current", "manual")
    deadline_src = nxt or cur
    frozen_status = override.status(conn)
    now = datetime.now(timezone.utc)
    return {
        "current_gw": cur["id"] if cur else None,
        "next_gw": nxt["id"] if nxt else None,
        "deadline_utc": deadline_src["deadline_utc"] if deadline_src else None,
        "mode": mode,
        "data_fresh_as_of_utc": fresh["m"] if fresh else None,
        "frozen": frozen_status is not None,
        "banners": _status_banners(conn, nxt, frozen_status, cfg, now),
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


def get_captain_picks(conn):
    """Wraps src.decisions.captain.get_captain_picks and enriches picks[0]
    with (reasoning, reasoning_source). Other picks keep the engine's reason
    string under both keys (no AI prose for vice/alts in S-A.1)."""
    from src.decisions import captain as captain_engine
    from src.ai import reasoning as ai_reasoning
    decision = captain_engine.get_captain_picks(conn)
    if not decision["picks"]:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_captain_reasoning(conn, gw, decision)
    enriched = list(decision["picks"])
    enriched[0] = {**enriched[0], "reasoning": prose, "reasoning_source": source}
    for i in range(1, len(enriched)):
        enriched[i] = {**enriched[i], "reasoning": enriched[i]["reason"],
                       "reasoning_source": "classic"}
    return {**decision, "picks": enriched}


def get_captain_reasoning(conn, gw):
    """Cheap lookup used by the Telegram path. Returns the cached AI prose for
    the captain pane at `gw`, or None on miss (or when the engine has no picks).
    """
    from src.decisions import captain as captain_engine
    from src.ai import reasoning as ai_reasoning
    decision = captain_engine.get_captain_picks(conn)
    if not decision["picks"]:
        return None
    prose, source = ai_reasoning.render_captain_reasoning(conn, gw, decision)
    return prose if source == "ai" else None


def get_transfer_suggestions(conn):
    """Wraps transfers.get_transfer_suggestions and enriches the TOP suggestion with
    (reasoning, reasoning_source). Other suggestions get reasoning='' + reasoning_source='classic'."""
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    enriched = list(decision["suggestions"])
    enriched[0] = {**enriched[0], "reasoning": prose, "reasoning_source": source}
    for i in range(1, len(enriched)):
        enriched[i] = {**enriched[i], "reasoning": "", "reasoning_source": "classic"}
    return {**decision, "suggestions": enriched}


def get_transfer_reasoning(conn, gw):
    """Cheap lookup for the Telegram path. Returns cached AI prose, or None on miss."""
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return None
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    return prose if source == "ai" else None


def get_chip_recommendation(conn):
    """Wraps chips.recommend_chip and enriches the recommendation (if any) with
    (reasoning, reasoning_source). When recommendation is None, returns unchanged."""
    from src.decisions import chips
    from src.ai import reasoning as ai_reasoning
    decision = chips.recommend_chip(conn)
    if decision.get("recommendation") is None:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_chip_reasoning(conn, gw, decision)
    return {
        **decision,
        "recommendation": {
            **decision["recommendation"],
            "reasoning": prose,
            "reasoning_source": source,
        },
    }


def get_chip_reasoning(conn, gw):
    """Telegram-path helper. Returns cached AI prose, or None on miss."""
    from src.decisions import chips
    from src.ai import reasoning as ai_reasoning
    decision = chips.recommend_chip(conn)
    if decision.get("recommendation") is None:
        return None
    prose, source = ai_reasoning.render_chip_reasoning(conn, gw, decision)
    return prose if source == "ai" else None
