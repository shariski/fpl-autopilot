from datetime import datetime, timezone
import json

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .deps import get_db
from . import queries
from src.data import repository

app = FastAPI(title="FPL Autopilot API")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/status")
def status(conn=Depends(get_db)):
    return queries.get_status(conn)


@app.get("/api/mode")
def get_mode(conn=Depends(get_db)):
    return queries.get_mode(conn)


@app.post("/api/mode")
def set_mode(payload: dict, conn=Depends(get_db)):
    """Runtime mode switch (manual|hybrid|auto); persists across redeploys."""
    m = (payload or {}).get("mode")
    if m not in ("manual", "hybrid", "auto"):
        return JSONResponse(status_code=400,
                            content={"detail": "mode must be manual, hybrid or auto"})
    conn.execute(
        "INSERT INTO system_state (key, value) VALUES ('mode', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps({"mode": m, "set_at": datetime.now(timezone.utc).isoformat(),
                     "source": "dashboard"}),))
    conn.commit()
    return queries.get_mode(conn)


@app.get("/api/squad")
def squad(conn=Depends(get_db)):
    return queries.get_squad(conn)


@app.get("/api/fixtures/planner")
def fixtures_planner(conn=Depends(get_db)):
    return queries.get_fixtures_planner(conn)


@app.get("/api/activity")
def activity(conn=Depends(get_db)):
    return queries.get_activity(conn)


@app.get("/api/captain")
def captain(conn=Depends(get_db)):
    return queries.get_captain_picks(conn)


@app.get("/api/transfers")
def transfers(conn=Depends(get_db)):
    return queries.get_transfer_suggestions(conn)


@app.get("/api/chips")
def chips(conn=Depends(get_db)):
    return queries.get_chip_recommendation(conn)


@app.post("/api/freeze")
def freeze(conn=Depends(get_db)):
    from src.execution import override
    override.freeze(conn, reason="frozen from dashboard", source="user")
    return queries.get_status(conn)


@app.post("/api/unfreeze")
def unfreeze(conn=Depends(get_db)):
    from src.execution import override
    override.unfreeze(conn, source="user")
    return queries.get_status(conn)


@app.post("/api/deadguard/keep")
def deadguard_keep(conn=Depends(get_db)):
    nxt = conn.execute("SELECT id FROM gameweeks WHERE is_next=1").fetchone()
    if nxt:
        repository.touch_user_action(conn, nxt["id"])
    return queries.get_status(conn)


@app.get("/api/players/{player_id}/insight")
def player_insight(player_id: int, conn=Depends(get_db)):
    """Per-player AI deep-dive. Cache-first; generates on miss; never 500s."""
    from src import config
    from src.ai import cache as ai_cache
    from src.ai.insight import runner

    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    if nxt is None or nxt["gw"] is None:
        return {"status": "unavailable", "reason": "no_next_gw"}
    gw = nxt["gw"]
    exists = conn.execute("SELECT id FROM players WHERE id=?", (player_id,)).fetchone()
    if exists is None:
        return JSONResponse(status_code=404, content={"detail": "unknown player"})
    if not config.ai_enabled():
        return {"status": "unavailable", "reason": "ai_disabled"}
    digest = runner.build_player_digest(conn, player_id, gw)
    if digest is None:
        return {"status": "unavailable", "reason": "no_digest"}
    rec_hash = ai_cache.recommendation_hash(digest)
    hit = ai_cache.get(conn, gw, runner.PANE_TYPE, rec_hash)
    if hit is not None:
        payload = runner.extract_json_object(hit["prose"])
        status = "cached"
    else:
        try:
            from src.ai.provider import build_provider
            provider = build_provider(config.load_config())
            payload = runner.generate_player_insight(
                conn, player_id, provider=provider,
                model_id=config.ai_deepseek_model())
        except Exception:
            return {"status": "unavailable", "reason": "provider_error"}
        if payload is None:
            return {"status": "unavailable", "reason": "gate_rejected"}
        status = "generated"
    return {
        "status": status, "player_id": player_id, "gw": gw,
        "player": _player_identity(conn, player_id),
        "insights": payload.get("insights", []),
        "summary": payload.get("summary", ""),
        "data_limits": payload.get("data_limits", []),
        "model_id": config.ai_deepseek_model(),
        "generated_at": hit["generated_at"] if hit is not None else None,
    }


def _player_identity(conn, player_id):
    row = conn.execute(
        "SELECT p.name, p.web_name, p.position, p.price, t.short_name AS team "
        "FROM players p JOIN teams t ON t.id = p.team_id WHERE p.id=?",
        (player_id,)).fetchone()
    if row is None:
        return None
    return {"name": row["name"], "web_name": row["web_name"],
            "position": row["position"], "team": row["team"], "price": row["price"]}


