import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import CaptainPicks from './CaptainPicks.svelte';
import { fullMock } from '$lib/mocks/full';

describe('CaptainPicks', () => {
	it('renders ranked picks with reason and xP', () => {
		render(CaptainPicks, { props: { captain: fullMock.captain } });
		expect(screen.getByText('Haaland')).toBeInTheDocument();
		expect(screen.getByText(/Highest xP/)).toBeInTheDocument();
		expect(screen.getByText('7.2')).toBeInTheDocument();
	});
	it('shows a forthcoming message when picks are empty', () => {
		render(CaptainPicks, { props: { captain: { picks: [], vice_player_id: null } } });
		expect(screen.getByText(/Captain ranker not yet available/i)).toBeInTheDocument();
	});
});
