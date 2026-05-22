import type { PageLoad } from './$types';
import { fetchDashboard, getMockDashboard } from '$lib/api/client';

// Default: live data from the backend (/api, dev-proxied to FastAPI).
// `?mock=full` / `?mock=launch` force the bundled fixtures — useful for a demo,
// offline, or when the backend isn't running.
export const load: PageLoad = async ({ url, fetch }) => {
	const mock = url.searchParams.get('mock');
	if (mock === 'full' || mock === 'launch') {
		return { dashboard: await getMockDashboard(mock), source: 'mock' as const };
	}
	return { dashboard: await fetchDashboard(fetch), source: 'live' as const };
};
