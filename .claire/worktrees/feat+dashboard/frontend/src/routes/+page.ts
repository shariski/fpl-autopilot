import type { PageLoad } from './$types';
import { getDashboard } from '$lib/api/client';
import type { MockScenario } from '$lib/types';

export const load: PageLoad = async ({ url }) => {
	const scenario: MockScenario = url.searchParams.get('mock') === 'launch' ? 'launch' : 'full';
	const dashboard = await getDashboard(scenario);
	return { dashboard, scenario };
};
