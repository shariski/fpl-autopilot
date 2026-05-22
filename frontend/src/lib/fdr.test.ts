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
