import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Header from './Header.svelte';
import type { Status } from '../types';

const status: Status = {
	current_gw: 38,
	next_gw: null,
	deadline_utc: '2999-01-01T00:00:00Z',
	mode: 'manual',
	data_fresh_as_of_utc: '2026-05-22T09:00:00Z',
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
