# Dashboard Deadguard/Freeze Banner + Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard shows deadguard/freeze state (banner + state dot) and offers state-only controls — Keep-as-is and Freeze/Unfreeze — as no-key DB-state POSTs, with live multi-device state via polling. No dashboard live FPL write (Undo stays Telegram/CLI).

**Architecture:** Backend: `get_status` gains `frozen` + populated `banners` (with an optional `action`); three thin POST endpoints (`/api/freeze`, `/api/unfreeze`, `/api/deadguard/keep`) mutate DB state only and return the fresh status; `serve` binds `127.0.0.1` by default. Frontend: `Banner.action?` + `Status.frozen` types; `client.postAction`/`fetchStatus`; a presentational `Header` that emits `onaction(endpoint)`; `+page.svelte` owns `postAction` + status polling.

**Tech Stack:** Python 3.11+ / FastAPI / pytest (backend); SvelteKit 5 / TypeScript / vitest + @testing-library/svelte (frontend). `node_modules` is already installed in `frontend/` (run `cd frontend && npm test` = `vitest run`). Backend: `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-05-24-dashboard-deadguard-controls-design.md`

**Conventions (follow exactly):**
- Backend tests: `tests/test_api.py` has `seed(conn)`, a `seeded` fixture, and a `client` TestClient fixture (in-memory DB via `app.dependency_overrides[get_db]`). Run `.venv/bin/pytest`. Baseline: **395 passed**.
- Frontend tests: vitest + `@testing-library/svelte`. Run `cd frontend && npm test`. Baseline: **44 passed**.
- **NEVER `git add -A`** — stage explicit paths.
- B-rules: B7 (web layer never holds the key / makes an FPL call; bind 127.0.0.1), B2 (interface→lower only), no `decision-engine.md` change.

---

### Task 1: `get_status` — `frozen` + populated `banners`

**Files:**
- Modify: `src/interface/queries.py` (`get_status`; add `_status_banners`; imports)
- Test: `tests/test_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (it has `seed`, the `seeded` fixture, imports `queries`):

```python
def test_status_has_frozen_false_by_default(seeded):
    s = queries.get_status(seeded)
    assert s["frozen"] is False


def test_status_frozen_banner(seeded):
    from src.execution import override
    override.freeze(seeded, reason="boom", source="user")
    s = queries.get_status(seeded)
    assert s["frozen"] is True
    assert any(b["level"] == "error" and "boom" in b["text"] for b in s["banners"])


def test_status_warning_window_banner(seeded):
    from datetime import datetime, timezone, timedelta
    soon = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    seeded.execute("UPDATE gameweeks SET deadline_utc=?, state='PENDING' WHERE is_next=1", (soon,))
    seeded.commit()
    s = queries.get_status(seeded)
    warn = [b for b in s["banners"] if b["level"] == "warning"]
    assert warn and warn[0]["action"] == {"label": "Keep as is", "endpoint": "/api/deadguard/keep"}


def test_status_executed_banner(seeded):
    seeded.execute("UPDATE gameweeks SET state='DEADGUARD_EXECUTED' WHERE is_next=1")
    seeded.commit()
    s = queries.get_status(seeded)
    assert any(b["level"] == "info" for b in s["banners"])
```

(The existing `test_get_status` asserts `s["banners"] == []` — it stays green because the seeded GW38 deadline is in the past, so no warning banner fires, and the default state is PENDING/not-frozen/not-executed.)

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_api.py -k "frozen or warning_window or executed_banner" -v`
Expected: FAIL (`frozen` key missing; banners empty).

- [ ] **Step 3: Implement**

In `src/interface/queries.py`, add imports at the top if absent: `from datetime import datetime, timezone` and `from src import config`. Add the helper and extend `get_status`:

```python
def _status_banners(conn, nxt, frozen_status, cfg, now):
    banners = []
    if frozen_status is not None:
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
    elif (state == "PENDING" and frozen_status is None and config.deadguard_enabled(cfg)
          and deadline is not None):
        mins = (deadline - now).total_seconds() / 60
        if 0 < mins <= config.deadguard_warning_minutes(cfg):
            banners.append({"level": "warning",
                            "text": f"Deadguard will set your team in ~{int(mins)} min unless you act.",
                            "action": {"label": "Keep as is", "endpoint": "/api/deadguard/keep"}})
    return banners
```

