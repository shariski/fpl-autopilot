# Phase 1 PWA Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build an installable, mobile-first SvelteKit PWA (the Interface layer) that renders all seven Phase 1 dashboard sections from typed mock data, including graceful null/empty states, behind a single swappable data client.

**Architecture:** SvelteKit SPA (`adapter-static`, `ssr=false`) made installable via `@vite-pwa/sveltekit`. A `+page.ts` `load()` reads a `?mock=full|launch` param and calls `lib/api/client.ts` — the one module that knows where data comes from (mock fixtures today, real `/api` later via a single marked integration point). Components are pure renderers (CLAUDE.md B2): they receive typed contract payloads and display them, never compute. The only display-side mapping is FDR integer → colour token.

**Tech Stack:** SvelteKit (Svelte 5 runes), TypeScript, Vite, `@vite-pwa/sveltekit`, `@sveltejs/adapter-static`, Vitest + jsdom + `@testing-library/svelte` + `@testing-library/jest-dom`, `@vite-pwa/assets-generator`.

**Source of truth:** `docs/superpowers/specs/2026-05-22-dashboard-pwa-design.md` and `docs/api-contract.md`. Everything lives under top-level `frontend/`. Never touch `src/` (Python backend) or existing `docs/` files.

**Conventions for every task:** run all commands from `frontend/` unless stated. Commit messages end with the repo's `Co-Authored-By` trailer. The branch is `feat/dashboard` (already created in an isolated worktree).

---

## File map (locked decomposition)

```
frontend/
├── package.json, svelte.config.js, vite.config.ts, tsconfig.json   # Tasks 1-3
├── vitest-setup.ts                                                 # Task 2
├── scripts/ (none — icons via npx)                                 # Task 3
├── static/
│   ├── logo.svg                                                    # Task 3
│   └── icons/  (generated: pwa-192x192.png, pwa-512x512.png, maskable-icon-512x512.png, apple-touch-icon-180x180.png, favicon.ico)  # Task 3
└── src/
    ├── app.html                                                    # Task 3
    ├── app.css                 # dark tokens + FDR scale + base     # Task 8
    ├── lib/
    │   ├── types.ts            # contract mirror                    # Task 4
    │   ├── fdr.ts              # FDR(1-5)→token, position routing   # Task 5
    │   ├── format.ts          # dash(), money(), countdown()       # Task 6
    │   ├── mocks/full.ts      # fully-populated Dashboard          # Task 7
    │   ├── mocks/launch.ts    # forthcoming = null/empty           # Task 7
    │   ├── api/client.ts      # getDashboard(scenario) — SWAP POINT# Task 7
    │   └── components/
    │       ├── Section.svelte           # shared section shell      # Task 8
    │       ├── EmptyState.svelte        # shared empty/forthcoming  # Task 8
    │       ├── Header.svelte            #                           # Task 9
    │       ├── Countdown.svelte         #                           # Task 9
    │       ├── SectionNav.svelte        #                           # Task 9
    │       ├── Pitch.svelte             #                           # Task 10
    │       ├── PlayerCard.svelte        #                           # Task 10
    │       ├── CaptainPicks.svelte      #                           # Task 11
    │       ├── TransferIdeas.svelte     #                           # Task 11
    │       ├── ChipRecommendation.svelte#                           # Task 12
    │       ├── ActivityLog.svelte       #                           # Task 12
    │       └── FixturePlanner.svelte    #                           # Task 13
    └── routes/
        ├── +layout.ts          # ssr=false; prerender=false        # Task 3
        ├── +layout.svelte      # imports app.css, app shell        # Task 8
        ├── +page.ts            # load() → client.getDashboard()    # Task 8
        └── +page.svelte        # composes the seven sections       # Task 14
```

Tests live next to source as `*.test.ts` (logic) / `*.svelte.test.ts` (components) per Vitest convention.

---

## Task 1: Scaffold the SvelteKit app

**Files:**
- Create: `frontend/` (entire SvelteKit minimal skeleton)

- [x] **Step 1: Scaffold with the `sv` CLI (minimal, TypeScript)**

Run from the worktree root (`/Users/falah/Work/fpl-autopilot/.claude/worktrees/feat+dashboard`):

```bash
npx sv create frontend --template minimal --types ts --no-add-ons --install npm
```

If the CLI is interactive instead of accepting flags, answer: template = **SvelteKit minimal**, type checking = **TypeScript**, additional options = **none** (we add testing/PWA manually for full control), package manager = **npm**.

- [x] **Step 2: Verify the dev server boots**

```bash
cd frontend && npm run dev -- --port 5173 &
sleep 4 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/ ; kill %1
```
Expected: `200`.

- [x] **Step 3: Commit**

```bash
git add frontend && git commit -m "feat(dashboard): scaffold SvelteKit minimal TS app

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add the testing stack (Vitest + Testing Library)

**Files:**
- Modify: `frontend/package.json` (deps + scripts)
- Modify: `frontend/vite.config.ts` (test config)
- Create: `frontend/vitest-setup.ts`
- Create: `frontend/src/lib/smoke.test.ts` (temporary, proves the runner)

- [x] **Step 1: Install test dependencies**

```bash
cd frontend
npm i -D vitest jsdom @testing-library/svelte @testing-library/jest-dom @testing-library/user-event
```

- [x] **Step 2: Create the test setup file**

`frontend/vitest-setup.ts`:
```ts
import '@testing-library/jest-dom/vitest';
```

- [x] **Step 3: Configure Vitest inside `vite.config.ts`**

Replace `frontend/vite.config.ts` with:
```ts
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [sveltekit()],
	test: {
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./vitest-setup.ts'],
		include: ['src/**/*.{test,spec}.{js,ts}']
	},
	// @testing-library/svelte needs the browser build of Svelte under test.
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined
});
```

- [x] **Step 4: Add the `test` script to `package.json`**

In `frontend/package.json` `"scripts"`, add:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [x] **Step 5: Write a smoke test**

`frontend/src/lib/smoke.test.ts`:
```ts
import { describe, it, expect } from 'vitest';

describe('test runner', () => {
	it('runs', () => {
		expect(1 + 1).toBe(2);
	});
});
```

- [x] **Step 6: Run it and verify it passes**

Run: `cd frontend && npm test`
Expected: 1 passed.

- [x] **Step 7: Delete the smoke test and commit**

```bash
cd frontend && rm src/lib/smoke.test.ts
git add frontend && git commit -m "test(dashboard): add Vitest + Testing Library (jsdom)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: SPA adapter + PWA (manifest, service worker, icons)

**Files:**
- Modify: `frontend/svelte.config.js`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/routes/+layout.ts`
- Modify: `frontend/src/app.html`
- Create: `frontend/static/logo.svg`
- Create: `frontend/static/icons/*` (generated)

- [x] **Step 1: Install adapter-static + PWA plugin + asset generator**

```bash
cd frontend
npm i -D @sveltejs/adapter-static @vite-pwa/sveltekit @vite-pwa/assets-generator
```

- [x] **Step 2: Configure SPA in `svelte.config.js`**

Replace `frontend/svelte.config.js` with:
```js
import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({ fallback: '200.html' }) // SPA fallback
	}
};

export default config;
```

- [x] **Step 3: Make the app a client-side SPA**

`frontend/src/routes/+layout.ts`:
```ts
// SPA: no SSR, no prerender. Data is fetched client-side (mocks now, /api later).
export const ssr = false;
export const prerender = false;
```

- [x] **Step 4: Create a distinctive logo source**

`frontend/static/logo.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0b0f14"/>
  <path d="M256 96 L400 416 H320 L296 352 H216 L192 416 H112 Z M244 280 H268 L256 232 Z"
        fill="#00e6a8"/>
  <path d="M150 150 L362 150" stroke="#1f6feb" stroke-width="22" stroke-linecap="round"/>
</svg>
```

- [x] **Step 5: Generate PWA icons**

```bash
cd frontend
npx @vite-pwa/assets-generator --preset minimal-2023 static/logo.svg
```
Move generated PNG/ICO assets into `static/icons/` (the generator writes alongside the source by default — relocate them):
```bash
cd frontend && mkdir -p static/icons && mv static/pwa-*.png static/maskable-*.png static/apple-touch-icon-*.png static/favicon.ico static/icons/ 2>/dev/null; ls static/icons
```
Expected: `pwa-192x192.png pwa-512x512.png maskable-icon-512x512.png apple-touch-icon-180x180.png favicon.ico` (names may vary slightly by generator version; use the actual generated filenames in Step 6).

- [x] **Step 6: Register the PWA plugin in `vite.config.ts`**

Update `frontend/vite.config.ts` to add `SvelteKitPWA`:
```ts
import { sveltekit } from '@sveltejs/kit/vite';
import { SvelteKitPWA } from '@vite-pwa/sveltekit';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		sveltekit(),
		SvelteKitPWA({
			registerType: 'autoUpdate',
			manifest: {
				name: 'FPL Autopilot',
				short_name: 'Autopilot',
				description: 'Personal Fantasy Premier League decision dashboard',
				lang: 'en',
				theme_color: '#0b0f14',
				background_color: '#0b0f14',
				display: 'standalone',
				orientation: 'portrait',
				start_url: '/',
				icons: [
					{ src: '/icons/pwa-192x192.png', sizes: '192x192', type: 'image/png' },
					{ src: '/icons/pwa-512x512.png', sizes: '512x512', type: 'image/png' },
					{
						src: '/icons/maskable-icon-512x512.png',
						sizes: '512x512',
						type: 'image/png',
						purpose: 'maskable'
					}
				]
			},
			workbox: {
				globPatterns: ['client/**/*.{js,css,ico,png,svg,webp,woff,woff2,html}']
			},
			devOptions: { enabled: true, type: 'module', navigateFallback: '/' }
		})
	],
	test: {
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./vitest-setup.ts'],
		include: ['src/**/*.{test,spec}.{js,ts}']
	},
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined
});
```

- [x] **Step 7: Add manifest + theme links to `app.html`**

In `frontend/src/app.html`, inside `<head>`, add (the PWA plugin injects the manifest, but set theme + apple icon):
```html
		<meta name="theme-color" content="#0b0f14" />
		<link rel="apple-touch-icon" href="/icons/apple-touch-icon-180x180.png" />
