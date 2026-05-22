# API Layer (FastAPI) — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** The Interface-layer backend — a FastAPI app serving the seven read-only endpoints in `docs/api-contract.md`, so the (parallel) PWA dashboard can wire to a real backend.
- **Slice goal:** `GET /api/{status,squad,fixtures/planner,activity,captain,transfers,chips}` return valid `api-contract.md` payloads from the live DB, runnable via `fpl-autopilot serve`.

Built in parallel with the captain/transfer engines (agents) and the dashboard (agent). This slice is conflict-free: it lives entirely in a new `src/interface/` package (plus `cli.py`/`pyproject.toml`, which only this workstream touches).

---

## 1. Context

`docs/api-contract.md` is the frozen contract (shapes + field availability). The Data + Analytics layers are merged, so most endpoints have live data now; the decision-engine-backed endpoints are stubbed and wired on merge.

Architecture boundary (B2): the Interface displays/serves — it does not compute decisions. The API **reads** Analytics/Decision outputs from the DB and assembles display JSON (joins, and a trivial `xp_next5` sum = read-model assembly, not decision logic). Decision computation stays in the decision layer (the API calls its readers).

## 2. Decisions locked

| Decision | Choice |
|---|---|
| Framework | FastAPI; run via `uvicorn` (+ a `fpl-autopilot serve` command) |
| DB access | a `get_db` FastAPI dependency reading `config.db_path()`, overridable in tests |
| captain/transfers/chips | **stub** the documented empty shapes now; wire to decision readers on merge (one line each) |
| New deps | `fastapi`, `uvicorn` (runtime); `httpx` (dev, for `TestClient`) |
| CORS | enabled for localhost origins (the dashboard dev server is a different port) |
| Methods | GET only (read-only, Phase 1) |

## 3. Scope

### In scope
- `src/interface/` package: `api.py` (FastAPI `app` + 7 routes), `queries.py` (read-model), `deps.py` (`get_db`).
- The four live endpoints (`status`, `squad`, `fixtures/planner`, `activity`) reading the DB.
- The three stub endpoints (`captain`, `transfers`, `chips`) returning the documented empty shapes, each with a marked one-line wiring point.
- `pyproject.toml`/`requirements.txt`: add `fastapi`, `uvicorn`, `httpx`.
- `src/cli.py`: add a `serve` subcommand (`uvicorn` run; host/port from config/.env, default 8000).
- CORS middleware for localhost.
- Tests via FastAPI `TestClient` against a seeded in-memory DB (dependency override).

