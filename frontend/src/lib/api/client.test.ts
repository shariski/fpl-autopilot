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
