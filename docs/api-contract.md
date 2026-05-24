# API Contract — Phase 1 Dashboard ⇄ Backend

The shared interface between the PWA dashboard (Interface layer) and the FastAPI backend. The dashboard is built against these shapes (with mock data) and the backend fulfills them. This lets the two be built in parallel.

**Conventions**
- Base path `/api`. Phase-1 endpoints are `GET`, JSON, read-only. Phase-2 state-mutating write endpoints are `POST` (see below).
- Prices and money are floats in £m (e.g. `14.7`, `2.3`).
- FDR values are integers `1`–`5` (1 = easiest, 5 = hardest).
- Positions are `GKP | DEF | MID | FWD`. Player `status` is the FPL flag `a|d|i|s|u`.
- Timestamps are ISO-8601 UTC strings.
- **Field availability:** fields tagged _(live)_ are backed by data already in the DB; _(forthcoming)_ fields depend on slices not yet built (xP, decision engines) and the backend returns `null` for them until then. The dashboard must render gracefully when a value is `null` (e.g. show "—").
- The Interface never computes (CLAUDE.md B2): it renders these payloads and accepts user input only.
- Errors: non-200 with body `{"error": "<message>"}`.

These payloads map to the dashboard sections defined in `docs/product-spec.md` ("Dashboard (Phase 1)").

---

## GET /api/status — header

```json
{
  "current_gw": 38,
  "next_gw": null,
  "deadline_utc": "2026-05-24T13:00:00Z",
  "mode": "manual",                       // auto | manual | hybrid | deadguard | frozen
  "frozen": false,                        // (Phase 2.5c-3) true when an emergency freeze is active
  "data_fresh_as_of_utc": "2026-05-22T09:00:00Z",
  "banners": [                            // setup/health banners + deadguard/freeze state; [] when all good
    {
      "level": "warning",                 // info | warning | error
      "text": "Deadguard runs in ~47 min.",
      "action": {                         // optional; present on actionable banners only
        "label": "Keep as is",
        "endpoint": "/api/deadguard/keep"
      }
    }
  ]
}
```

**`Status.frozen`** _(Phase 2.5c-3, live)_: `true` when an emergency freeze is active (autonomous writes are
halted). Mirrors the `mode: "frozen"` value but is an explicit boolean for UI toggles.

**`Banner.action`** _(Phase 2.5c-3)_: optional field present only on banners that expose a one-click action.
`endpoint` is the POST path the dashboard calls when the user taps `label` (no body required; returns the
fresh `Status`). Absent on purely informational banners.

All other fields _(live)_ except `mode` (currently always `manual` until Phase 2).

## GET /api/squad — my team (pitch view)

```json
{
  "gw": 37,
  "bank": 2.3,
  "team_value": 99.7,
  "free_transfers": null,                 // (forthcoming, auth-only — Phase 2)
  "players": [
    {
      "id": 8260,
      "web_name": "Haaland",
      "position": "FWD",
      "team_short": "MCI",
      "price": 14.7,
      "status": "a",
      "is_captain": true,                 // (live, from picks snapshot)
      "is_vice_captain": false,
      "multiplier": 2,
      "xp_next": 7.2,                      // (forthcoming — xP slice; null until then)
      "xp_next5": 31.4                     // (forthcoming)
    }
  ]
}
```
Exactly 15 players. `id, web_name, position, team_short, price, status, is_captain, is_vice_captain, multiplier` _(live)_.

## GET /api/captain — captain picks _(forthcoming — captain ranker)_

```json
{
  "picks": [                              // top 5 by xP, ranked
    {
      "player_id": 8260,
      "web_name": "Haaland",
      "xp": 7.2,
      "fixture": "MCI v BOU (H)",
      "reason": "Highest xP (7.2). Next best Salah 6.1 — gap 1.1. Home vs FDR-2 defense."
    }
  ],
  "vice_player_id": 328,
  "confidence": 75                        // integer 0–100; null when picks is empty
}
```
`confidence` reflects data staleness (cache age), player availability (FPL status flags), and xP gap between the top two options. Computed by `src/decisions/confidence.score()`.

Until built, backend returns `{"picks": [], "vice_player_id": null, "confidence": null}`.

## GET /api/transfers — transfer ideas _(forthcoming — transfer engine)_

