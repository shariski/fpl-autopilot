import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import CaptainPicks from './CaptainPicks.svelte';
import { fullMock } from '$lib/mocks/full';
import type { Captain } from '$lib/types';

describe('CaptainPicks', () => {
	it('renders ranked picks with reason and xP', () => {
		render(CaptainPicks, { props: { captain: fullMock.captain } });
		expect(screen.getByText('Haaland')).toBeInTheDocument();
		// top pick shows reasoning (AI prose) when present; subsequent picks fall back to reason
		expect(screen.getByText(/Haaland is the captain this week/)).toBeInTheDocument();
		expect(screen.getByText('7.2')).toBeInTheDocument();
	});
	it('shows a forthcoming message when picks are empty', () => {
		render(CaptainPicks, { props: { captain: { picks: [], vice_player_id: null } } });
		expect(screen.getByText(/Captain ranker not yet available/i)).toBeInTheDocument();
	});
});

describe('CaptainPicks AI/classic badge', () => {
	it('shows AI badge when top pick has reasoning_source ai', () => {
		const captain: Captain = {
			picks: [
				{ player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
				  reason: 'template reason', reasoning: 'AI prose here.', reasoning_source: 'ai' },
			],
			vice_player_id: null,
		};
		render(CaptainPicks, { props: { captain } });
		expect(screen.getByText('AI')).toBeInTheDocument();
		expect(screen.getByText('AI prose here.')).toBeInTheDocument();
	});

	it('shows classic label when top pick has reasoning_source classic', () => {
		const captain: Captain = {
			picks: [
				{ player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
				  reason: 'template reason', reasoning: 'template reason',
				  reasoning_source: 'classic' },
			],
			vice_player_id: null,
		};
		render(CaptainPicks, { props: { captain } });
		expect(screen.getByText('classic')).toBeInTheDocument();
		expect(screen.getByText('template reason')).toBeInTheDocument();
	});

	it('falls back to reason when reasoning fields absent (backwards-compat)', () => {
		const captain: Captain = {
			picks: [
				{ player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
				  reason: 'template reason' },
			],
			vice_player_id: null,
		};
		render(CaptainPicks, { props: { captain } });
		expect(screen.getByText('template reason')).toBeInTheDocument();
	});
});