```
Also set `<html lang="en">` and ensure the viewport meta is `width=device-width, initial-scale=1`.

- [x] **Step 8: Verify build produces a manifest + service worker**

```bash
cd frontend && npm run build
ls build/manifest.webmanifest build/sw.js 2>/dev/null || ls build/ | grep -E "manifest|sw"
```
Expected: a `*.webmanifest` and a service worker file exist in `build/`.

- [x] **Step 9: Commit**

```bash
git add frontend && git commit -m "feat(dashboard): SPA adapter + installable PWA (manifest, SW, icons)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Contract types (`lib/types.ts`)

**Files:**
- Create: `frontend/src/lib/types.ts`
- Test: `frontend/src/lib/types.test.ts`

- [x] **Step 1: Write the type file (mirrors `api-contract.md`)**

`frontend/src/lib/types.ts`:
```ts
// Mirrors docs/api-contract.md exactly. (forthcoming) fields are nullable.
export type Mode = 'auto' | 'manual' | 'hybrid' | 'deadguard' | 'frozen';
export type Position = 'GKP' | 'DEF' | 'MID' | 'FWD';
export type PlayerStatus = 'a' | 'd' | 'i' | 's' | 'u';
export type Chip = 'wildcard' | 'free_hit' | 'bench_boost' | 'triple_captain';

export interface Banner {
	level: 'info' | 'warning' | 'error';
	text: string;
}

export interface Status {
	current_gw: number;
	next_gw: number | null;
	deadline_utc: string;
	mode: Mode;
	data_fresh_as_of_utc: string;
	banners: Banner[];
}

export interface SquadPlayer {
	id: number;
	web_name: string;
	position: Position;
	team_short: string;
	price: number;
	status: PlayerStatus;
	is_captain: boolean;
	is_vice_captain: boolean;
	multiplier: number; // 0 = bench, 1 = starter, 2 = captain
	xp_next: number | null; // (forthcoming)
	xp_next5: number | null; // (forthcoming)
}

export interface Squad {
	gw: number;
	bank: number;
	team_value: number;
	free_transfers: number | null; // (forthcoming, auth-only)
	players: SquadPlayer[]; // exactly 15
}

export interface CaptainPick {
	player_id: number;
	web_name: string;
	xp: number;
	fixture: string;
	reason: string;
}
export interface Captain {
	picks: CaptainPick[]; // top 5, ranked; [] until built
	vice_player_id: number | null;
}

export interface TransferSide {
	player_id: number;
	web_name: string;
	price: number;
}
export interface TransferSuggestion {
	out: TransferSide;
	in: TransferSide;
	ep_delta_5gw: number;
	hit_cost: number; // 0, -4, -8 ...
	confidence: number;
}
export interface Transfers {
	suggestions: TransferSuggestion[]; // [] if none worth it
	empty_reason: string | null;
}

export interface ChipRecommendation {
	chip: Chip;
	reason: string;
}
export interface Chips {
	recommendation: ChipRecommendation | null;
}

export interface PlannerCell {
	gw: number;
	opponent_short: string;
	home: boolean;
	fdr_attack: number; // 1-5
	fdr_defense: number; // 1-5
}
export interface PlannerRow {
	player_id: number;
	web_name: string;
	position: Position;
	team_short: string;
	cells: (PlannerCell | null)[]; // null = blank GW
}
export interface Planner {
	horizon: number[];
	rows: PlannerRow[];
}

export interface ActivityEntry {
	ts_utc: string;
	gw: number;
	mode: Mode;
	decision_type: 'captain' | 'transfer' | 'bench' | 'chip' | 'deadguard';
	action_taken: string;
	executed: boolean;
}
export interface Activity {
	entries: ActivityEntry[];
}

export interface ApiError {
	error: string;
}

// Aggregate the client returns in one call (one fetch fan-out later).
export interface Dashboard {
	status: Status;
	squad: Squad;
	captain: Captain;
	transfers: Transfers;
	chips: Chips;
	planner: Planner;
	activity: Activity;
}

export type MockScenario = 'full' | 'launch';
```

- [x] **Step 2: Write a compile-guard test**

`frontend/src/lib/types.test.ts`:
```ts
import { describe, it, expectTypeOf } from 'vitest';
import type { Dashboard, SquadPlayer, PlannerRow } from './types';

describe('contract types', () => {
	it('SquadPlayer xp fields are nullable (forthcoming)', () => {
		expectTypeOf<SquadPlayer['xp_next']>().toEqualTypeOf<number | null>();
	});
	it('PlannerRow cells allow null (blank GW)', () => {
		expectTypeOf<PlannerRow['cells'][number]>().toMatchTypeOf<object | null>();
	});
	it('Dashboard aggregates the seven payloads', () => {
		expectTypeOf<Dashboard>().toHaveProperty('status');
		expectTypeOf<Dashboard>().toHaveProperty('planner');
		expectTypeOf<Dashboard>().toHaveProperty('activity');
	});
});
```

- [x] **Step 3: Run and verify it passes**

Run: `cd frontend && npm test -- types`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/types.test.ts
git commit -m "feat(dashboard): contract types mirroring api-contract.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: FDR colour mapping (`lib/fdr.ts`) — TDD

This is the only display-side mapping the Interface owns (B2-permitted: presentational, not decision logic).

**Files:**
- Create: `frontend/src/lib/fdr.ts`
- Test: `frontend/src/lib/fdr.test.ts`

- [x] **Step 1: Write the failing tests**

`frontend/src/lib/fdr.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { fdrToken, cellFdr } from './fdr';
import type { PlannerCell } from './types';

const cell = (over: Partial<PlannerCell> = {}): PlannerCell => ({
	gw: 38,
	opponent_short: 'BOU',
	home: true,
	fdr_attack: 2,
	fdr_defense: 4,
	...over
});

describe('fdrToken', () => {
	it('maps 1-5 to fdr CSS custom properties', () => {
		expect(fdrToken(1)).toBe('var(--fdr-1)');
		expect(fdrToken(5)).toBe('var(--fdr-5)');
	});
	it('clamps out-of-range values', () => {
		expect(fdrToken(0)).toBe('var(--fdr-1)');
		expect(fdrToken(9)).toBe('var(--fdr-5)');
	});
});

describe('cellFdr', () => {
	it('uses fdr_attack for attackers (FWD/MID)', () => {
		expect(cellFdr('FWD', cell())).toBe(2);
		expect(cellFdr('MID', cell())).toBe(2);
	});
	it('uses fdr_defense for defenders (DEF/GKP)', () => {
		expect(cellFdr('DEF', cell())).toBe(4);
		expect(cellFdr('GKP', cell())).toBe(4);
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- fdr`
Expected: FAIL ("fdrToken is not a function" / module not found).

- [x] **Step 3: Implement `lib/fdr.ts`**

```ts
import type { PlannerCell, Position } from './types';

/** Clamp an FDR to 1-5 and return its CSS colour token. Presentational only. */
export function fdrToken(value: number): string {
	const v = Math.min(5, Math.max(1, Math.round(value)));
	return `var(--fdr-${v})`;
}

/** Per api-contract.md: attackers coloured by fdr_attack, defenders by fdr_defense. */
export function cellFdr(position: Position, cell: PlannerCell): number {
	return position === 'FWD' || position === 'MID' ? cell.fdr_attack : cell.fdr_defense;
}
```

- [x] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- fdr`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/fdr.ts frontend/src/lib/fdr.test.ts
git commit -m "feat(dashboard): FDR colour-token mapping (attack vs defense by position)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Display helpers (`lib/format.ts`) — TDD

**Files:**
- Create: `frontend/src/lib/format.ts`
- Test: `frontend/src/lib/format.test.ts`

- [x] **Step 1: Write the failing tests**

`frontend/src/lib/format.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { dash, money, countdown } from './format';

