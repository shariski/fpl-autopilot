# FPL Autopilot — Dashboard (Phase 1, Interface layer)

Installable, mobile-first SvelteKit PWA. Renders all seven Phase 1 dashboard
sections, wired to the live FastAPI backend (`/api`) with bundled mock fixtures
as a demo/offline fallback.

## Run

Start the backend (serves `/api` on `:8000`), then the dashboard:

```bash
# 1. Backend (from the repo root) — see the Python project
fpl-autopilot serve            # uvicorn on http://localhost:8000

# 2. Dashboard
cd frontend
npm install
npm run dev                    # http://localhost:5173 (proxies /api -> :8000)
npm test                       # Vitest
npm run build && npm run preview
```

The dev/preview servers proxy `/api` to `http://localhost:8000` (see
`server.proxy` in `vite.config.ts`), so the app calls same-origin `/api/...`.

## Data source

- **`/`** (default) — **live**: parallel GET of the seven `/api` endpoints,
  assembled into one typed `Dashboard`. If the backend isn't running, the load
  errors (start it, or use a mock URL below).
- **`/?mock=full`** — bundled fixtures, every field populated (demo / offline).
- **`/?mock=launch`** — the day-one state: `(forthcoming)` fields (xP, captain,
  transfers, chips) null/empty; live data still shows.

Resilience: the three decision endpoints (`captain`, `transfers`, `chips`) and
the activity log degrade to their empty state (logged, not silent) if they fail,
so a single unavailable slice never blanks the dashboard. The service worker
caches `/api` responses network-first, so the installed app opens offline with
last-known data.

## The single integration point

`src/lib/api/client.ts` is the only module that knows where data comes from:
`fetchDashboard()` (live) and `getMockDashboard()` (fixtures). Change the data
source there and nowhere else — components consume the same typed `Dashboard`.
