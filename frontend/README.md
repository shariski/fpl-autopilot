# FPL Autopilot — Dashboard (Phase 1, Interface layer)

Installable, mobile-first SvelteKit PWA. Renders all seven Phase 1 dashboard
sections from typed mock data behind a single swappable client.

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest
npm run build && npm run preview
```

## Mock scenarios

- `/?mock=full` (default) — every field populated.
- `/?mock=launch` — the real day-one: `(forthcoming)` fields (xP, captain,
  transfers, chips) are null/empty; live data (status, squad, FDR, activity) shows.

## Wiring to the real backend (single integration point)

`src/lib/api/client.ts` → `getDashboard()` is the only place that knows where
data comes from. Replace its body (marked `INTEGRATION POINT`) with parallel
`fetch('/api/...')` calls per `docs/api-contract.md`. Nothing else changes.
