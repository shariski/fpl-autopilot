import type { PageLoad } from './$types';
import { fetchAudit, fetchDashboard } from '$lib/api/client';

// Default: latest audit for the current GW. The current GW is taken from /api/status
// (Status.current_gw or next_gw). Page renders an empty state if no audit has been
// persisted yet — the user runs `fpl-autopilot review` to generate one.
export const load: PageLoad = async ({ url, fetch }) => {
	const gwParam = url.searchParams.get('gw');
	let gw: number;
	if (gwParam !== null && /^\d+$/.test(gwParam)) {
		gw = Number(gwParam);
	} else {
		const dashboard = await fetchDashboard(fetch);
		gw = dashboard.status.current_gw || dashboard.status.next_gw || 0;
	}
	const audit = await fetchAudit(gw, fetch);
	return { gw, audit };
};
