import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

const builder = {
	status: 'generated',
	gw: 38,
	source: 'ai',
	picks: [
		{
			player_id: 1,
			web_name: 'Hall',
			team: 'NEW',
			position: 'DEF',
			price: 5.0,
			xp_6gw: 24.3,
			slot: 'DEF1',
			reason: 'Stable minutes and value.'
		}
	],
	template_rationale: 'Balanced template with a value defense.',
	risks: ['Fixture rotation risk.'],
	budget_used: 99.5,
	speculation: {
		spikes: [{ player_id: 1, level: 'high', reason: 'Three home fixtures at fdr 1.' }],
		drops: [],
		market_read: 'Midfield-heavy slate.'
	},
	model_id: 'deepseek-chat',
	generated_at: '2026-08-14T00:00:00Z'
};

afterEach(() => vi.unstubAllGlobals());

describe('squad-builder page', () => {
	it('renders picks, speculation and budget', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => builder }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText('Balanced template with a value defense.'));
		expect(screen.getAllByText('Hall').length).toBeGreaterThan(0);
		expect(screen.getByText(/99\.5m used \/ 100m/)).toBeInTheDocument();
		expect(screen.getByText(/apply-squad/)).toBeInTheDocument();
		expect(screen.getByText('AI speculation')).toBeInTheDocument();
		expect(screen.getByText('Midfield-heavy slate.')).toBeInTheDocument();
		expect(screen.getByText(/Three home fixtures at fdr 1\./)).toBeInTheDocument();
	});
});