### Out of scope
- Auth, POST/execution endpoints (Phase 2).
- Serving the built frontend (the dashboard agent owns the PWA; this exposes only the API).
- The chip recommender itself (separate slice); `chips` stays stubbed.
- Wiring captain/transfers to the real readers — done as a tiny follow-up once those PRs merge (the endpoints are stubbed until then so this slice doesn't depend on the in-flight engines).

## 4. File layout

```
src/interface/
  __init__.py
  deps.py        # get_db dependency
  queries.py     # read-model: get_status, get_squad, get_fixtures_planner, get_activity
  api.py         # FastAPI app + 7 GET routes + CORS
src/cli.py       # +serve subcommand
pyproject.toml / requirements.txt   # +fastapi, uvicorn, httpx
tests/test_api.py
```

## 5. Components

### 5.1 `deps.py`
```
def get_db():
    conn = connect(config.db_path()); init_db(conn)
    try: yield conn
    finally: conn.close()
```

### 5.2 `queries.py` (read-model; pure-ish — takes a conn, returns contract dicts)
- `get_status(conn)`: `current_gw`/`next_gw`/`deadline_utc` from `gameweeks` (is_current / is_next); `mode` from `config.load_config()["mode"]["current"]`; `data_fresh_as_of_utc` = `MAX(last_fetched_utc)` from `cache_meta`; `banners: []`.
- `get_squad(conn)`: latest `my_team` snapshot → `gw`, `bank`, `team_value`, `free_transfers`; `players`: for each pick (parsed from `picks_json`), join `players` (web_name, position, price, status) + `teams.short_name`, plus `is_captain`/`is_vice_captain`/`multiplier` from the pick, `xp_next` = `xp[player, next_gw]` (v1), `xp_next5` = sum of the player's next-5-GW `xp` (v1; `null` if no rows).
- `get_fixtures_planner(conn, horizon=5)`: `horizon` = next 5 GW ids from the first unfinished GW; `rows`: per squad player → `cells` (length `horizon`); each cell = the player's team's fixture that GW → `{gw, opponent_short, home, fdr_attack, fdr_defense}` (from `fixtures`+`teams`+`fdr`), or `null` for a blank GW (team has no fixture).
- `get_activity(conn, limit=20)`: most-recent `activity_log` rows → contract shape (empty list for now).

### 5.3 `api.py`
FastAPI `app`, CORS middleware (allow `http://localhost:*` dev origins, GET only), and routes:
- `GET /api/status` → `queries.get_status(conn)`
- `GET /api/squad` → `queries.get_squad(conn)`
- `GET /api/fixtures/planner` → `queries.get_fixtures_planner(conn)`
- `GET /api/activity` → `queries.get_activity(conn)`
- `GET /api/captain` → `{"picks": [], "vice_player_id": None}`  *(# WIRE: src.decisions.captain.get_captain_picks(conn) on merge)*
- `GET /api/transfers` → `{"suggestions": [], "empty_reason": "No transfers worth making this GW."}`  *(# WIRE: src.decisions.transfers.get_transfer_suggestions(conn) on merge)*
- `GET /api/chips` → `{"recommendation": None}`  *(# until chip recommender slice)*

All DB routes take `conn=Depends(get_db)`.

### 5.4 `cli.py` `serve`
`fpl-autopilot serve [--host 0.0.0.0] [--port 8000]` → `uvicorn.run("src.interface.api:app", host, port)`. Port default from `.env` `PORT` else 8000.

## 6. Testing
FastAPI `TestClient`; a `client` fixture seeds an in-memory DB (reusing repository + `fdr.compute_and_store` + `xp.compute_and_store` on frozen fixtures with one crafted unfinished GW + fixtures) and overrides `get_db` to yield it.
- `test_status_shape`: 200; keys `current_gw, deadline_utc, mode, banners`.
- `test_squad_has_15_with_xp`: 200; 15 players; each has `web_name, position, price, xp_next` (xp_next a number after seeding xp).
- `test_fixtures_planner_grid`: 200; rows per squad player; cells carry 1–5 `fdr_attack`/`fdr_defense` or null for blanks.
- `test_activity_empty_ok`: 200; `entries == []`.
- `test_captain_transfers_chips_stub_shapes`: each returns the documented empty shape (captain `{picks:[], vice_player_id:None}`, transfers `{suggestions:[], empty_reason:...}`, chips `{recommendation:None}`).
- `test_cors_header_present`: a request with an `Origin: http://localhost:5173` gets `access-control-allow-origin`.

## 7. Definition of done
1. `pytest` green incl. API tests.
2. `fpl-autopilot serve` starts; `curl localhost:8000/api/squad` (after a live `refresh` + fdr/xp compute) returns the 15-man squad with `xp_next` populated; `/api/status` and `/api/fixtures/planner` return live data.
3. Stub endpoints return the exact documented shapes (ready for one-line wiring when the engines merge).

## 8. Notes / coordination
- **Wiring follow-up:** when `feat/captain-ranker` and `feat/transfer-engine` merge, replace the two stub return lines with calls to `get_captain_picks(conn)` / `get_transfer_suggestions(conn)` (a tiny PR). The dashboard agent consumes the same contract throughout, so its wiring is unaffected.
- **No conflicts:** this slice's files (`src/interface/*`, `tests/test_api.py`) are disjoint from the engines (`src/decisions/*`) and the dashboard (`frontend/`). `cli.py`/`pyproject.toml` are touched only by this workstream.
