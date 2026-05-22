# Phase 1 PWA Dashboard (Interface Layer) — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** The entire Phase 1 Interface layer — a single-page, installable, mobile-first PWA that renders all seven dashboard sections from `product-spec.md` against the shapes in `api-contract.md`. Built with **mock fixtures only**; wiring to the real `/api` is a deliberate, single-file, out-of-scope follow-up.
- **Slice goal:** `npm run dev` serves an installable PWA that renders Header, My Team, Captain, Transfers, Chip, Fixture Planner, and Activity Log — including the graceful null/empty states for all _(forthcoming)_ fields — driven entirely by typed mock data behind one swappable client module.

First code in the Interface layer. Built in parallel with the analytics/decision backend, isolated on branch `feat/dashboard` in a worktree, never touching the Python `src/`.

---

## 1. Context

`product-spec.md` ("Dashboard (Phase 1)") defines seven sections, top to bottom. `api-contract.md` defines the exact `GET /api/*` JSON shapes the backend will fulfil and the dashboard renders. The contract splits fields into:

- **_(live)_** — backed by data already in the DB: `/status`, `/squad` (minus `xp_*`), `/fixtures/planner` (FDR), `/activity`.
- **_(forthcoming)_** — depend on decision/xP slices not yet built: `/captain`, `/transfers`, `/chips`, and `xp_next`/`xp_next5`/`free_transfers`. The backend returns `null`/`[]` for these until those slices land.

This means roughly half the dashboard's data is *absent at launch*. The null/empty state is therefore a **first-class design target**, not an edge case.

Two hard constraints from `CLAUDE.md` / `architecture.md`:

- **B2 — the Interface never computes.** It renders contract payloads and accepts user input. It does not derive xP, rank captains, or compute FDR. The single permitted "computation" is presentational: mapping an FDR integer (1–5) to a colour, which is display logic, not decision logic.
- **B3 — single-tenant.** No multi-user, no social, no auth UI in Phase 1.

`risks.md` D1 (✅ resolved) fixes the stack: **SvelteKit PWA** via `@vite-pwa/sveltekit`. `architecture.md` confirms mobile-first, installable, no native app.

The brief also requires that swapping mocks → the real backend later is a **one-file change**.

## 2. Decisions locked (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Aesthetic | FPL-native *mental model*, command-deck *execution* | Familiarity reduces friction (the product's animating goal); dark/data-dense/tabular execution keeps it distinctive (B9), avoiding the generic-FPL-clone trap. |
| Navigation | Single vertical scroll + sticky quick-jump chip row | Glanceable summary in one scroll; instant jump to any section under deadline pressure. |
| Fixture grid on mobile | Fit-to-width compact grid (no horizontal scroll) | All 5–6 GW columns visible at once → best for spotting fixture swings; each cell shows its FDR number for accessibility. |
| Theme | Dark-first only (no light toggle) | YAGNI for a single-user tool; one palette to polish and test. |
| Data/mock strategy | One typed `lib/api/client.ts` module, one function per endpoint, returning imported fixtures; `+page.ts` `load()` calls it | Idiomatic SvelteKit *and* a literal one-file swap point to real `/api`. No extra deps (MSW rejected on YAGNI). |
| Mock scenarios | `?mock=full` (default, all populated) and `?mock=launch` (forthcoming = null/empty) | Demonstrates both the polished state and the real day-one graceful-degradation from one build. |
| Type safety | `lib/types.ts` mirrors `api-contract.md` exactly | The contract becomes compile-checked; fixtures are typed so they can't drift from the shapes. |

## 3. Architecture & file structure

Top-level `frontend/` directory, never colliding with the Python `src/`:

```
frontend/
├── package.json
├── svelte.config.js
├── vite.config.ts            # @vite-pwa/sveltekit plugin (manifest + SW)
├── tsconfig.json
├── static/
│   ├── icons/                # PWA icons: 192, 512, maskable
│   ├── favicon.png
│   └── robots.txt
└── src/
    ├── app.html
    ├── app.css               # dark design tokens + FDR colour scale + base styles
    ├── lib/
    │   ├── types.ts          # TS types mirroring api-contract.md (the contract in code)
    │   ├── api/
    │   │   └── client.ts     # ← THE single mock↔real swap point (one fn per endpoint)
    │   ├── mocks/
    │   │   ├── full.ts       # everything populated (default scenario)
    │   │   └── launch.ts     # forthcoming = null/empty (real day-one)
    │   ├── fdr.ts            # FDR(1-5) → CSS colour-token mapping (presentational only)
    │   ├── format.ts        # tiny display helpers (countdown, "—" for null, £m)
    │   └── components/
    │       ├── Header.svelte
    │       ├── Countdown.svelte
    │       ├── SectionNav.svelte
    │       ├── Pitch.svelte
    │       ├── PlayerCard.svelte
    │       ├── CaptainPicks.svelte
    │       ├── TransferIdeas.svelte
    │       ├── ChipRecommendation.svelte
    │       ├── FixturePlanner.svelte
    │       ├── ActivityLog.svelte
    │       ├── Section.svelte        # shared section shell (title + anchor id)
    │       └── EmptyState.svelte     # shared null/forthcoming/empty messaging
    └── routes/
        ├── +layout.svelte           # app shell, global styles
        ├── +page.ts                 # load() → client.getDashboard() (mocks now)
        └── +page.svelte             # composes the seven sections in order
```

Data flow (Interface-only, respecting B2):

```
+page.ts load()  →  lib/api/client.ts  →  lib/mocks/{full|launch}.ts   (today)
                                       →  fetch('/api/...')             (future: one-file swap)
        ↓
+page.svelte  →  section components  →  render only (no computation)
```

## 4. The contract in code (`lib/types.ts`)

One exported type per endpoint payload, mirroring `api-contract.md` field-for-field, with `(forthcoming)` fields typed as nullable:

- `Status` — `current_gw:number; next_gw:number|null; deadline_utc:string; mode:Mode; data_fresh_as_of_utc:string; banners:Banner[]`
- `Mode = 'auto'|'manual'|'hybrid'|'deadguard'|'frozen'`; `Banner = {level:'info'|'warning'|'error'; text:string}`
- `Squad` — `gw; bank; team_value; free_transfers:number|null; players:SquadPlayer[]` (exactly 15)
- `SquadPlayer` — live fields + `xp_next:number|null; xp_next5:number|null`
- `Position = 'GKP'|'DEF'|'MID'|'FWD'`; `PlayerStatus = 'a'|'d'|'i'|'s'|'u'`
- `Captain` — `picks:CaptainPick[]; vice_player_id:number|null`
- `Transfers` — `suggestions:TransferSuggestion[]; empty_reason:string|null`
- `Chips` — `recommendation: ChipRecommendation|null`; `Chip = 'wildcard'|'free_hit'|'bench_boost'|'triple_captain'`
- `Planner` — `horizon:number[]; rows:PlannerRow[]`; cell entries may be `null` (blank GW)
- `Activity` — `entries:ActivityEntry[]`
- `Dashboard` — aggregate `{status, squad, captain, transfers, chips, planner, activity}` returned by the client in one call.

A non-200 error shape `{error:string}` is modelled and surfaced as a section/page error state.

## 5. The data client (`lib/api/client.ts`) — the single swap point

```ts
// Scenario selection is mock-only and disappears when wired to the real API.
export async function getDashboard(scenario: MockScenario): Promise<Dashboard> {
  // TODAY: return the chosen mock fixture set (full | launch).
  // INTEGRATION POINT — to wire the real backend, replace the body below with
  // parallel fetches of GET /api/{status,squad,captain,transfers,chips,
  // fixtures/planner,activity} and return the assembled Dashboard. Nothing
  // else in the app changes.
  return scenario === 'launch' ? launchMock : fullMock;
}
```

`+page.ts` reads `?mock=` (default `full`), calls `getDashboard`, and returns the typed `Dashboard` to `+page.svelte`. The `INTEGRATION POINT` comment is the only place that knows where data comes from.

## 6. The seven sections + null/empty behaviour