Change `get_status` to (a) read `state` for the next GW, (b) read the freeze, (c) return `frozen` + banners. Replace the body:

```python
def get_status(conn):
    from src.execution import override
    cur = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_current=1").fetchone()
    nxt = conn.execute("SELECT id, deadline_utc, state FROM gameweeks WHERE is_next=1").fetchone()
    fresh = conn.execute("SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()
    cfg = load_config()
    mode = cfg.get("mode", {}).get("current", "manual")
    deadline_src = nxt or cur
    frozen_status = override.status(conn)
    now = datetime.now(timezone.utc)
    return {
        "current_gw": cur["id"] if cur else None,
        "next_gw": nxt["id"] if nxt else None,
        "deadline_utc": deadline_src["deadline_utc"] if deadline_src else None,
        "mode": mode,
        "data_fresh_as_of_utc": fresh["m"] if fresh else None,
        "frozen": frozen_status is not None,
        "banners": _status_banners(conn, nxt, frozen_status, cfg, now),
    }
```

(`load_config` is already imported in `queries.py`. The `nxt` row now includes `state`.)

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS — the 4 new tests + the existing `test_get_status`/`test_status_endpoint` (which still see `banners == []` / `mode` on the seeded past-deadline data).

- [ ] **Step 5: Commit**

```bash
git add src/interface/queries.py tests/test_api.py
git commit -m "feat: get_status returns frozen + deadguard/freeze banners (2.5c-3)"
```

---

### Task 2: Write endpoints — freeze / unfreeze / deadguard keep

**Files:**
- Modify: `src/interface/api.py` (CORS allow POST; 3 endpoints; import `repository`)
- Test: `tests/test_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (has the `client` fixture + `seed`, `connect`, `init_db`, `TestClient` imported):

```python
@pytest.fixture
def client_conn():
    from src.interface.api import app
    from src.interface.deps import get_db
    conn = connect(":memory:", check_same_thread=False)
    init_db(conn)
    seed(conn)
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def test_freeze_endpoint(client):
    r = client.post("/api/freeze")
    assert r.status_code == 200 and r.json()["frozen"] is True


def test_unfreeze_endpoint(client):
    client.post("/api/freeze")
    r = client.post("/api/unfreeze")
    assert r.status_code == 200 and r.json()["frozen"] is False


def test_keep_endpoint_sets_user_acted(client_conn):
    client, conn = client_conn
    r = client.post("/api/deadguard/keep")
    assert r.status_code == 200
    state = conn.execute("SELECT state FROM gameweeks WHERE is_next=1").fetchone()["state"]
    assert state == "USER_ACTED"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_api.py -k "freeze_endpoint or unfreeze_endpoint or keep_endpoint" -v`
Expected: FAIL (405 Method Not Allowed / endpoints missing).

- [ ] **Step 3: Implement**

In `src/interface/api.py`: extend the CORS `allow_methods` to include POST, import `repository`, and add the three endpoints. Change:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

Add `from src.data import repository` to the imports, and add (after the existing GET endpoints):

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
        repository.touch_user_action(conn, nxt["id"])
    return queries.get_status(conn)
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS (the 3 new + all existing API tests; `test_cors_header` still green).

- [ ] **Step 5: Commit**

```bash
git add src/interface/api.py tests/test_api.py
git commit -m "feat: dashboard write endpoints — freeze/unfreeze/deadguard-keep (2.5c-3)"
```

---

### Task 3: Bind `127.0.0.1` by default

**Files:**
- Modify: `src/cli.py` (`serve` default + the `serve` subparser `--host` default)
- Test: `tests/test_scheduler.py` (append — it already exercises `cli.serve`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler.py`:

```python
def test_serve_defaults_to_localhost():
    import inspect
    import src.cli as cli
    assert inspect.signature(cli.serve).parameters["host"].default == "127.0.0.1"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_scheduler.py -k serve_defaults_to_localhost -v`
Expected: FAIL (`assert "0.0.0.0" == "127.0.0.1"`).

