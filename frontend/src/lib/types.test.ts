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