```json
{
  "suggestions": [                        // top 3 by EP delta; [] if none worth it
    {
      "out": {"player_id": 1, "web_name": "Isak", "price": 9.0},
      "in":  {"player_id": 2, "web_name": "Watkins", "price": 9.0},
      "ep_delta_5gw": 3.2,
      "hit_cost": 0,                       // 0, -4, -8 ...
      "confidence": 78
    }
  ],
  "empty_reason": null                     // e.g. "No transfers worth making this GW."
}
```

## GET /api/chips — chip recommendation _(forthcoming — chip recommender)_

```json
{
  "recommendation": {                      // or null when no chip condition is flagged
    "chip": "bench_boost",                 // wildcard | free_hit | bench_boost | triple_captain
    "reason": "DGW: all 15 have fixtures; combined bench xP 5.2 (> threshold 4)."
  }
}
```

## GET /api/fixtures/planner — fixture difficulty grid _(FDR live)_

Per squad player across the next N gameweeks (the 5×6 grid in `product-spec.md`).

```json
{
  "horizon": [38, 39, 40, 41, 42],
  "rows": [
    {
      "player_id": 8260,
      "web_name": "Haaland",
      "position": "FWD",
      "team_short": "MCI",
      "cells": [
        {"gw": 38, "opponent_short": "BOU", "home": true, "fdr_attack": 2, "fdr_defense": 3}
        // null cell entry for a blank gameweek (team has no fixture that GW)
      ]
    }
  ]
}
```
`fdr_attack`/`fdr_defense` _(live, from the `fdr` table)_. The dashboard colours each cell by `fdr_attack` for attackers (FWD/MID) and `fdr_defense` for defenders (DEF/GKP).

## GET /api/activity — activity log _(live; empty until decisions are logged)_

```json
{
  "entries": [
    {
      "ts_utc": "2026-05-22T19:30:00Z",
      "gw": 38,
      "mode": "manual",
      "decision_type": "captain",          // captain | transfer | bench | chip | deadguard
      "action_taken": "Captain set to Haaland",
      "executed": false
    }
  ]
}
```
Supports query params later (`?gw=`, `?limit=`); Phase-1 default returns the most recent ~20.

---

## POST /api/freeze — activate emergency freeze _(Phase 2.5c-3)_

No request body. Activates the emergency freeze (sets `frozen = true` in DB state). No FPL API call is made.
Returns the fresh `Status` object (same shape as `GET /api/status`).

**Constraint:** localhost only (`127.0.0.1`). No auth token required — freeze is plaintext operational state
(CLAUDE.md B7 / runbook freeze section).

```
POST /api/freeze
→ 200  Status   (frozen: true, mode: "frozen", banners updated)
→ 200  Status   (already frozen — idempotent)
```

## POST /api/unfreeze — deactivate emergency freeze _(Phase 2.5c-3)_

No request body. Clears the emergency freeze (`frozen = false`, mode reverts to previous operating mode).
Returns the fresh `Status`.

```
POST /api/unfreeze
→ 200  Status   (frozen: false, mode restored)
→ 200  Status   (was not frozen — idempotent)
```

## POST /api/deadguard/keep — signal USER_ACTED to suppress deadguard _(Phase 2.5c-3)_

No request body. Records a USER_ACTED event for the current gameweek, which prevents deadguard from triggering
its automatic actions for that GW (equivalent to the user confirming "I'm watching, do nothing"). Returns the
fresh `Status` (the deadguard warning banner will be absent after this call).

```
POST /api/deadguard/keep
→ 200  Status   (deadguard warning banner removed)
→ 200  Status   (already USER_ACTED this GW — idempotent)
```

---

## Notes for the two sides

- **Dashboard (frontend agent):** build every section against mock JSON matching the shapes above. Treat _(forthcoming)_ fields as nullable and design empty/loading states. Mobile-first PWA (`product-spec.md`, `architecture.md`). Don't compute anything — just render + accept input.
- **Backend (analytics/decision agents):** these are thin FastAPI read endpoints over the DB + decision outputs. `status`, `squad` (minus xP), `fixtures/planner` (FDR), and `activity` are buildable now; `captain`/`transfers`/`chips` and the `xp_*` fields land as those slices complete.
- This contract is the source of truth for the interface; if a shape must change, update it here first and both sides follow.
