# Dashboard Deadguard/Freeze Banner + Controls — Design (Phase 2.5c-3)

**Status:** approved 2026-05-24
**Slice:** Phase 2.5c-3 (last of the 2.5c bundle; 2.5c-1 late-news + 2.5c-2 undo are done). The dashboard
half of `docs/deadguard.md`: show deadguard/freeze state, and offer the **state-only** controls
(Keep-as-is, Freeze/Unfreeze). Multi-device folds in via polling. **The last Phase-2 slice.**
**Depends on:** Phase-1 dashboard (FastAPI `src/interface/{api,queries,deps}.py` + SvelteKit
`frontend/`), 2.7 override (`src/execution/override.py`: `is_frozen`/`status`/`freeze`/`unfreeze`),
2.5a deadguard (`gameweeks.state`/`deadline_utc`/`deadguard_*`, `repository.touch_user_action`).
**Source of truth:** `docs/deadguard.md` §"User opens the dashboard during/after deadguard executed"
(banner + Keep-as-is) and §"Multiple devices" (backend is the single source of truth). The dashboard does
**not** perform live FPL writes — Undo stays on the Telegram/CLI paths shipped in 2.5c-2.

## Goal

When the user opens the dashboard, they immediately see whether deadguard is about to act, has acted, or
the system is frozen — and can act on it without leaving the page: tap **Keep as is** to stop an imminent
deadguard, or **Freeze/Unfreeze** the autonomous engine. These are reversible DB-state toggles — no FPL
write, no master key — so the always-on web layer never holds the FPL session (security boundary, B7 ethos).

## Decisions (locked — brainstorming 2026-05-24)

| Decision | Choice |
|----------|--------|
| Dashboard write scope | **State-only:** Keep-as-is (→ USER_ACTED) + Freeze/Unfreeze. NO dashboard Undo (live FPL write) — it stays on Telegram ↩️ + CLI (2.5c-2); the banner shows an "undo via Telegram/CLI" hint. |
| Auth | **None, localhost-bound.** Consistent with the existing no-auth GET API; the writes are non-destructive, reversible, secret-free. `serve` binds **127.0.0.1 by default** (was 0.0.0.0) so the now-mutating API isn't LAN-reachable unless the user opts in with `--host 0.0.0.0`. |
| Banner display | Reuse the existing infra: `get_status` already returns `banners` and `Header.svelte` already renders them + `.dot.frozen`/`.dot.deadguard`. Backend just **populates** them. |
| Freeze control | A single header **Freeze/Unfreeze toggle** driven by a new `status.frozen: bool` (not an always-present banner). Contextual actions (Keep) ride on the banner via an optional `Banner.action`. |
| Live state / multi-device | The dashboard **polls** `/api/status` (~30 s + on focus + after each action). Backend is the single source of truth, so a second device reflects changes within a poll. No SSE/WebSocket. |
| Decision logic | **No `decision-engine.md` change** — this is display + state toggles, no threshold/EP/FDR. |

## Architecture

```
src/interface/queries.py   ← get_status gains `frozen` + populated `banners` (with optional action); _status_banners helper
src/interface/api.py       ← POST /api/freeze, /api/unfreeze, /api/deadguard/keep; CORS allow POST
src/cli.py                 ← serve() + the `serve` subparser default host 0.0.0.0 -> 127.0.0.1
frontend/src/lib/types.ts  ← Status.frozen: boolean; Banner.action?: {label, endpoint}
frontend/src/lib/api/client.ts ← fetchStatus(fetch); postAction(path, fetch)
frontend/src/lib/components/Header.svelte ← Freeze/Unfreeze toggle + banner action buttons
frontend/src/routes/+page.svelte ← poll fetchStatus (30s + focus + after action) into reactive status
docs/api-contract.md       ← Status.frozen, Banner.action, the 3 POST endpoints
docs/deadguard.md          ← dashboard banner/controls + multi-device note
docs/runbook.md            ← 127.0.0.1 bind note
```
B2: `queries`/`api` (interface) read the Data Layer + `override` (execution) — interface→lower, allowed.
The web layer performs only DB-state writes (`touch_user_action`, `override.freeze/unfreeze`) — never an
FPL call, never the master key.

## §1 Backend — `get_status` populates `frozen` + `banners`

