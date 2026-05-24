import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import Header from './Header.svelte';
import type { Status } from '../types';

const status: Status = {
	current_gw: 38,
	next_gw: null,
	deadline_utc: '2999-01-01T00:00:00Z',
	mode: 'manual',
	data_fresh_as_of_utc: '2026-05-22T09:00:00Z',
	frozen: false,
	banners: [{ level: 'warning', text: 'Understat data is 8 days stale.' }]
};

describe('Header', () => {
	it('shows the gameweek and mode', () => {
		render(Header, { props: { status } });
		expect(screen.getByText(/GW38/)).toBeInTheDocument();
		expect(screen.getByText(/manual/i)).toBeInTheDocument();
	});
	it('renders warning banners', () => {
		render(Header, { props: { status } });
		expect(screen.getByText('Understat data is 8 days stale.')).toBeInTheDocument();
	});
});

const base: Status = {
	current_gw: 1,
	next_gw: 2,
	deadline_utc: '2026-05-25T10:00:00+00:00',
	mode: 'manual',
	data_fresh_as_of_utc: '2026-05-24T10:00:00+00:00',
	frozen: false,
	banners: []
};

describe('Header controls', () => {
	it('freeze toggle calls onaction with /api/freeze when not frozen', async () => {
		const onaction = vi.fn();
		render(Header, { props: { status: base, onaction } });
		await userEvent.click(screen.getByRole('button', { name: /^freeze$/i }));
		expect(onaction).toHaveBeenCalledWith('/api/freeze');
	});

	it('shows Unfreeze and calls /api/unfreeze when frozen', async () => {
		const onaction = vi.fn();
		render(Header, {
			props: { status: { ...base, frozen: true, banners: [{ level: 'error', text: 'frozen' }] }, onaction }
		});
		await userEvent.click(screen.getByRole('button', { name: /unfreeze/i }));
		expect(onaction).toHaveBeenCalledWith('/api/unfreeze');
	});

	it('a banner action renders a button that calls onaction with its endpoint', async () => {
		const onaction = vi.fn();
		const banners = [
			{ level: 'warning' as const, text: 'soon', action: { label: 'Keep as is', endpoint: '/api/deadguard/keep' } }
		];
		render(Header, { props: { status: { ...base, banners }, onaction } });
		await userEvent.click(screen.getByRole('button', { name: /keep as is/i }));
		expect(onaction).toHaveBeenCalledWith('/api/deadguard/keep');
	});
});
