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
