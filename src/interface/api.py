from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from .deps import get_db
from . import queries
from src.decisions import captain as captain_engine, transfers as transfers_engine, chips as chips_engine

app = FastAPI(title="FPL Autopilot API")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET"],
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
    return captain_engine.get_captain_picks(conn)


@app.get("/api/transfers")
def transfers(conn=Depends(get_db)):
    return transfers_engine.get_transfer_suggestions(conn)


@app.get("/api/chips")
def chips(conn=Depends(get_db)):
    return chips_engine.recommend_chip(conn)
