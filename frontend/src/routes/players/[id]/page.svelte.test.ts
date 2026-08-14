import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

const insight = {
	status: 'generated',
	player_id: 1,
	player: { name: 'Lewis Hall', web_name: 'Hall', position: 'DEF', team: 'NEW', price: 5.0 },
	gw: 38,
	insights: [
		{
			category: 'fixture_alignment',
			claim: 'Three home fixtures against low-FDR defences.',
			evidence_used: ['1', '1'],
			confidence: 'high',
			implication: 'Good run ahead.'
		}
	],
	summary: 'Strong fixture run.',
	data_limits: ['no current-season minutes yet (pre-season)'],
	model_id: 'deepseek-chat',
	generated_at: '2026-08-14T00:00:00Z'
};

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('players/[id] page', () => {
	it('renders player identity, insight cards with evidence and limits', async () => {
		const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => insight });
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: { playerId: 1 } } });
		await waitFor(() => screen.getByText('Strong fixture run.'));
		expect(screen.getByText('Lewis Hall')).toBeInTheDocument();
		expect(screen.getByText('NEW')).toBeInTheDocument();
		expect(screen.getByText('fixture_alignment')).toBeInTheDocument();
		expect(screen.getByText('high')).toBeInTheDocument();
		expect(screen.getByText(/no current-season minutes/)).toBeInTheDocument();
	});

	it('shows loading then unavailable on a failing endpoint', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue({ ok: true, json: async () => ({ status: 'unavailable' }) });
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: { playerId: 1 } } });
		await waitFor(() => screen.getByText('Analysis unavailable.'));
		expect(screen.getByText('Retry')).toBeInTheDocument();
	});
});