`get_status` adds `frozen` and a populated `banners` list (currently `[]`). New helper:
```python
def _status_banners(conn, nxt, frozen_status, cfg, now):
    banners = []
    if frozen_status is not None:                       # override.status(conn)
        banners.append({"level": "error",
                        "text": f"Auto-execution frozen — {frozen_status['reason']}."})
    if nxt is None:
        return banners
    state = nxt["state"]
    deadline = datetime.fromisoformat(nxt["deadline_utc"]) if nxt["deadline_utc"] else None
    if state == "DEADGUARD_EXECUTED":
        banners.append({"level": "info",
                        "text": "Deadguard set your team this gameweek. "
                                "Undo a transfer via Telegram or `undo-transfer` before the deadline."})
    elif (state == "PENDING" and not frozen_status and config.deadguard_enabled(cfg)
          and deadline is not None):
        mins = (deadline - now).total_seconds() / 60
        if 0 < mins <= config.deadguard_warning_minutes(cfg):
            banners.append({"level": "warning",
                            "text": f"Deadguard will set your team in ~{int(mins)} min unless you act.",
                            "action": {"label": "Keep as is", "endpoint": "/api/deadguard/keep"}})
    return banners
```
`get_status` returns the existing fields plus `"frozen": bool(frozen_status)` and `"banners": _status_banners(...)`.
It reads `override.status(conn)` and the next GW's `state`/`deadline_utc`. Pure read; no decision change.
(`get_status`'s `SELECT ... WHERE is_next=1` is extended to include `state`.)

## §2 Backend — write endpoints (`api.py`), DB-state only

Allow POST in CORS (`allow_methods=["GET", "POST"]`) and add three endpoints; each returns the fresh status:
```python
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
        repository.touch_user_action(conn, nxt["id"])      # -> USER_ACTED
    return queries.get_status(conn)
```
No master key, no FPL session — pure DB-state writes via the existing `override`/`repository` helpers.

## §3 Backend — bind localhost by default

`cli.serve(host="0.0.0.0", ...)` → `host="127.0.0.1"`, and the `serve` subparser `--host` default
`"0.0.0.0"` → `"127.0.0.1"`. The user opts into LAN exposure with `--host 0.0.0.0`. (The existing
`test_serve_*` tests monkeypatch `uvicorn.run`, so the host value isn't asserted — they stay green.)

## §4 Frontend — types + client

`types.ts`:
```ts
export interface Banner { level: 'info' | 'warning' | 'error'; text: string; action?: { label: string; endpoint: string }; }
export interface Status { /* ...existing... */ frozen: boolean; banners: Banner[]; }
```
`client.ts`:
```ts
export async function fetchStatus(fetchFn: Fetch = fetch): Promise<Status> {
    return getJson<Status>('/api/status', fetchFn);
}
export async function postAction(path: string, fetchFn: Fetch = fetch): Promise<Status> {
    const res = await fetchFn(path, { method: 'POST' });
    if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
    return res.json() as Promise<Status>;          // endpoints return the fresh status
}
```

## §5 Frontend — Header controls + polling

`Header.svelte` (takes `status` + an `onstatus` callback to push updates up):
- A **Freeze/Unfreeze toggle** in the header row: shows "Unfreeze" when `status.frozen`, else "Freeze";
  on click → `postAction(status.frozen ? '/api/unfreeze' : '/api/freeze')` → `onstatus(newStatus)`.
- Each banner with an `action` renders a button → `postAction(action.endpoint)` → `onstatus(newStatus)`.
- Errors are caught and surfaced as a transient inline note (never crash the header).

`+page.svelte`: hold `status` in `$state` (seeded from `data.dashboard.status`), pass to `Header` with
`onstatus`; poll `fetchStatus` every 30 s and on `window` focus, updating `status` (skip in `mock` mode).
Multi-device: a second dashboard polling the same backend reflects an action within one interval.

## Safety & B-rules
- **B7:** the web layer never holds the master key or makes an FPL call; the new writes are DB-state only.
  Default `127.0.0.1` bind keeps the mutating API off the LAN.
- **B8:** no dashboard live-write path; Undo (the only live action) stays on the bounded Telegram/CLI executors.
- **B9/B10:** `override.freeze/unfreeze` already log to `activity_log`; Keep-as-is sets USER_ACTED (logged via
  the existing deadguard state machinery). No new notification path.
- **B2:** interface reads execution/data; no inversion. No `decision-engine.md` change (B4 N/A).
- **R3:** the agent never runs the live server; tests are fixtures-only (FastAPI `TestClient` on in-memory
  DB; vitest with mocked fetch).

## Testing
- **pytest** (`tests/test_api.py` extends): `get_status` returns `frozen` + the right banner per state —
  frozen → error banner + `frozen:true`; PENDING within warn window → warning banner + Keep action;
  DEADGUARD_EXECUTED → info banner; normal → no banner. The 3 POST endpoints mutate state and return the
  fresh status (`POST /api/freeze` → `frozen:true`; `/api/unfreeze` → `frozen:false`; `/api/deadguard/keep`
  → next GW state USER_ACTED). CORS allows POST. `cli.serve` default host is `127.0.0.1`.
- **vitest** (`frontend`): `client.postAction`/`fetchStatus` hit the right path/method; `Header` renders
  the Freeze toggle (label flips on `status.frozen`) and a banner Keep button, and clicking calls
  `postAction` + `onstatus`. `npm test` (= `vitest run`) green.
- Full `.venv/bin/pytest -q` green; `cd frontend && npm test` green.

## Scope boundary
- **IN:** `get_status` frozen+banners, 3 write endpoints, 127.0.0.1 bind, types+client+Header+polling,
  api-contract.md/deadguard.md/runbook.md updates.
- **OUT:** dashboard live **Undo** (stays Telegram/CLI — banner hint only); any auth/token (localhost trust);
  SSE/WebSocket push (polling instead); a freeze *reason* input on the dashboard (fixed "frozen from dashboard").
- **OUT → Phase 3:** anything AI/conversational. After this slice, **Phase 2 is complete.**

## Definition of done (CLAUDE.md B14)
- Opening the dashboard shows: a red banner + Unfreeze toggle when frozen; an amber "deadguard in ~Xm" banner
  with a **Keep as is** button in the warning window (tapping it → USER_ACTED, banner clears on next poll); an
  info banner after deadguard executed (with the Telegram/CLI undo hint). A **Freeze** toggle is always in the
  header. A second device reflects any of these within ~30 s. The server binds 127.0.0.1 by default.
- All tests green (pytest + vitest); the web layer makes no FPL call and holds no key; no `decision-engine.md`
  change; api-contract.md/deadguard.md/runbook.md updated; the agent never ran the live server.
- Manual smoke check (out of band, by the user): run `fpl-autopilot serve`, open the dashboard, tap Freeze →
  red banner appears + (via Telegram/CLI) `freeze-status` shows frozen; tap Unfreeze → clears; force a GW into
  the warning window and confirm the Keep-as-is button sets USER_ACTED.
