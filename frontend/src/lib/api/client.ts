import type {
	Dashboard,
	MockScenario,
	Status,
	Squad,
	Captain,
	Transfers,
	Chips,
	Planner,
	Activity
} from '../types';
import { fullMock } from '../mocks/full';
import { launchMock } from '../mocks/launch';

type Fetch = typeof fetch;

// Same-origin: the dev server proxies /api -> the FastAPI backend (see
// `server.proxy` in vite.config.ts); in production the SPA is served from the
// same origin as the API. Override the base only if that ever changes.
const API_BASE = '';

async function getJson<T>(path: string, fetchFn: Fetch): Promise<T> {
	const res = await fetchFn(`${API_BASE}${path}`);
	if (!res.ok) {
		let detail = String(res.status);
		try {
			const body = await res.json();
			if (body?.error) detail = body.error; // contract: non-200 -> { "error": ... }
		} catch {
			/* non-JSON error body — keep the status code */
		}
		throw new Error(`GET ${path} failed: ${detail}`);
	}
	return res.json() as Promise<T>;
}

export async function fetchStatus(fetchFn: Fetch = fetch): Promise<Status> {
	return getJson<Status>('/api/status', fetchFn);
}

export async function postAction(path: string, fetchFn: Fetch = fetch): Promise<Status> {
	const res = await fetchFn(`${API_BASE}${path}`, { method: 'POST' });
	if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
	return res.json() as Promise<Status>;
}

const EMPTY_CAPTAIN: Captain = { picks: [], vice_player_id: null };
const EMPTY_TRANSFERS: Transfers = { suggestions: [], empty_reason: null };
const EMPTY_CHIPS: Chips = { recommendation: null };

/**
 * A decision endpoint that fails (slice mid-deploy, transient 500) degrades to
 * its empty/forthcoming shape so it can't blank the whole dashboard — the same
 * graceful state the UI already renders. The failure is logged (not silent).
 */
async function softGet<T>(path: string, fetchFn: Fetch, fallback: T): Promise<T> {
	try {
		return await getJson<T>(path, fetchFn);
	} catch (err) {
		console.warn(`[dashboard] ${path} unavailable, showing empty state:`, err);
		return fallback;
	}
}

/**
 * Live dashboard: parallel GET of the seven /api endpoints, assembled into one
 * typed Dashboard. The core read-model endpoints (status, squad, fixtures
 * planner) must succeed — if they fail the load rejects and the error page
 * shows. The decision endpoints (captain, transfers, chips) and the activity
 * log degrade gracefully via softGet.
 *
 * This is the single integration point: changing the data source = this file only.
 */
export async function fetchDashboard(fetchFn: Fetch = fetch): Promise<Dashboard> {
	const [status, squad, planner, activity, captain, transfers, chips] = await Promise.all([
		getJson<Status>('/api/status', fetchFn),
		getJson<Squad>('/api/squad', fetchFn),
		getJson<Planner>('/api/fixtures/planner', fetchFn),
		softGet<Activity>('/api/activity', fetchFn, { entries: [] }),
		softGet<Captain>('/api/captain', fetchFn, EMPTY_CAPTAIN),
		softGet<Transfers>('/api/transfers', fetchFn, EMPTY_TRANSFERS),
		softGet<Chips>('/api/chips', fetchFn, EMPTY_CHIPS)
	]);
	return { status, squad, captain, transfers, chips, planner, activity };
}

/** Mock fixtures for demo / offline / tests. `?mock=full|launch` selects this path. */
export async function getMockDashboard(scenario: MockScenario = 'full'): Promise<Dashboard> {
	return scenario === 'launch' ? launchMock : fullMock;
}
