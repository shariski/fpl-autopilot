import { describe, it, expect, vi } from 'vitest';
import { getMockDashboard, fetchDashboard } from './client';
import { fullMock } from '../mocks/full';

describe('getMockDashboard', () => {
	it('full scenario: squad has exactly 15 players, one captain, one vice', async () => {
		const d = await getMockDashboard('full');
		expect(d.squad.players).toHaveLength(15);
		expect(d.squad.players.filter((p) => p.is_captain)).toHaveLength(1);
		expect(d.squad.players.filter((p) => p.is_vice_captain)).toHaveLength(1);
	});
	it('full scenario: forthcoming fields are populated', async () => {
		const d = await getMockDashboard('full');
		expect(d.squad.players[0].xp_next).not.toBeNull();
		expect(d.captain.picks.length).toBeGreaterThan(0);
		expect(d.chips.recommendation).not.toBeNull();
	});
	it('full scenario: planner horizon length matches each row, with a blank-GW null cell present', async () => {
		const d = await getMockDashboard('full');
		const n = d.planner.horizon.length;
		expect(n).toBeGreaterThanOrEqual(5);
		for (const row of d.planner.rows) expect(row.cells).toHaveLength(n);
		const hasBlank = d.planner.rows.some((r) => r.cells.some((c) => c === null));
		expect(hasBlank).toBe(true);
	});
	it('full scenario: FDR values across cells span a range (not all identical)', async () => {
		const d = await getMockDashboard('full');
		const vals = new Set<number>();
		for (const r of d.planner.rows) for (const c of r.cells) if (c) vals.add(c.fdr_attack);
		expect(vals.size).toBeGreaterThan(1);
	});
	it('launch scenario: forthcoming fields are null/empty but live data remains', async () => {
		const d = await getMockDashboard('launch');
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

// A fetch stub that maps endpoint suffixes to payloads; paths in `fail` 500.
function stubFetch(payloads: Record<string, unknown>, fail: string[] = []): typeof fetch {
	return (async (input: string | URL) => {
		const path = typeof input === 'string' ? input : input.toString();
		const ok = !fail.some((f) => path.endsWith(f));
		const key = Object.keys(payloads).find((k) => path.endsWith(k));
		return {
			ok,
			status: ok ? 200 : 500,
			json: async () => (ok ? payloads[key as string] : { error: 'boom' })
		} as Response;
	}) as unknown as typeof fetch;
}

const allPayloads = {
	'/api/status': fullMock.status,
	'/api/squad': fullMock.squad,
	'/api/fixtures/planner': fullMock.planner,
	'/api/activity': fullMock.activity,
	'/api/captain': fullMock.captain,
	'/api/transfers': fullMock.transfers,
	'/api/chips': fullMock.chips
};

describe('fetchDashboard', () => {
	it('fans out to the seven endpoints and assembles a Dashboard', async () => {
		const d = await fetchDashboard(stubFetch(allPayloads));
		expect(d.squad.players).toHaveLength(15);
		expect(d.captain.picks.length).toBeGreaterThan(0);
		expect(d.planner.horizon.length).toBeGreaterThan(0);
		expect(d.activity.entries.length).toBeGreaterThan(0);
	});

	it('degrades a failing decision endpoint to its empty shape (core data intact)', async () => {
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		const d = await fetchDashboard(stubFetch(allPayloads, ['/api/captain']));
		expect(d.captain.picks).toEqual([]); // degraded, not thrown
		expect(d.squad.players).toHaveLength(15); // core intact
		vi.restoreAllMocks();
	});

	it('rejects when a core endpoint (squad) fails', async () => {
		await expect(fetchDashboard(stubFetch(allPayloads, ['/api/squad']))).rejects.toThrow(/squad/);
	});
});
