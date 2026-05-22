import { describe, it, expect } from 'vitest';
import { dash, money, countdown } from './format';

describe('dash', () => {
	it('renders an em-dash for null/undefined', () => {
		expect(dash(null)).toBe('—');
		expect(dash(undefined)).toBe('—');
	});
	it('passes numbers through with optional fixed decimals', () => {
		expect(dash(7.2)).toBe('7.2');
		expect(dash(31.41, 1)).toBe('31.4');
	});
});

describe('money', () => {
	it('formats £m with one decimal', () => {
		expect(money(14.7)).toBe('£14.7');
		expect(money(2.3)).toBe('£2.3');
	});
});

describe('countdown', () => {
	it('formats remaining time as Hh Mm', () => {
		const now = new Date('2026-05-24T11:00:00Z').getTime();
		expect(countdown('2026-05-24T13:14:00Z', now)).toBe('2h 14m');
	});
	it('shows "Deadline passed" once elapsed', () => {
		const now = new Date('2026-05-24T14:00:00Z').getTime();
		expect(countdown('2026-05-24T13:00:00Z', now)).toBe('Deadline passed');
	});
	it('includes days when more than 24h remain', () => {
		const now = new Date('2026-05-22T13:00:00Z').getTime();
		expect(countdown('2026-05-24T13:00:00Z', now)).toBe('2d 0h 0m');
	});
});