describe('dash', () => {
	it('renders an em-dash for null/undefined', () => {
		expect(dash(null)).toBe('—');
		expect(dash(undefined)).toBe('—');
	});
	it('passes numbers through with optional fixed decimals', () => {
		expect(dash(7.2)).toBe('7.2');
		expect(dash(31.41, 1)).toBe('31.4');
	});
});

describe('money', () => {
	it('formats £m with one decimal', () => {
		expect(money(14.7)).toBe('£14.7');
		expect(money(2.3)).toBe('£2.3');
	});
});

describe('countdown', () => {
	it('formats remaining time as Hh Mm', () => {
		const now = new Date('2026-05-24T11:00:00Z').getTime();
		expect(countdown('2026-05-24T13:14:00Z', now)).toBe('2h 14m');
	});
	it('shows "Deadline passed" once elapsed', () => {
		const now = new Date('2026-05-24T14:00:00Z').getTime();
		expect(countdown('2026-05-24T13:00:00Z', now)).toBe('Deadline passed');
	});
	it('includes days when more than 24h remain', () => {
		const now = new Date('2026-05-22T13:00:00Z').getTime();
		expect(countdown('2026-05-24T13:00:00Z', now)).toBe('2d 0h 0m');
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- format`
Expected: FAIL.

- [x] **Step 3: Implement `lib/format.ts`**

```ts
/** Render a numeric value, or an em-dash when it is null/undefined (forthcoming fields). */
export function dash(value: number | null | undefined, decimals?: number): string {
	if (value === null || value === undefined) return '—';
	return decimals === undefined ? String(value) : value.toFixed(decimals);
}

/** Format money in £m, e.g. 14.7 -> "£14.7". */
export function money(value: number): string {
	return `£${value.toFixed(1)}`;
}

/** Human countdown from now (ms) to an ISO deadline. */
export function countdown(deadlineUtc: string, now: number = Date.now()): string {
	const diff = new Date(deadlineUtc).getTime() - now;
	if (diff <= 0) return 'Deadline passed';
	const totalMin = Math.floor(diff / 60000);
	const days = Math.floor(totalMin / 1440);
	const hours = Math.floor((totalMin % 1440) / 60);
	const mins = totalMin % 60;
	return days > 0 ? `${days}d ${hours}h ${mins}m` : `${hours}h ${mins}m`;
}
```

- [x] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- format`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts
git commit -m "feat(dashboard): display helpers (dash, money, countdown)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Mock fixtures + data client (`lib/mocks`, `lib/api/client.ts`) — TDD

**Files:**
- Create: `frontend/src/lib/mocks/full.ts`
- Create: `frontend/src/lib/mocks/launch.ts`
- Create: `frontend/src/lib/api/client.ts`
- Test: `frontend/src/lib/api/client.test.ts`

- [x] **Step 1: Write the failing test (pins fixture invariants + scenario switch)**

`frontend/src/lib/api/client.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { getDashboard } from './client';

describe('getDashboard', () => {
	it('full scenario: squad has exactly 15 players, one captain, one vice', async () => {
		const d = await getDashboard('full');
		expect(d.squad.players).toHaveLength(15);
		expect(d.squad.players.filter((p) => p.is_captain)).toHaveLength(1);
		expect(d.squad.players.filter((p) => p.is_vice_captain)).toHaveLength(1);
	});
	it('full scenario: forthcoming fields are populated', async () => {
		const d = await getDashboard('full');
		expect(d.squad.players[0].xp_next).not.toBeNull();
		expect(d.captain.picks.length).toBeGreaterThan(0);
		expect(d.chips.recommendation).not.toBeNull();
	});
	it('full scenario: planner horizon length matches each row, with a blank-GW null cell present', async () => {
		const d = await getDashboard('full');
		const n = d.planner.horizon.length;
		expect(n).toBeGreaterThanOrEqual(5);
		for (const row of d.planner.rows) expect(row.cells).toHaveLength(n);
		const hasBlank = d.planner.rows.some((r) => r.cells.some((c) => c === null));
		expect(hasBlank).toBe(true);
	});
	it('full scenario: FDR values across cells span a range (not all identical)', async () => {
		const d = await getDashboard('full');
		const vals = new Set<number>();
		for (const r of d.planner.rows)
			for (const c of r.cells) if (c) vals.add(c.fdr_attack);
		expect(vals.size).toBeGreaterThan(1);
	});
	it('launch scenario: forthcoming fields are null/empty but live data remains', async () => {
		const d = await getDashboard('launch');
		expect(d.squad.players).toHaveLength(15); // squad core still live
		expect(d.squad.players[0].xp_next).toBeNull();
		expect(d.squad.free_transfers).toBeNull();
		expect(d.captain.picks).toEqual([]);
		expect(d.transfers.suggestions).toEqual([]);
		expect(d.chips.recommendation).toBeNull();
		expect(d.activity.entries).toEqual([]);
		expect(d.planner.rows.length).toBeGreaterThan(0); // FDR is live
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- client`
Expected: FAIL (module not found).

- [x] **Step 3: Implement the full mock fixture**

`frontend/src/lib/mocks/full.ts` — a complete, typed `Dashboard`. Squad is a valid 4-4-2 (11 starters mult≥1, 4 bench mult 0); Haaland captain, Salah vice. Planner has 15 rows over a 5-GW horizon `[38,39,40,41,42]` with one blank-GW `null` cell and a 1–5 spread.
```ts
import type { Dashboard, SquadPlayer, PlannerRow } from '../types';

const players: SquadPlayer[] = [
	// Starting XI (multiplier >= 1)
	{ id: 1, web_name: 'Raya', position: 'GKP', team_short: 'ARS', price: 5.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 3.9, xp_next5: 18.2 },
	{ id: 2, web_name: 'Gabriel', position: 'DEF', team_short: 'ARS', price: 6.3, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.4, xp_next5: 20.1 },
	{ id: 3, web_name: 'Saliba', position: 'DEF', team_short: 'ARS', price: 6.1, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.2, xp_next5: 19.6 },
	{ id: 4, web_name: 'Gvardiol', position: 'DEF', team_short: 'MCI', price: 6.5, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.0, xp_next5: 18.4 },
	{ id: 5, web_name: 'Hall', position: 'DEF', team_short: 'NEW', price: 5.4, status: 'd', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 3.6, xp_next5: 16.8 },
	{ id: 6, web_name: 'Salah', position: 'MID', team_short: 'LIV', price: 13.1, status: 'a', is_captain: false, is_vice_captain: true, multiplier: 1, xp_next: 6.1, xp_next5: 28.0 },
	{ id: 7, web_name: 'Saka', position: 'MID', team_short: 'ARS', price: 10.4, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 5.4, xp_next5: 24.7 },
	{ id: 8, web_name: 'Palmer', position: 'MID', team_short: 'CHE', price: 10.8, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 5.0, xp_next5: 23.1 },
	{ id: 9, web_name: 'Mbeumo', position: 'MID', team_short: 'BRE', price: 7.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.6, xp_next5: 21.0 },
	{ id: 10, web_name: 'Haaland', position: 'FWD', team_short: 'MCI', price: 14.7, status: 'a', is_captain: true, is_vice_captain: false, multiplier: 2, xp_next: 7.2, xp_next5: 31.4 },
	{ id: 11, web_name: 'Isak', position: 'FWD', team_short: 'NEW', price: 9.3, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 1, xp_next: 4.8, xp_next5: 22.3 },
	// Bench (multiplier 0)
	{ id: 12, web_name: 'Sels', position: 'GKP', team_short: 'NFO', price: 5.0, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 3.2, xp_next5: 14.9 },
	{ id: 13, web_name: 'Lacroix', position: 'DEF', team_short: 'CRY', price: 4.6, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 3.0, xp_next5: 13.7 },
	{ id: 14, web_name: 'Rogers', position: 'MID', team_short: 'AVL', price: 5.7, status: 'i', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 0.0, xp_next5: 8.1 },
	{ id: 15, web_name: 'Watkins', position: 'FWD', team_short: 'AVL', price: 9.0, status: 'a', is_captain: false, is_vice_captain: false, multiplier: 0, xp_next: 4.1, xp_next5: 19.2 }
];

const horizon = [38, 39, 40, 41, 42];

// FDR cells per player; opponent/home illustrative; one team has a blank GW (null).
const opp: Record<number, ([string, boolean, number, number] | null)[]> = {
	1: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	2: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	3: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	4: [['WHU', true, 1, 2], ['CHE', false, 4, 4], ['TOT', true, 3, 3], ['FUL', false, 2, 2], ['BOU', true, 2, 3]],
	5: [['MCI', false, 5, 5], ['ARS', true, 4, 5], null, ['BUR', false, 1, 1], ['WOL', true, 2, 2]],
	6: [['WHU', false, 2, 2], ['CRY', true, 2, 2], ['BOU', false, 3, 3], ['BHA', true, 3, 3], ['MCI', false, 5, 5]],
	7: [['BOU', true, 2, 3], ['NEW', false, 3, 4], ['LIV', true, 4, 4], ['BHA', false, 3, 3], ['EVE', true, 2, 2]],
	8: [['EVE', true, 2, 2], ['WOL', false, 2, 2], ['ARS', true, 5, 5], ['NEW', false, 4, 4], ['FUL', true, 2, 2]],
	9: [['FUL', true, 2, 2], ['TOT', false, 4, 3], ['EVE', true, 2, 2], ['LIV', false, 5, 5], ['CRY', true, 2, 2]],
	10: [['BOU', false, 3, 3], ['AVL', true, 3, 3], ['CHE', false, 4, 4], ['SOU', true, 1, 1], ['FUL', false, 2, 2]],
	11: [['ARS', true, 4, 5], ['MCI', false, 5, 5], ['WHU', true, 2, 2], ['BRE', false, 3, 3], ['EVE', true, 2, 2]],
	12: [['CHE', true, 4, 4], ['BHA', false, 3, 3], ['MUN', true, 3, 4], ['WHU', false, 2, 2], ['LEE', true, 1, 1]],
	13: [['LEE', true, 1, 1], ['BUR', false, 1, 1], ['NEW', true, 4, 4], ['ARS', false, 5, 5], ['SUN', true, 1, 1]],
	14: [['SUN', true, 1, 1], ['BRE', false, 2, 2], ['MCI', true, 5, 5], ['BOU', false, 3, 3], ['NFO', true, 2, 2]],
	15: [['SUN', true, 1, 1], ['BRE', false, 2, 2], ['MCI', true, 5, 5], ['BOU', false, 3, 3], ['NFO', true, 2, 2]]
};

const rows: PlannerRow[] = players.map((p) => ({
	player_id: p.id,
	web_name: p.web_name,
	position: p.position,
	team_short: p.team_short,
	cells: opp[p.id].map((c, i) =>
		c === null
			? null
			: { gw: horizon[i], opponent_short: c[0], home: c[1], fdr_attack: c[2], fdr_defense: c[3] }
	)
}));

export const fullMock: Dashboard = {
	status: {
		current_gw: 38,
		next_gw: null,
		deadline_utc: '2026-05-24T13:00:00Z',
		mode: 'manual',
		data_fresh_as_of_utc: '2026-05-22T09:00:00Z',
		banners: [{ level: 'warning', text: 'Understat data is 8 days stale.' }]
	},
	squad: { gw: 37, bank: 2.3, team_value: 99.7, free_transfers: 1, players },
	captain: {
		picks: [
			{ player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BOU (H)', reason: 'Highest xP (7.2). Next best Salah 6.1 — gap 1.1. Home vs FDR-3 defense.' },
			{ player_id: 6, web_name: 'Salah', xp: 6.1, fixture: 'WHU v LIV (A)', reason: 'Second highest xP. Strong away record; FDR-2 attack matchup.' },
			{ player_id: 7, web_name: 'Saka', xp: 5.4, fixture: 'ARS v BOU (H)', reason: 'Home vs FDR-2 attack. On set pieces.' },
			{ player_id: 8, web_name: 'Palmer', xp: 5.0, fixture: 'CHE v EVE (H)', reason: 'Penalties + home vs FDR-2.' },
			{ player_id: 11, web_name: 'Isak', xp: 4.8, fixture: 'NEW v ARS (H)', reason: 'In form, but FDR-4 defense caps ceiling.' }
		],
		vice_player_id: 6
	},
	transfers: {
		suggestions: [
			{ out: { player_id: 5, web_name: 'Hall', price: 5.4 }, in: { player_id: 101, web_name: 'Aina', price: 5.0 }, ep_delta_5gw: 2.6, hit_cost: 0, confidence: 74 },
			{ out: { player_id: 14, web_name: 'Rogers', price: 5.7 }, in: { player_id: 102, web_name: 'Gordon', price: 7.5 }, ep_delta_5gw: 4.1, hit_cost: -4, confidence: 69 },
			{ out: { player_id: 13, web_name: 'Lacroix', price: 4.6 }, in: { player_id: 103, web_name: 'Andersen', price: 4.6 }, ep_delta_5gw: 1.4, hit_cost: 0, confidence: 61 }
		],
		empty_reason: null
	},
	chips: {
		recommendation: { chip: 'bench_boost', reason: 'DGW: all 15 have fixtures; combined bench xP 5.2 (> threshold 4).' }
	},
	planner: { horizon, rows },
	activity: {
		entries: [
			{ ts_utc: '2026-05-22T19:30:00Z', gw: 38, mode: 'manual', decision_type: 'captain', action_taken: 'Captain set to Haaland', executed: false },
			{ ts_utc: '2026-05-22T03:05:00Z', gw: 38, mode: 'manual', decision_type: 'transfer', action_taken: 'Generated 3 transfer suggestions', executed: false },
			{ ts_utc: '2026-05-22T03:01:00Z', gw: 38, mode: 'manual', decision_type: 'bench', action_taken: 'Recomputed FDR for GW38–42', executed: false }
		]
	}
};
```

- [x] **Step 4: Implement the launch mock (DRY — derive from full, null the forthcoming fields)**

`frontend/src/lib/mocks/launch.ts`:
```ts
import type { Dashboard } from '../types';
import { fullMock } from './full';

// Real day-one: live data present (status, squad core, FDR planner); forthcoming = null/empty.
export const launchMock: Dashboard = {
	status: fullMock.status,
	squad: {
		...fullMock.squad,
		free_transfers: null,
		players: fullMock.squad.players.map((p) => ({ ...p, xp_next: null, xp_next5: null }))
	},
	captain: { picks: [], vice_player_id: null },
	transfers: { suggestions: [], empty_reason: null },
	chips: { recommendation: null },
	planner: fullMock.planner,
	activity: { entries: [] }
};
```

- [x] **Step 5: Implement the client (the single swap point)**

`frontend/src/lib/api/client.ts`:
```ts
import type { Dashboard, MockScenario } from '../types';
import { fullMock } from '../mocks/full';
import { launchMock } from '../mocks/launch';

/**
 * Single data-access point for the dashboard.
 *
 * INTEGRATION POINT — to wire the real backend, replace the body below with a
 * parallel fetch of GET /api/{status,squad,captain,transfers,chips,
 * fixtures/planner,activity} and assemble the Dashboard. The `scenario`
 * argument is mock-only and is dropped at that point. Nothing else in the app
 * changes — components consume the same typed Dashboard.
 */
export async function getDashboard(scenario: MockScenario = 'full'): Promise<Dashboard> {
	return scenario === 'launch' ? launchMock : fullMock;
}
```

- [x] **Step 6: Run and verify it passes**

Run: `cd frontend && npm test -- client`
Expected: PASS (all invariants).

- [x] **Step 7: Commit**

```bash
git add frontend/src/lib/mocks frontend/src/lib/api
git commit -m "feat(dashboard): typed mock fixtures (full/launch) + client swap point

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: App shell — design tokens, layout, page load, shared components

**Files:**
- Create: `frontend/src/app.css`
- Create: `frontend/src/routes/+layout.svelte`
- Create: `frontend/src/routes/+page.ts`
- Create: `frontend/src/lib/components/Section.svelte`
- Create: `frontend/src/lib/components/EmptyState.svelte`
- Test: `frontend/src/lib/components/EmptyState.svelte.test.ts`

- [x] **Step 1: Create the dark design tokens + FDR scale + base styles**

`frontend/src/app.css`:
```css
:root {
	--bg: #0b0f14;
	--surface: #121821;
	--surface-2: #1a212c;
	--border: #243040;
	--text: #e6edf3;
	--text-dim: #8b97a7;
	--accent: #00e6a8;
	--accent-2: #1f6feb;
	--danger: #ff5d5d;
	--warning: #ffb454;

	/* FDR 1 (easiest) -> 5 (hardest), tuned for dark bg */
	--fdr-1: #1f8a4c;
	--fdr-2: #6fbf45;
	--fdr-3: #b9a13e;
	--fdr-4: #e07a3e;
	--fdr-5: #c83737;

	--radius: 12px;
	--mono: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
	--sans: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
	background: var(--bg);
	color: var(--text);
	font-family: var(--sans);
	-webkit-font-smoothing: antialiased;
}
.tnum { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.app { max-width: 560px; margin: 0 auto; padding: 0 12px 64px; }
h2 { font-size: 0.82rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-dim); margin: 0; }
```

- [x] **Step 2: Create the layout shell**

`frontend/src/routes/+layout.svelte`:
```svelte
<script lang="ts">
	import '../app.css';
	let { children } = $props();
</script>

<div class="app">
	{@render children()}
</div>
```

- [x] **Step 3: Create the page load (reads `?mock=`, calls the client)**

`frontend/src/routes/+page.ts`:
```ts
import type { PageLoad } from './$types';
import { getDashboard } from '$lib/api/client';
import type { MockScenario } from '$lib/types';

export const load: PageLoad = async ({ url }) => {
	const scenario: MockScenario = url.searchParams.get('mock') === 'launch' ? 'launch' : 'full';
	const dashboard = await getDashboard(scenario);
	return { dashboard, scenario };
};
```

- [x] **Step 4: Write the failing EmptyState test**

`frontend/src/lib/components/EmptyState.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import EmptyState from './EmptyState.svelte';

describe('EmptyState', () => {
	it('renders the provided message', () => {
		render(EmptyState, { props: { message: 'No transfers worth making this GW.' } });
		expect(screen.getByText('No transfers worth making this GW.')).toBeInTheDocument();
	});
});
```

- [x] **Step 5: Run to verify it fails**

Run: `cd frontend && npm test -- EmptyState`
Expected: FAIL (component not found).

- [x] **Step 6: Implement Section + EmptyState**

`frontend/src/lib/components/Section.svelte`:
```svelte
<script lang="ts">
	let { id, title, children } = $props<{ id: string; title: string; children: any }>();
</script>

<section {id} class="section">
	<header class="section-head"><h2>{title}</h2></header>
	{@render children()}
</section>

<style>
	.section { margin-top: 20px; scroll-margin-top: 96px; }
	.section-head { margin-bottom: 8px; }
</style>
```

`frontend/src/lib/components/EmptyState.svelte`:
```svelte
<script lang="ts">
	let { message } = $props<{ message: string }>();
</script>

<p class="empty">{message}</p>

<style>
	.empty {
		color: var(--text-dim);
		font-size: 0.9rem;
		background: var(--surface);
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		padding: 16px;
		margin: 0;
	}
</style>
```

- [x] **Step 7: Run to verify it passes**

Run: `cd frontend && npm test -- EmptyState`
Expected: PASS.

- [x] **Step 8: Verify the dev server renders the (still mostly empty) page**

```bash
cd frontend && npm run dev -- --port 5173 &
sleep 4 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/ ; kill %1
```
Expected: `200`.

- [x] **Step 9: Commit**

```bash
git add frontend/src/app.css frontend/src/routes frontend/src/lib/components/Section.svelte frontend/src/lib/components/EmptyState.svelte frontend/src/lib/components/EmptyState.svelte.test.ts
git commit -m "feat(dashboard): app shell, dark tokens, page load, shared Section/EmptyState

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Header, Countdown, SectionNav

**Files:**
- Create: `frontend/src/lib/components/Header.svelte`
- Create: `frontend/src/lib/components/Countdown.svelte`
- Create: `frontend/src/lib/components/SectionNav.svelte`
- Test: `frontend/src/lib/components/Header.svelte.test.ts`
- Test: `frontend/src/lib/components/SectionNav.svelte.test.ts`

- [x] **Step 1: Write the failing Header test**

`frontend/src/lib/components/Header.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Header from './Header.svelte';
import type { Status } from '../types';

const status: Status = {
	current_gw: 38,
	next_gw: null,
	deadline_utc: '2999-01-01T00:00:00Z',
	mode: 'manual',
	data_fresh_as_of_utc: '2026-05-22T09:00:00Z',
	banners: [{ level: 'warning', text: 'Understat data is 8 days stale.' }]
};

describe('Header', () => {
	it('shows the gameweek and mode', () => {
		render(Header, { props: { status } });
		expect(screen.getByText(/GW38/)).toBeInTheDocument();
		expect(screen.getByText(/manual/i)).toBeInTheDocument();
	});
	it('renders warning banners', () => {
		render(Header, { props: { status } });
		expect(screen.getByText('Understat data is 8 days stale.')).toBeInTheDocument();
	});
});
```

- [x] **Step 2: Write the failing SectionNav test**

`frontend/src/lib/components/SectionNav.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import SectionNav from './SectionNav.svelte';

describe('SectionNav', () => {
	it('shows the chip nav item only when a chip is recommended', () => {
		const { unmount } = render(SectionNav, { props: { hasChip: false } });
		expect(screen.queryByRole('link', { name: /chip/i })).toBeNull();
		unmount();
		render(SectionNav, { props: { hasChip: true } });
		expect(screen.getByRole('link', { name: /chip/i })).toBeInTheDocument();
	});
	it('always shows Team, Captain, Transfers, Fixtures, Log', () => {
		render(SectionNav, { props: { hasChip: false } });
		for (const label of [/team/i, /captain/i, /transfers/i, /fixtures/i, /log/i])
			expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
	});
});
```

- [x] **Step 3: Run both to verify they fail**

Run: `cd frontend && npm test -- Header SectionNav`
Expected: FAIL (components not found).

- [x] **Step 4: Implement Countdown**

`frontend/src/lib/components/Countdown.svelte` (live-ticking, every 30s):
```svelte
<script lang="ts">
	import { countdown } from '$lib/format';
	let { deadlineUtc } = $props<{ deadlineUtc: string }>();
	let now = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 30000);
		return () => clearInterval(t);
	});
	let label = $derived(countdown(deadlineUtc, now));
</script>

<span class="tnum">{label}</span>
```

- [x] **Step 5: Implement Header**

`frontend/src/lib/components/Header.svelte`:
```svelte
<script lang="ts">
	import type { Status } from '$lib/types';
	import Countdown from './Countdown.svelte';
	let { status } = $props<{ status: Status }>();
</script>

<header class="hdr">
	<div class="row">
		<strong>GW{status.current_gw}</strong>
		<span class="dot {status.mode}"></span>
		<span class="mode">{status.mode}</span>
		<span class="cd"><Countdown deadlineUtc={status.deadline_utc} /></span>
	</div>
	{#if status.banners.length}
		<ul class="banners">
			{#each status.banners as b}
				<li class="banner {b.level}">{b.text}</li>
			{/each}
		</ul>
	{/if}
</header>

<style>
	.hdr { position: sticky; top: 0; z-index: 10; background: var(--bg); padding: 12px 0 8px; }
	.row { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; }
	.mode { color: var(--text-dim); text-transform: capitalize; font-size: 0.85rem; }
	.cd { margin-left: auto; color: var(--accent); }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
	.dot.frozen { background: var(--text-dim); }
	.dot.deadguard { background: var(--warning); }
	.banners { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
	.banner { font-size: 0.8rem; padding: 8px 10px; border-radius: 8px; }
	.banner.warning { background: rgba(255, 180, 84, 0.12); color: var(--warning); }
	.banner.error { background: rgba(255, 93, 93, 0.12); color: var(--danger); }
	.banner.info { background: var(--surface); color: var(--text-dim); }
</style>
```

- [x] **Step 6: Implement SectionNav**

`frontend/src/lib/components/SectionNav.svelte` (sticky chip row of anchor links):
```svelte
<script lang="ts">
	let { hasChip } = $props<{ hasChip: boolean }>();
	const items = [
		{ id: 'team', label: 'Team' },
		{ id: 'captain', label: 'Captain' },
		{ id: 'transfers', label: 'Transfers' },
		...(hasChip ? [{ id: 'chip', label: 'Chip' }] : []),
		{ id: 'fixtures', label: 'Fixtures' },
		{ id: 'log', label: 'Log' }
	];
</script>

<nav class="nav">
	{#each items as it}
		<a class="chip" href={`#${it.id}`}>{it.label}</a>
	{/each}
</nav>

<style>
	.nav {
		position: sticky; top: 48px; z-index: 9; background: var(--bg);
		display: flex; gap: 6px; overflow-x: auto; padding: 6px 0 10px; scrollbar-width: none;
	}
	.nav::-webkit-scrollbar { display: none; }
	.chip {
		flex: 0 0 auto; font-size: 0.78rem; color: var(--text-dim); text-decoration: none;
		background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px;
	}
	.chip:active { color: var(--text); border-color: var(--accent); }
</style>
```

- [x] **Step 7: Run both to verify they pass**

Run: `cd frontend && npm test -- Header SectionNav`
Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add frontend/src/lib/components/Header.svelte frontend/src/lib/components/Countdown.svelte frontend/src/lib/components/SectionNav.svelte frontend/src/lib/components/Header.svelte.test.ts frontend/src/lib/components/SectionNav.svelte.test.ts
git commit -m "feat(dashboard): header (GW/mode/countdown/banners) + sticky section nav

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: My Team — Pitch + PlayerCard

**Files:**
- Create: `frontend/src/lib/components/PlayerCard.svelte`
- Create: `frontend/src/lib/components/Pitch.svelte`
- Test: `frontend/src/lib/components/Pitch.svelte.test.ts`

- [x] **Step 1: Write the failing Pitch test**

`frontend/src/lib/components/Pitch.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Pitch from './Pitch.svelte';
import { fullMock } from '$lib/mocks/full';
import { launchMock } from '$lib/mocks/launch';

describe('Pitch', () => {
	it('renders all 15 players', () => {
		render(Pitch, { props: { squad: fullMock.squad } });
		for (const p of fullMock.squad.players)
			expect(screen.getAllByText(p.web_name).length).toBeGreaterThan(0);
	});
	it('marks the captain with a C armband', () => {
		render(Pitch, { props: { squad: fullMock.squad } });
		expect(screen.getByLabelText('captain')).toHaveTextContent('C');
	});
	it('shows xP when present and an em-dash when forthcoming (launch)', () => {
		const { unmount } = render(Pitch, { props: { squad: fullMock.squad } });
		expect(screen.getAllByText('7.2').length).toBeGreaterThan(0);
		unmount();
		render(Pitch, { props: { squad: launchMock.squad } });
		expect(screen.getAllByText('—').length).toBeGreaterThan(0);
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- Pitch`
Expected: FAIL.

- [x] **Step 3: Implement PlayerCard**

`frontend/src/lib/components/PlayerCard.svelte`:
```svelte
<script lang="ts">
	import type { SquadPlayer } from '$lib/types';
	import { dash, money } from '$lib/format';
	let { player } = $props<{ player: SquadPlayer }>();
	const flag = $derived(player.status !== 'a');
</script>

<div class="card" class:bench={player.multiplier === 0}>
	<div class="top">
		{#if player.is_captain}<span class="band" aria-label="captain">C</span>{/if}
		{#if player.is_vice_captain}<span class="band v" aria-label="vice-captain">V</span>{/if}
		{#if flag}<span class="status {player.status}" title={player.status}></span>{/if}
	</div>
	<div class="name">{player.web_name}</div>
	<div class="meta tnum">{player.team_short} · {money(player.price)}</div>
	<div class="xp tnum">
		<span>{dash(player.xp_next, 1)}</span><small>{dash(player.xp_next5, 1)}</small>
	</div>
</div>

<style>
	.card { position: relative; background: var(--surface); border: 1px solid var(--border);
		border-radius: 10px; padding: 8px 6px; text-align: center; min-width: 0; }
	.card.bench { opacity: 0.72; }
	.top { position: absolute; top: 4px; left: 4px; display: flex; gap: 3px; }
	.band { font-size: 0.6rem; font-weight: 700; background: var(--accent); color: #00261c;
		border-radius: 4px; padding: 0 3px; }
	.band.v { background: var(--accent-2); color: #fff; }
	.status { position: absolute; top: 4px; right: 4px; width: 7px; height: 7px; border-radius: 50%; background: var(--warning); }
	.status.i, .status.s, .status.u { background: var(--danger); }
	.name { font-size: 0.82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.meta { font-size: 0.66rem; color: var(--text-dim); }
	.xp { margin-top: 4px; font-size: 0.82rem; color: var(--accent); }
	.xp small { color: var(--text-dim); margin-left: 6px; font-size: 0.66rem; }
</style>
```

- [x] **Step 4: Implement Pitch (starters by position rows + bench strip)**

`frontend/src/lib/components/Pitch.svelte`:
```svelte
<script lang="ts">
	import type { Squad, Position } from '$lib/types';
	import PlayerCard from './PlayerCard.svelte';
	let { squad } = $props<{ squad: Squad }>();
	const starters = $derived(squad.players.filter((p) => p.multiplier > 0));
	const bench = $derived(squad.players.filter((p) => p.multiplier === 0));
	const rowFor = (pos: Position) => starters.filter((p) => p.position === pos);
	const order: Position[] = ['GKP', 'DEF', 'MID', 'FWD'];
</script>

<div class="summary tnum">
	Bank {squad.bank.toFixed(1)} · Value {squad.team_value.toFixed(1)}
	{#if squad.free_transfers !== null}· {squad.free_transfers} FT{/if}
</div>

<div class="pitch">
	{#each order as pos}
		<div class="line">
			{#each rowFor(pos) as p (p.id)}<PlayerCard player={p} />{/each}
		</div>
	{/each}
</div>

<div class="bench">
	{#each bench as p (p.id)}<PlayerCard player={p} />{/each}
</div>

<style>
	.summary { font-size: 0.78rem; color: var(--text-dim); margin-bottom: 8px; }
	.pitch {
		background: linear-gradient(180deg, #0e3b2a, #0a2a1f);
		border: 1px solid var(--border); border-radius: var(--radius);
		padding: 12px 8px; display: grid; gap: 12px;
	}
	.line { display: grid; grid-auto-flow: column; gap: 6px; justify-content: center; }
	.bench {
		margin-top: 8px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;
		padding-top: 8px; border-top: 1px dashed var(--border);
	}
</style>
```

- [x] **Step 5: Run to verify it passes**

Run: `cd frontend && npm test -- Pitch`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add frontend/src/lib/components/PlayerCard.svelte frontend/src/lib/components/Pitch.svelte frontend/src/lib/components/Pitch.svelte.test.ts
git commit -m "feat(dashboard): my-team pitch view (XI by position + bench, C/V, xP/—)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Captain picks + Transfer ideas

**Files:**
- Create: `frontend/src/lib/components/CaptainPicks.svelte`
- Create: `frontend/src/lib/components/TransferIdeas.svelte`
- Test: `frontend/src/lib/components/CaptainPicks.svelte.test.ts`
- Test: `frontend/src/lib/components/TransferIdeas.svelte.test.ts`

- [x] **Step 1: Write the failing CaptainPicks test**

`frontend/src/lib/components/CaptainPicks.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import CaptainPicks from './CaptainPicks.svelte';
import { fullMock } from '$lib/mocks/full';

describe('CaptainPicks', () => {
	it('renders ranked picks with reason and xP', () => {
		render(CaptainPicks, { props: { captain: fullMock.captain } });
		expect(screen.getByText('Haaland')).toBeInTheDocument();
		expect(screen.getByText(/Highest xP/)).toBeInTheDocument();
		expect(screen.getByText('7.2')).toBeInTheDocument();
	});
	it('shows a forthcoming message when picks are empty', () => {
		render(CaptainPicks, { props: { captain: { picks: [], vice_player_id: null } } });
		expect(screen.getByText(/Captain ranker not yet available/i)).toBeInTheDocument();
	});
});
```

- [x] **Step 2: Write the failing TransferIdeas test**

`frontend/src/lib/components/TransferIdeas.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TransferIdeas from './TransferIdeas.svelte';
import { fullMock } from '$lib/mocks/full';

describe('TransferIdeas', () => {
	it('renders suggestions with EP delta and hit cost', () => {
		render(TransferIdeas, { props: { transfers: fullMock.transfers } });
		expect(screen.getByText(/Hall/)).toBeInTheDocument();
		expect(screen.getByText(/Aina/)).toBeInTheDocument();
		expect(screen.getByText(/\+2\.6/)).toBeInTheDocument();
	});
	it('shows empty_reason when there are no suggestions', () => {
		render(TransferIdeas, {
			props: { transfers: { suggestions: [], empty_reason: 'No transfers worth making this GW.' } }
		});
		expect(screen.getByText('No transfers worth making this GW.')).toBeInTheDocument();
	});
	it('shows a forthcoming message when empty and reason is null', () => {
		render(TransferIdeas, { props: { transfers: { suggestions: [], empty_reason: null } } });
		expect(screen.getByText(/Transfer engine not yet available/i)).toBeInTheDocument();
	});
});
```

- [x] **Step 3: Run both to verify they fail**

Run: `cd frontend && npm test -- CaptainPicks TransferIdeas`
Expected: FAIL.

- [x] **Step 4: Implement CaptainPicks**

`frontend/src/lib/components/CaptainPicks.svelte`:
```svelte
<script lang="ts">
	import type { Captain } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { captain } = $props<{ captain: Captain }>();
</script>

{#if captain.picks.length === 0}
	<EmptyState message="Captain ranker not yet available — arrives with the decision engine." />
{:else}
	<ol class="picks">
		{#each captain.picks as p, i (p.player_id)}
			<li class="pick">
				<span class="rank tnum">{i + 1}</span>
				<div class="body">
					<div class="line1"><strong>{p.web_name}</strong>
						<span class="fix">{p.fixture}</span>
						<span class="xp tnum">{p.xp.toFixed(1)}</span>
					</div>
					<p class="reason">{p.reason}</p>
				</div>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.picks { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
	.pick { display: flex; gap: 10px; background: var(--surface); border: 1px solid var(--border);
		border-radius: var(--radius); padding: 10px; }
	.rank { color: var(--text-dim); font-size: 0.9rem; width: 1.2rem; }
	.body { flex: 1; min-width: 0; }
	.line1 { display: flex; align-items: baseline; gap: 8px; }
	.fix { color: var(--text-dim); font-size: 0.74rem; }
	.xp { margin-left: auto; color: var(--accent); }
	.reason { margin: 4px 0 0; font-size: 0.8rem; color: var(--text-dim); }
</style>
```

- [x] **Step 5: Implement TransferIdeas**

`frontend/src/lib/components/TransferIdeas.svelte`:
```svelte
<script lang="ts">
	import type { Transfers } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { transfers } = $props<{ transfers: Transfers }>();
	const fmtDelta = (n: number) => (n >= 0 ? `+${n.toFixed(1)}` : n.toFixed(1));
</script>

{#if transfers.suggestions.length === 0}
	<EmptyState
		message={transfers.empty_reason ??
			'Transfer engine not yet available — arrives with the decision engine.'}
	/>
{:else}
	<ul class="xfers">
		{#each transfers.suggestions as s (s.out.player_id + '-' + s.in.player_id)}
			<li class="xfer">
				<div class="move">
					<span class="out">{s.out.web_name}</span>
					<span class="arrow">→</span>
					<span class="in">{s.in.web_name}</span>
				</div>
				<div class="nums tnum">
					<span class="delta">{fmtDelta(s.ep_delta_5gw)} EP</span>
					<span class="hit" class:free={s.hit_cost === 0}>
						{s.hit_cost === 0 ? 'free' : s.hit_cost}
					</span>
					<span class="conf">{s.confidence}%</span>
				</div>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.xfers { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
	.xfer { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
		padding: 10px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
	.move { display: flex; align-items: center; gap: 8px; font-weight: 600; min-width: 0; }
	.out { color: var(--danger); }
	.in { color: var(--accent); }
	.arrow { color: var(--text-dim); }
	.nums { display: flex; gap: 10px; font-size: 0.78rem; align-items: center; }
	.delta { color: var(--text); }
	.hit { color: var(--danger); }
	.hit.free { color: var(--text-dim); }
	.conf { color: var(--text-dim); }
</style>
```

- [x] **Step 6: Run both to verify they pass**

Run: `cd frontend && npm test -- CaptainPicks TransferIdeas`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add frontend/src/lib/components/CaptainPicks.svelte frontend/src/lib/components/TransferIdeas.svelte frontend/src/lib/components/CaptainPicks.svelte.test.ts frontend/src/lib/components/TransferIdeas.svelte.test.ts
git commit -m "feat(dashboard): captain picks + transfer ideas with forthcoming/empty states

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Chip recommendation + Activity log

**Files:**
- Create: `frontend/src/lib/components/ChipRecommendation.svelte`
- Create: `frontend/src/lib/components/ActivityLog.svelte`
- Test: `frontend/src/lib/components/ChipRecommendation.svelte.test.ts`
- Test: `frontend/src/lib/components/ActivityLog.svelte.test.ts`

- [x] **Step 1: Write the failing ChipRecommendation test**

`frontend/src/lib/components/ChipRecommendation.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ChipRecommendation from './ChipRecommendation.svelte';

describe('ChipRecommendation', () => {
	it('renders the chip name and reason when present', () => {
		render(ChipRecommendation, {
			props: { chips: { recommendation: { chip: 'bench_boost', reason: 'DGW: bench xP 5.2 (> 4).' } } }
		});
		expect(screen.getByText(/Bench Boost/i)).toBeInTheDocument();
		expect(screen.getByText(/DGW: bench xP/)).toBeInTheDocument();
	});
	it('renders nothing when recommendation is null', () => {
		const { container } = render(ChipRecommendation, { props: { chips: { recommendation: null } } });
		expect(container.textContent?.trim()).toBe('');
	});
});
```

- [x] **Step 2: Write the failing ActivityLog test**

`frontend/src/lib/components/ActivityLog.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ActivityLog from './ActivityLog.svelte';
import { fullMock } from '$lib/mocks/full';

describe('ActivityLog', () => {
	it('renders entries with action text', () => {
		render(ActivityLog, { props: { activity: fullMock.activity } });
		expect(screen.getByText('Captain set to Haaland')).toBeInTheDocument();
	});
	it('shows an empty message when there are no entries', () => {
		render(ActivityLog, { props: { activity: { entries: [] } } });
		expect(screen.getByText(/No decisions logged yet/i)).toBeInTheDocument();
	});
});
```

- [x] **Step 3: Run both to verify they fail**

Run: `cd frontend && npm test -- ChipRecommendation ActivityLog`
Expected: FAIL.

- [x] **Step 4: Implement ChipRecommendation**

`frontend/src/lib/components/ChipRecommendation.svelte` (renders nothing when null — caller also hides the Section + nav chip):
```svelte
<script lang="ts">
	import type { Chips } from '$lib/types';
	let { chips } = $props<{ chips: Chips }>();
	const label: Record<string, string> = {
		wildcard: 'Wildcard',
		free_hit: 'Free Hit',
		bench_boost: 'Bench Boost',
		triple_captain: 'Triple Captain'
	};
	const rec = $derived(chips.recommendation);
</script>

{#if rec}
	<div class="chip-rec">
		<div class="badge">{label[rec.chip] ?? rec.chip}</div>
		<p class="reason">{rec.reason}</p>
	</div>
{/if}

<style>
	.chip-rec { background: linear-gradient(180deg, rgba(0,230,168,0.10), var(--surface));
		border: 1px solid var(--accent); border-radius: var(--radius); padding: 12px; }
	.badge { display: inline-block; font-weight: 700; color: #00261c; background: var(--accent);
		border-radius: 6px; padding: 2px 8px; font-size: 0.82rem; }
	.reason { margin: 8px 0 0; font-size: 0.85rem; color: var(--text); }
</style>
```

- [x] **Step 5: Implement ActivityLog**

`frontend/src/lib/components/ActivityLog.svelte`:
```svelte
<script lang="ts">
	import type { Activity } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { activity } = $props<{ activity: Activity }>();
	const fmtTs = (iso: string) =>
		new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
</script>

{#if activity.entries.length === 0}
	<EmptyState message="No decisions logged yet." />
{:else}
	<ul class="log">
		{#each activity.entries as e (e.ts_utc + e.action_taken)}
			<li class="entry">
				<span class="type {e.decision_type}">{e.decision_type}</span>
				<span class="action">{e.action_taken}</span>
				<span class="ts tnum">{fmtTs(e.ts_utc)}</span>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.log { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
	.entry { display: flex; align-items: center; gap: 8px; font-size: 0.8rem;
		background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
	.type { text-transform: uppercase; font-size: 0.62rem; letter-spacing: 0.05em; color: var(--accent-2);
		border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }
	.action { flex: 1; min-width: 0; }
	.ts { color: var(--text-dim); font-size: 0.7rem; }
</style>
```

- [x] **Step 6: Run both to verify they pass**

Run: `cd frontend && npm test -- ChipRecommendation ActivityLog`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add frontend/src/lib/components/ChipRecommendation.svelte frontend/src/lib/components/ActivityLog.svelte frontend/src/lib/components/ChipRecommendation.svelte.test.ts frontend/src/lib/components/ActivityLog.svelte.test.ts
git commit -m "feat(dashboard): chip recommendation (hidden when null) + activity log

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Fixture planner grid

**Files:**
- Create: `frontend/src/lib/components/FixturePlanner.svelte`
- Test: `frontend/src/lib/components/FixturePlanner.svelte.test.ts`

- [x] **Step 1: Write the failing test**

`frontend/src/lib/components/FixturePlanner.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/svelte';
import FixturePlanner from './FixturePlanner.svelte';
import { fullMock } from '$lib/mocks/full';

describe('FixturePlanner', () => {
	it('renders the GW horizon header', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		for (const gw of fullMock.planner.horizon)
			expect(screen.getByText(String(gw))).toBeInTheDocument();
	});
	it('colours an attacker cell by fdr_attack and a defender cell by fdr_defense', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		// Haaland (FWD): GW38 fdr_attack 3 -> var(--fdr-3)
		const haa = screen.getByTestId('cell-10-38');
		expect(haa.getAttribute('style')).toContain('--fdr-3');
		// Gabriel (DEF): GW38 fdr_defense 3 -> var(--fdr-3)
		const gab = screen.getByTestId('cell-2-38');
		expect(gab.getAttribute('style')).toContain('--fdr-3');
	});
	it('renders the FDR number inside each cell', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		const haa = screen.getByTestId('cell-10-38');
		expect(within(haa).getByText('3')).toBeInTheDocument();
	});
	it('renders a blank-GW cell as an em-dash', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		// Hall (id 5) has a null cell at GW40 in the fixture
		const blank = screen.getByTestId('cell-5-40');
		expect(blank).toHaveTextContent('—');
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- FixturePlanner`
Expected: FAIL.

- [x] **Step 3: Implement FixturePlanner (fit-to-width compact grid)**

`frontend/src/lib/components/FixturePlanner.svelte`:
```svelte
<script lang="ts">
	import type { Planner } from '$lib/types';
	import { fdrToken, cellFdr } from '$lib/fdr';
	let { planner } = $props<{ planner: Planner }>();
	const cols = $derived(planner.horizon.length);
</script>

<div class="grid" style={`--cols:${cols}`}>
	<div class="head name">Player</div>
	{#each planner.horizon as gw}<div class="head tnum">{gw}</div>{/each}

	{#each planner.rows as row (row.player_id)}
		<div class="name">
			<span class="pn">{row.web_name}</span>
			<span class="tm">{row.team_short}</span>
		</div>
		{#each row.cells as cell, i}
			{#if cell === null}
				<div class="cell blank" data-testid={`cell-${row.player_id}-${planner.horizon[i]}`}>—</div>
			{:else}
				{@const v = cellFdr(row.position, cell)}
				<div
					class="cell tnum"
					style={`background:${fdrToken(v)}`}
					data-testid={`cell-${row.player_id}-${cell.gw}`}
					title={`${cell.opponent_short} ${cell.home ? '(H)' : '(A)'} · FDR ${v}`}
				>
					{v}
				</div>
			{/if}
		{/each}
	{/each}
</div>

<style>
	.grid {
		display: grid;
		grid-template-columns: minmax(64px, 1.4fr) repeat(var(--cols), 1fr);
		gap: 3px; align-items: stretch;
	}
	.head { font-size: 0.66rem; color: var(--text-dim); text-align: center; padding: 2px 0; }
	.head.name { text-align: left; }
	.name { display: flex; flex-direction: column; justify-content: center; min-width: 0; }
	.pn { font-size: 0.74rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.tm { font-size: 0.6rem; color: var(--text-dim); }
	.cell { display: flex; align-items: center; justify-content: center; aspect-ratio: 1 / 1;
		border-radius: 6px; font-size: 0.74rem; font-weight: 700; color: #0b0f14; }
	.cell.blank { background: var(--surface-2); color: var(--text-dim); font-weight: 400; }
</style>
```

- [x] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- FixturePlanner`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/components/FixturePlanner.svelte frontend/src/lib/components/FixturePlanner.svelte.test.ts
git commit -m "feat(dashboard): fit-to-width FDR fixture planner grid (attack/defense by position)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Compose the page + scenario rendering

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Test: `frontend/src/routes/page.svelte.test.ts`

- [x] **Step 1: Write the failing page composition test**

`frontend/src/routes/page.svelte.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Page from './+page.svelte';
import { fullMock } from '$lib/mocks/full';
import { launchMock } from '$lib/mocks/launch';

describe('+page (composition)', () => {
	it('full scenario renders all seven sections incl. the chip section', () => {
		render(Page, { props: { data: { dashboard: fullMock, scenario: 'full' } } });
		for (const id of ['team', 'captain', 'transfers', 'chip', 'fixtures', 'log'])
			expect(document.getElementById(id)).not.toBeNull();
		expect(screen.getByText(/GW38/)).toBeInTheDocument();
	});
	it('launch scenario hides the chip section and shows forthcoming states', () => {
		render(Page, { props: { data: { dashboard: launchMock, scenario: 'launch' } } });
		expect(document.getElementById('chip')).toBeNull();
		expect(screen.getByText(/Captain ranker not yet available/i)).toBeInTheDocument();
		expect(screen.getByText(/No decisions logged yet/i)).toBeInTheDocument();
	});
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- page.svelte`
Expected: FAIL (page still the scaffold default).

- [x] **Step 3: Implement the composed page**

`frontend/src/routes/+page.svelte`:
```svelte
<script lang="ts">
	import type { PageData } from './$types';
	import Header from '$lib/components/Header.svelte';
	import SectionNav from '$lib/components/SectionNav.svelte';
	import Section from '$lib/components/Section.svelte';
	import Pitch from '$lib/components/Pitch.svelte';
	import CaptainPicks from '$lib/components/CaptainPicks.svelte';
	import TransferIdeas from '$lib/components/TransferIdeas.svelte';
	import ChipRecommendation from '$lib/components/ChipRecommendation.svelte';
	import FixturePlanner from '$lib/components/FixturePlanner.svelte';
	import ActivityLog from '$lib/components/ActivityLog.svelte';

	let { data } = $props<{ data: PageData }>();
	const d = $derived(data.dashboard);
	const hasChip = $derived(d.chips.recommendation !== null);
</script>

<Header status={d.status} />
<SectionNav {hasChip} />

<Section id="team" title="My Team"><Pitch squad={d.squad} /></Section>
<Section id="captain" title="Captain Pick"><CaptainPicks captain={d.captain} /></Section>
<Section id="transfers" title="Transfer Ideas"><TransferIdeas transfers={d.transfers} /></Section>
{#if hasChip}
	<Section id="chip" title="Chip Recommendation"><ChipRecommendation chips={d.chips} /></Section>
{/if}
<Section id="fixtures" title="Fixture Planner"><FixturePlanner planner={d.planner} /></Section>
<Section id="log" title="Activity Log"><ActivityLog activity={d.activity} /></Section>
```

- [x] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- page.svelte`
Expected: PASS.

- [x] **Step 5: Run the FULL test suite**

Run: `cd frontend && npm test`
Expected: all suites pass.

- [x] **Step 6: Smoke-check both scenarios in the dev server**

```bash
cd frontend && npm run dev -- --port 5173 &
sleep 4
curl -s -o /dev/null -w "full %{http_code}\n" "http://localhost:5173/?mock=full"
curl -s -o /dev/null -w "launch %{http_code}\n" "http://localhost:5173/?mock=launch"
kill %1
```
Expected: `full 200` and `launch 200`.

- [x] **Step 7: Commit**

```bash
git add frontend/src/routes/+page.svelte frontend/src/routes/page.svelte.test.ts
git commit -m "feat(dashboard): compose seven sections; chip section hidden at launch

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Build verification, PWA/Lighthouse, README, PR

**Files:**
- Create: `frontend/README.md`
- (No source changes unless verification surfaces a bug.)

- [x] **Step 1: Production build + preview**

```bash
cd frontend && npm run build && npm run preview -- --port 4173 &
sleep 4
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:4173/
curl -s http://localhost:4173/manifest.webmanifest | head -c 200; echo
kill %1
```
Expected: `200`; manifest JSON prints (name "FPL Autopilot").

- [x] **Step 2: Lighthouse PWA + mobile check (headless)**

```bash
cd frontend && npm run preview -- --port 4173 &
sleep 4
npx --yes lighthouse http://localhost:4173/?mock=full \
	--only-categories=pwa,performance,accessibility,best-practices \
	--preset=mobile --quiet --chrome-flags="--headless=new --no-sandbox" \
	--output=json --output-path=./lighthouse-report.json || true
node -e "const r=require('./frontend/lighthouse-report.json');console.log('PWA installable:',JSON.stringify(r.audits['installable-manifest'].score),'SW:',JSON.stringify(r.audits['service-worker']?.score));" 2>/dev/null || true
kill %1
```
Expected: `installable-manifest` score `1` and a registered service worker. If Lighthouse cannot run in this environment, manually confirm: `manifest.webmanifest` is served, `sw.js` exists in `build/`, icons resolve (200) — and record that Lighthouse must be run on a machine with Chrome before merge. Delete the report file afterward: `rm -f frontend/lighthouse-report.json`.

- [x] **Step 3: Write the frontend README (run + the single integration point)**

`frontend/README.md`:
```markdown
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
```

- [x] **Step 4: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add frontend/README.md
git commit -m "docs(dashboard): frontend README — run + integration point

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [x] **Step 5: Push and open the PR**

```bash
git push -u origin feat/dashboard
gh pr create --base main --head feat/dashboard \
	--title "Phase 1 PWA dashboard (Interface layer)" \
	--body "$(cat <<'EOF'
Implements the Phase 1 Interface layer per docs/superpowers/specs/2026-05-22-dashboard-pwa-design.md.

- SvelteKit SPA, installable PWA (@vite-pwa/sveltekit): manifest + service worker + icons.
- All seven dashboard sections from product-spec.md, rendered from typed mock data matching docs/api-contract.md.
- Graceful null/empty states for every (forthcoming) field; `?mock=launch` shows the real day-one state.
- FDR planner grid colours by fdr_attack (FWD/MID) / fdr_defense (DEF/GKP), fit-to-width, numbers in cells.
- Single integration point for the real /api: `frontend/src/lib/api/client.ts` (getDashboard, marked INTEGRATION POINT).
- Interface never computes (CLAUDE.md B2). Python `src/` untouched.

Out of scope: wiring to the live backend (the marked one-file change).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [x] **Step 6: Confirm the PR exists**

Run: `gh pr view --json url,state,baseRefName,headRefName`
Expected: state OPEN, base `main`, head `feat/dashboard`.

---

## Self-review (completed by plan author)

**Spec coverage:**
- Header/status → Task 9. My-team pitch (15, C/V, xP "—") → Task 10. Captain (+empty) → Task 11. Transfers (+empty/forthcoming) → Task 11. Chip (hidden when null) → Tasks 12 + 14. Fixture planner (FDR by position, fit-to-width, blank GW, numbers) → Task 13. Activity (+empty) → Task 12. ✓
- Single client swap point → Task 7 (`client.ts` INTEGRATION POINT) + README Task 15. ✓
- `?mock=full` / `?mock=launch` scenarios → Tasks 7, 8, 14. ✓
- Types mirror contract → Task 4. FDR mapping (attack/defense by position) → Task 5. ✓
- PWA installable (manifest, SW, icons), Lighthouse, mobile-first → Tasks 3, 15. ✓
- Dark-first, single-scroll + sticky nav, FPL-native pitch → Tasks 8, 9, 10. ✓
- Isolation (frontend/ only, no src/ or existing docs touched) → file map + every task path. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete code. ✓

**Type consistency:** `getDashboard(scenario)`, `Dashboard`, `MockScenario`, `cellFdr(position, cell)`, `fdrToken(value)`, `dash()/money()/countdown()`, `fullMock`/`launchMock`, and component prop names (`status`, `squad`, `captain`, `transfers`, `chips`, `planner`, `activity`) are used identically across Tasks 4–14. Player ids in `full.ts` (Haaland=10, Gabriel=2, Hall=5) match the FixturePlanner test test-ids in Task 13. ✓