@app.get("/api/squad/builder")
def squad_builder(conn=Depends(get_db)):
    """AI-picked starting squad. Cache-first; validates + enriches the picks."""
    from src import config
    from src.ai import cache as ai_cache
    from src.ai.squad import runner
    from src.decisions.squad_builder import build_candidate_pool

    pool = build_candidate_pool(conn)
    if not pool:
        return JSONResponse(status_code=404, content={"detail": "no upcoming gameweek"})
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    gw = nxt["gw"] if nxt else None
    if not config.ai_enabled():
        return {"status": "unavailable", "reason": "ai_disabled"}
    digest = runner.build_squad_digest(conn, pool=pool)
    rec_hash = ai_cache.recommendation_hash(digest)
    hit = ai_cache.get(conn, gw, runner.PANE_TYPE, rec_hash)
    if hit is not None:
        result = runner.extract_json_object(hit["prose"])
        status = "cached"
    else:
        try:
            from src.ai.provider import build_provider
            provider = build_provider(config.load_config())
            result = runner.generate_squad(conn, provider=provider,
                                           model_id=config.ai_deepseek_model())
        except Exception:
            return {"status": "unavailable", "reason": "provider_error"}
        if result is None:
            return {"status": "unavailable", "reason": "gate_rejected"}
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
    return {
        "status": status, "gw": gw, "source": result.get("source", "ai"),
        "picks": picks, "template_rationale": result.get("template_rationale", ""),
        "risks": result.get("risks", []), "budget_used": budget_used,
        "speculation": spec,
        "model_id": config.ai_deepseek_model(),
        "generated_at": (hit["generated_at"] if hit is not None
                         else datetime.now(timezone.utc).isoformat()),
        # the squad-builder page shows when the underlying data was last fetched
        "data_basis": {
            "as_of_utc": conn.execute(
                "SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()["m"],
            "xp_model_version": config.load_config().get("xp_model", {}).get("version", "v2"),
        },
    }


@app.get("/api/audit/{gw}")
def audit_for_gw(gw: int):
    """Return the most recent persisted audit whose gw_hi matches `gw`. 404 if none.

    Files are named `audit_{lo}-{hi}_{ts}.json` (per reports.persist). We match on `-{gw}_`
    and pick the lexicographically-largest filename, which sorts by ISO-formatted timestamp.
    """
    from fastapi import HTTPException
    from src.audit import reports
    matches = sorted(reports.DEFAULT_DIR.glob(f"audit_*-{gw}_*.json"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"no audit found for gw={gw}")
    report = reports.load(matches[-1])
    return reports._to_jsonable(report)


# --- Static frontend (SvelteKit adapter-static build) ---
# Mounted at "/" so the dashboard PWA is served from the same FastAPI
# process in production. Conditional on the directory existing so local
# dev (no built frontend) is unaffected. The mount sits AFTER all
# @app.get/@app.post decorators above so the /api/* route table is
# registered first and is not shadowed by StaticFiles.
from pathlib import Path
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

_FRONTEND_BUILD = Path("/app/frontend_build")


class SpaStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback: unknown paths serve 200.html.

    SvelteKit's adapter-static emits 200.html (fallback: '200.html'); client-side
    routes like /players/449 have no real file, so we serve the fallback instead
    of 404ing, letting the SPA router render the page.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                fallback = Path(self.directory) / "200.html"
                if fallback.is_file():
                    return FileResponse(fallback)
            raise


def _mount_frontend(target_app, build_dir=None):
    """Mount the SvelteKit static build on `target_app` at /, if the build
    directory exists. Factored out so tests can drive it with a temp path."""
    build_dir = build_dir or _FRONTEND_BUILD
    if build_dir.is_dir():
        target_app.mount("/",
                         SpaStaticFiles(directory=build_dir, html=True),
                         name="frontend")


_mount_frontend(app)

@app.get("/api/speculation/notes")
def speculation_notes(conn=Depends(get_db)):
    return {"notes": repository.list_speculation_notes(conn)}


@app.post("/api/speculation/notes")
def add_speculation_note(payload: dict, conn=Depends(get_db)):
    note = str((payload or {}).get("note") or "").strip()
    if not note:
        return JSONResponse(status_code=400, content={"detail": "note must be non-empty"})
    team_id = (payload or {}).get("team_id")
    player_id = (payload or {}).get("player_id")
    nid = repository.add_speculation_note(conn, note, team_id=team_id, player_id=player_id)
    repository.log_activity(conn, decision_type="speculation", mode="manual",
                            action_taken=f"note add: {note[:80]}", executed=True,
                            inputs={"note_id": nid})
    row = [n for n in repository.list_speculation_notes(conn) if n["id"] == nid]
    return {"note": row[0] if row else None}


@app.delete("/api/speculation/notes/{note_id}")
def delete_speculation_note(note_id: int, conn=Depends(get_db)):
    if not repository.deactivate_speculation_note(conn, note_id):
        return JSONResponse(status_code=404, content={"detail": "note not found"})
    repository.log_activity(conn, decision_type="speculation", mode="manual",
                            action_taken=f"note rm: id {note_id}", executed=True,
                            inputs={"note_id": note_id})
    return {"ok": True}


@app.get("/api/speculation/teams")
def speculation_teams(conn=Depends(get_db)):
    return {"teams": [dict(r) for r in conn.execute(
        "SELECT id, short_name FROM teams ORDER BY short_name")]}


@app.get("/api/speculation/players")
def speculation_players(team_id: int, conn=Depends(get_db)):
    return {"players": [dict(r) for r in conn.execute(
        "SELECT id, web_name FROM players WHERE team_id=? ORDER BY web_name", (team_id,))]}