| # | Section | Component | Live now | Graceful state when forthcoming/empty |
|---|---|---|---|---|
| 1 | Header | `Header` + `Countdown` | GW, deadline countdown, mode pill, banners | `next_gw` null → omit; `mode` always `manual`; `banners:[]` → nothing |
| 2 | My Team | `Pitch` + `PlayerCard` | 15 players in pitch rows (GKP/DEF/MID/FWD); name, pos, team, price, status flag, C/V armband | `xp_next`/`xp_next5` null → "—"; `free_transfers` null → hide that stat |
| 3 | Captain | `CaptainPicks` | — | `picks:[]` → "Captain ranker not yet available — arrives with the decision engine." (component keys off data shape, never the scenario name) |
| 4 | Transfers | `TransferIdeas` | — | `suggestions:[]` → show `empty_reason` ("No transfers worth making this GW.") or, if null, the forthcoming message |
| 5 | Chip | `ChipRecommendation` | — | `recommendation:null` → **entire section + its nav chip hidden** (only visible when a chip is flagged, per spec) |
| 6 | Fixture Planner | `FixturePlanner` | full FDR grid, colour-coded | `null` cell → blank-GW "—"; grid itself is always live |
| 7 | Activity Log | `ActivityLog` | recent entries (~20) | `entries:[]` → "No decisions logged yet." |

Loading state: while `load()` resolves, sections render lightweight skeletons (the PWA may serve cached fixtures instantly, so this is brief but defined).

## 7. FDR colour mapping (`lib/fdr.ts`) — the only Interface "logic"

Five CSS custom properties `--fdr-1 … --fdr-5` on an FPL-familiar green→red scale (1 = easiest/green, 3 = neutral, 5 = hardest/red), tuned for a dark background. `fdr.ts` exposes `fdrToken(value)` returning the token, and a `cellFdr(row, cell)` helper that picks **`fdr_attack` for FWD/MID** and **`fdr_defense` for DEF/GKP** per the contract. Every cell also renders its integer value (colour-blind safety + the brief's requirement). This is presentational mapping only — no difficulty is *computed*, just coloured.

## 8. PWA setup

- `@vite-pwa/sveltekit` with `registerType: 'autoUpdate'`.
- **Web manifest:** name "FPL Autopilot", short_name "Autopilot", standalone display, dark theme/background colour, portrait, maskable + any-purpose icons (192/512).
- **Service worker:** Workbox via the plugin — precache the app shell; runtime-cache the (future) `/api` GETs with a network-first strategy so the dashboard opens offline with last-known data (matches "never fall a GW behind").
- Installable to home screen; verified by Lighthouse "Installable" + PWA category checks.

## 9. Mock fixtures

`full.ts` and `launch.ts` each export a complete `Dashboard`. Realistic, recognisable data (real-ish team short codes, plausible prices/xP, a 15-player squad with one captain + one vice, a 5-GW horizon with at least one blank-GW `null` cell and a spread of FDR 1–5). `full` populates every forthcoming field; `launch` sets `xp_*`, `free_transfers` → null, `captain.picks` → [], `transfers.suggestions` → [] with a null/forthcoming reason, `chips.recommendation` → null, `activity.entries` → []. Both share the same live `status`/`squad`-core/`planner` data so the contrast is purely the forthcoming fields.

## 10. Out of scope (explicit)

- **Wiring to the real backend.** Single integration point: the body of `getDashboard` in `client.ts`, marked `// INTEGRATION POINT`. No real network calls on this branch.
- Anything Phase 2+: auth UI, mode switching controls, Telegram, executing actions, write endpoints.
- Multi-user / social / light theme / native app (B3, locked decisions).
- Any change under `src/` or to existing `docs/` files (only this spec + its plan are added under `docs/superpowers/`).

## 11. Verification / definition of done

1. `npm run dev` serves the app; all seven sections render under `?mock=full`.
2. `?mock=launch` shows every graceful null/empty state (xP "—", captain/transfers/activity empty messages, chip section hidden).
3. Fixture grid colours cells correctly (attack vs defense by position) with visible numbers, fits phone width with no horizontal scroll, and shows a blank-GW "—".
4. `npm run build` completes clean; `npm run preview` serves the built PWA.
5. Lighthouse: PWA installable + category checks pass; mobile layout is the primary, verified viewport.
6. App is installable to home screen (manifest + service worker registered).
7. Work committed on `feat/dashboard`; PR opened against `main`. Python `src/` untouched.
