from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from .deps import get_db
from . import queries
from src.data import repository

app = FastAPI(title="FPL Autopilot API")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/status")
def status(conn=Depends(get_db)):
    return queries.get_status(conn)


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
from fastapi.staticfiles import StaticFiles

_FRONTEND_BUILD = Path("/app/frontend_build")


def _mount_frontend(target_app, build_dir=None):
    """Mount the SvelteKit static build on `target_app` at /, if the build
    directory exists. Factored out so tests can drive it with a temp path."""
    build_dir = build_dir or _FRONTEND_BUILD
    if build_dir.is_dir():
        target_app.mount("/",
                         StaticFiles(directory=build_dir, html=True),
                         name="frontend")


_mount_frontend(app)