- [ ] **Step 3: Implement**

In `src/cli.py`, change `def serve(host="0.0.0.0", port=None, scheduler=True):` to `def serve(host="127.0.0.1", port=None, scheduler=True):`. Also change the `serve` subparser's host default — find `p_serve.add_argument("--host", default="0.0.0.0")` and change `default="127.0.0.1"`.

- [ ] **Step 4: Run it to confirm it passes**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: PASS — the new test + existing `test_serve_starts_scheduler`/`test_serve_no_scheduler` (they monkeypatch `uvicorn.run`, so the host value isn't asserted).

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_scheduler.py
git commit -m "feat: serve binds 127.0.0.1 by default (2.5c-3)"
```

---

### Task 4: Frontend types + client (`postAction` / `fetchStatus`)

**Files:**
- Modify: `frontend/src/lib/types.ts` (`Banner.action?`, `Status.frozen`)
- Modify: `frontend/src/lib/api/client.ts` (`fetchStatus`, `postAction`)
- Test: `frontend/src/lib/api/client.test.ts` (append)

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/api/client.test.ts`:

```typescript
describe('fetchStatus', () => {
	it('GETs /api/status and returns the status', async () => {
		let path = '';
		const f = (async (p: string) => {
			path = p;
			return { ok: true, status: 200, json: async () => ({ frozen: false, banners: [] }) } as Response;
		}) as unknown as typeof fetch;
		const { fetchStatus } = await import('./client');
		const s = await fetchStatus(f);
		expect(path).toBe('/api/status');
		expect(s.frozen).toBe(false);
	});
});

describe('postAction', () => {
	it('POSTs to the path and returns the fresh status', async () => {
		let seen: { path: string; method: string | undefined } | null = null;
		const f = (async (p: string, init: RequestInit) => {
			seen = { path: p, method: init?.method };
			return { ok: true, status: 200, json: async () => ({ frozen: true, banners: [] }) } as Response;
		}) as unknown as typeof fetch;
		const { postAction } = await import('./client');
		const s = await postAction('/api/freeze', f);
		expect(seen).toEqual({ path: '/api/freeze', method: 'POST' });
		expect(s.frozen).toBe(true);
	});

	it('throws on a non-ok response', async () => {
		const f = (async () => ({ ok: false, status: 500, json: async () => ({}) }) as Response) as unknown as typeof fetch;
		const { postAction } = await import('./client');
		await expect(postAction('/api/freeze', f)).rejects.toThrow(/freeze/);
	});
});
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `cd frontend && npm test -- src/lib/api/client.test.ts`
Expected: FAIL (`fetchStatus`/`postAction` not exported).

- [ ] **Step 3: Implement**

In `frontend/src/lib/types.ts`, change the `Banner` and `Status` interfaces:

```typescript
export interface Banner {
	level: 'info' | 'warning' | 'error';
	text: string;
	action?: { label: string; endpoint: string };
}

export interface Status {
	current_gw: number;
	next_gw: number | null;
	deadline_utc: string;
	mode: Mode;
	data_fresh_as_of_utc: string;
	frozen: boolean;
	banners: Banner[];
}
```

In `frontend/src/lib/api/client.ts`, add (after `getJson`; `API_BASE` and `Fetch`/`Status` are already in scope):

```typescript
export async function fetchStatus(fetchFn: Fetch = fetch): Promise<Status> {
	return getJson<Status>('/api/status', fetchFn);
}

export async function postAction(path: string, fetchFn: Fetch = fetch): Promise<Status> {
	const res = await fetchFn(`${API_BASE}${path}`, { method: 'POST' });
	if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
	return res.json() as Promise<Status>;
}
```

Also update the two bundled mocks so they satisfy the new `Status.frozen` field: in `frontend/src/lib/mocks/full.ts` and `frontend/src/lib/mocks/launch.ts`, add `frozen: false` to each `status` object (next to `banners`). (Otherwise `svelte-check`/TS would flag the mocks; vitest is JS-loose but keep them correct.)

- [ ] **Step 4: Run them to confirm they pass**

Run: `cd frontend && npm test -- src/lib/api/client.test.ts`
Expected: PASS (the 3 new + existing client tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api/client.ts frontend/src/lib/mocks/full.ts frontend/src/lib/mocks/launch.ts frontend/src/lib/api/client.test.ts
git commit -m "feat: frontend Status.frozen + Banner.action + client postAction/fetchStatus (2.5c-3)"
```

---

### Task 5: `Header.svelte` — Freeze/Unfreeze toggle + banner action buttons

**Files:**
- Modify: `frontend/src/lib/components/Header.svelte` (add `onaction` prop + controls)
- Test: `frontend/src/lib/components/Header.svelte.test.ts` (new)

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/components/Header.svelte.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import Header from './Header.svelte';
import type { Status } from '$lib/types';

const base: Status = {
	current_gw: 1,
	next_gw: 2,
	deadline_utc: '2026-05-25T10:00:00+00:00',
	mode: 'manual',
	data_fresh_as_of_utc: '2026-05-24T10:00:00+00:00',
	frozen: false,
	banners: []
};

describe('Header controls', () => {
	it('freeze toggle calls onaction with /api/freeze when not frozen', async () => {
		const onaction = vi.fn();
		render(Header, { props: { status: base, onaction } });
		await userEvent.click(screen.getByRole('button', { name: /^freeze$/i }));
		expect(onaction).toHaveBeenCalledWith('/api/freeze');
	});

	it('shows Unfreeze and calls /api/unfreeze when frozen', async () => {
		const onaction = vi.fn();
		render(Header, {
			props: { status: { ...base, frozen: true, banners: [{ level: 'error', text: 'frozen' }] }, onaction }
		});
		await userEvent.click(screen.getByRole('button', { name: /unfreeze/i }));
		expect(onaction).toHaveBeenCalledWith('/api/unfreeze');
	});

	it('a banner action renders a button that calls onaction with its endpoint', async () => {
		const onaction = vi.fn();
		const banners = [
			{ level: 'warning' as const, text: 'soon', action: { label: 'Keep as is', endpoint: '/api/deadguard/keep' } }
		];
		render(Header, { props: { status: { ...base, banners }, onaction } });
		await userEvent.click(screen.getByRole('button', { name: /keep as is/i }));
		expect(onaction).toHaveBeenCalledWith('/api/deadguard/keep');
	});
});
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `cd frontend && npm test -- src/lib/components/Header.svelte.test.ts`
Expected: FAIL (no freeze button / no `onaction`).

- [ ] **Step 3: Implement**

Rewrite `frontend/src/lib/components/Header.svelte` to add the `onaction` prop (optional, guarded), a Freeze/Unfreeze toggle in the row, and a button for any banner with an `action`:

```svelte
<script lang="ts">
	import type { Status } from '$lib/types';
	import Countdown from './Countdown.svelte';
	let { status, onaction }: { status: Status; onaction?: (endpoint: string) => void } = $props();
</script>

<header class="hdr">
	<div class="row">
		<strong>GW{status.current_gw}</strong>
		<span class="dot {status.frozen ? 'frozen' : status.mode}"></span>
		<span class="mode">{status.frozen ? 'frozen' : status.mode}</span>
		<button class="toggle" onclick={() => onaction?.(status.frozen ? '/api/unfreeze' : '/api/freeze')}>
			{status.frozen ? 'Unfreeze' : 'Freeze'}
		</button>
		<span class="cd"><Countdown deadlineUtc={status.deadline_utc} /></span>
	</div>
	{#if status.banners.length}
		<ul class="banners">
			{#each status.banners as b}
				<li class="banner {b.level}">
					<span>{b.text}</span>
					{#if b.action}
						<button class="action" onclick={() => onaction?.(b.action!.endpoint)}>{b.action.label}</button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</header>

<style>
	.hdr { position: sticky; top: 0; z-index: 10; background: var(--bg); padding: 12px 0 8px; }
	.row { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; }
	.mode { color: var(--text-dim); text-transform: capitalize; font-size: 0.85rem; }
	.toggle { font-size: 0.75rem; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--text-dim);
		background: var(--surface); color: var(--text); cursor: pointer; }
	.cd { margin-left: auto; color: var(--accent); }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
	.dot.frozen { background: var(--text-dim); }
	.dot.deadguard { background: var(--warning); }
	.banners { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
	.banner { font-size: 0.8rem; padding: 8px 10px; border-radius: 8px; display: flex; align-items: center; gap: 8px; }
	.banner .action { margin-left: auto; font-size: 0.75rem; padding: 3px 8px; border-radius: 6px;
		border: 1px solid currentColor; background: transparent; color: inherit; cursor: pointer; }
	.banner.warning { background: rgba(255, 180, 84, 0.12); color: var(--warning); }
	.banner.error { background: rgba(255, 93, 93, 0.12); color: var(--danger); }
	.banner.info { background: var(--surface); color: var(--text-dim); }
</style>
```

(Preserves the existing classes/styles; adds `.toggle` + `.action` buttons and the frozen dot/label. `onaction` is optional + guarded so the existing page test — which renders Header without `onaction` — still works.)

- [ ] **Step 4: Run them to confirm they pass**

Run: `cd frontend && npm test -- src/lib/components/Header.svelte.test.ts`
Expected: PASS. Then the full frontend suite: `cd frontend && npm test` — all green (the existing `page.svelte.test.ts` still passes; Header renders with the added controls).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/Header.svelte frontend/src/lib/components/Header.svelte.test.ts
git commit -m "feat: Header freeze/unfreeze toggle + banner action buttons (2.5c-3)"
```

---

### Task 6: `+page.svelte` — wire actions + status polling

**Files:**
- Modify: `frontend/src/routes/+page.svelte` (status `$state`, `onaction` handler, polling)

- [ ] **Step 1: Implement the wiring**

This task is integration wiring (the unit-level behavior — `postAction`/`fetchStatus` and the Header `onaction` — is already tested in Tasks 4–5). Update `frontend/src/routes/+page.svelte` to own a reactive `status`, handle actions via `postAction`, and poll `fetchStatus`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import type { PageData } from './$types';
	import type { Status } from '$lib/types';
	import { fetchStatus, postAction } from '$lib/api/client';
	import Header from '$lib/components/Header.svelte';
	import SectionNav from '$lib/components/SectionNav.svelte';
	import Section from '$lib/components/Section.svelte';
	import Pitch from '$lib/components/Pitch.svelte';
	import CaptainPicks from '$lib/components/CaptainPicks.svelte';
	import TransferIdeas from '$lib/components/TransferIdeas.svelte';
	import ChipRecommendation from '$lib/components/ChipRecommendation.svelte';
	import FixturePlanner from '$lib/components/FixturePlanner.svelte';
	import ActivityLog from '$lib/components/ActivityLog.svelte';

	let { data }: { data: PageData } = $props();
	const d = $derived(data.dashboard);
	const hasChip = $derived(d.chips.recommendation !== null);

	let status = $state<Status>(data.dashboard.status);
	const live = $derived(data.source === 'live');

	async function handleAction(endpoint: string) {
		try {
			status = await postAction(endpoint);
		} catch (e) {
			console.warn('[dashboard] action failed', endpoint, e);
		}
	}

	async function refreshStatus() {
		try {
			status = await fetchStatus();
		} catch (e) {
			console.warn('[dashboard] status refresh failed', e);
		}
	}

	onMount(() => {
		if (!live) return; // mock mode: no polling
		const id = setInterval(refreshStatus, 30000);
		const onFocus = () => refreshStatus();
		window.addEventListener('focus', onFocus);
		return () => {
			clearInterval(id);
			window.removeEventListener('focus', onFocus);
		};
	});
</script>

<Header {status} onaction={handleAction} />
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

(`Header` now gets the reactive `status` + `handleAction`. Polling only runs for live data, never in `?mock=` mode. The rest of the page is unchanged.)

- [ ] **Step 2: Run the full frontend suite + type check**

Run: `cd frontend && npm test`
Expected: PASS — all frontend tests including the existing `page.svelte.test.ts` (the page still renders; the test seeds `data.dashboard` and the Header consumes `status` seeded from it).
Run: `cd frontend && npm run check`
Expected: 0 errors (types line up: `status: Status` with `frozen`, `Banner.action?`).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/+page.svelte
git commit -m "feat: dashboard wires freeze/keep actions + status polling (2.5c-3)"
```

---

### Task 7: Docs + full-suite verification

**Files:**
- Modify: `docs/api-contract.md`, `docs/deadguard.md`, `docs/runbook.md`

- [ ] **Step 1: Update the API contract**

In `docs/api-contract.md`, document: `Status.frozen: boolean`; `Banner.action?: { label, endpoint }`; and the three POST endpoints (`POST /api/freeze`, `POST /api/unfreeze`, `POST /api/deadguard/keep` — each returns the fresh `Status`, no body, no auth). Match the file's existing format (grep its headings first).

- [ ] **Step 2: Update deadguard.md + runbook.md**

In `docs/deadguard.md`, add (near the existing dashboard/undo sections):

```markdown
## Dashboard banner + controls (Phase 2.5c-3)

The dashboard polls `/api/status` (~30 s + on focus) and shows the deadguard/freeze state: a red banner +
Unfreeze toggle when frozen; an amber "deadguard in ~Xm" banner with a **Keep as is** button in the warning
window (→ USER_ACTED); an info banner after deadguard executed (with a Telegram/CLI undo hint). A **Freeze**
toggle is always in the header. These are no-key DB-state writes (`POST /api/freeze`/`/api/unfreeze`/
`/api/deadguard/keep`); the dashboard performs no live FPL write (Undo stays Telegram/CLI). The backend is
the single source of truth, so a second device reflects changes within one poll.
```

In `docs/runbook.md`, add a note that `fpl-autopilot serve` now binds `127.0.0.1` by default (the API can mutate state via the dashboard write endpoints); use `--host 0.0.0.0` to expose it on the LAN deliberately.

- [ ] **Step 3: Run BOTH full suites**

Run: `.venv/bin/pytest -q`
Expected: PASS — all backend tests (395 baseline + ~8 new ≈ 403).
Run: `cd frontend && npm test`
Expected: PASS — all frontend tests (44 baseline + ~6 new ≈ 50).
If anything fails, fix before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/api-contract.md docs/deadguard.md docs/runbook.md
git commit -m "docs: dashboard deadguard/freeze controls — api-contract + deadguard + runbook (2.5c-3)"
```

---

## Definition of done (CLAUDE.md B14)
- Dashboard shows the deadguard/freeze banner + state dot, a Freeze/Unfreeze header toggle, and a Keep-as-is button in the warning window; actions hit the no-key POST endpoints and the view reflects new state within a poll (multi-device). `serve` binds 127.0.0.1 by default. No dashboard live FPL write; the web layer holds no master key.
- Both suites green (`.venv/bin/pytest -q` + `cd frontend && npm test`); tests fixtures-only (TestClient on in-memory DB; vitest with stub fetch / `vi.fn`). The agent never ran the live server (R3).
- No `decision-engine.md` change. `api-contract.md`/`deadguard.md`/`runbook.md` updated.

## Self-review notes (checked against the spec)
- **Spec coverage:** §1 get_status frozen+banners → Task 1; §2 write endpoints → Task 2; §3 bind 127.0.0.1 → Task 3; §4 types+client → Task 4; §5 Header + polling → Tasks 5–6; docs → Task 7. All mapped.
- **Refinement vs spec §5:** the spec sketched Header calling `postAction` directly; the plan makes Header **presentational** (`onaction(endpoint)` callback) with `postAction`+polling owned by `+page.svelte`. Same behavior, cleaner separation, simpler Header test (a `vi.fn`, no module mock). Noted deliberately.
- **Type/name consistency:** `Status.frozen: boolean`, `Banner.action?: {label, endpoint}`, `fetchStatus(fetch)`, `postAction(path, fetch)`, endpoints `/api/freeze` `/api/unfreeze` `/api/deadguard/keep`, `_status_banners(conn, nxt, frozen_status, cfg, now)` — identical across tasks.
- **Existing tests:** `test_get_status` (banners==[]) and `page.svelte.test.ts` stay green (seeded past-deadline → no banner; `onaction` optional). Mocks get `frozen:false` (Task 4) so `npm run check` passes.
```
