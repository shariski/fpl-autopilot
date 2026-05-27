import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/svelte';
import Page from './+page.svelte';
import type { AuditReport } from '$lib/types';

function makeReport(overrides: Partial<AuditReport> = {}): AuditReport {
	return {
		gw_range: [3, 5],
		generated_at: '2026-05-27T10:30:00Z',
		model_version: 'v1',
		residuals: [],
		cluster_counts: {},
		aggregate_trends: {},
		proposals: [],
		narrative: null,
		narrative_provider: null,
		...overrides
	};
}

describe('audit page', () => {
	it('renders empty state when audit is null', () => {
		render(Page, { props: { data: { gw: 5, audit: null } } });
		expect(screen.getByText(/no audit available for gw5/i)).toBeInTheDocument();
		expect(screen.getByText(/fpl-autopilot review/i)).toBeInTheDocument();
	});

	it('renders meta, clusters, trends, and residuals sections when audit is present', () => {
		const report = makeReport({
			cluster_counts: { unclassified: 3, xp_model_miss: 1 },
			aggregate_trends: {
				lineup: { n: 4, mean_residual: -1.5, stddev: 2.0, ci_95: [-3.5, 0.5] }
			},
			residuals: [
				{
					activity_log_id: 1,
					gw: 4,
					decision_type: 'lineup',
					subject_player_ids: [10],
					expected_points: 13,
					actual_points: 4,
					residual: -9,
					model_version: 'v1',
					inputs_summary: { web_name: 'Haaland', xp: 6.5 }
				}
			]
		});
		render(Page, { props: { data: { gw: 5, audit: report } } });

		expect(screen.getByTestId('audit-meta')).toBeInTheDocument();
		expect(screen.getByTestId('audit-clusters')).toBeInTheDocument();
		expect(screen.getByTestId('audit-trends')).toBeInTheDocument();
		expect(screen.getByTestId('audit-residuals')).toBeInTheDocument();
		// Residual table includes the player's name
		expect(screen.getByText('Haaland')).toBeInTheDocument();
		// Trends show 'lineup' (scoped — 'lineup' also appears in the residuals table)
		expect(within(screen.getByTestId('audit-trends')).getByText('lineup')).toBeInTheDocument();
	});

	it('renders the narrative section only when narrative is present', () => {
		const withNarrative = makeReport({
			narrative: 'Captain pick performed as expected this window.',
			narrative_provider: 'claude-sonnet-4-6'
		});
		render(Page, { props: { data: { gw: 5, audit: withNarrative } } });
		expect(screen.getByTestId('audit-narrative')).toBeInTheDocument();
		expect(screen.getByText(/Captain pick performed/i)).toBeInTheDocument();
		expect(screen.getByText(/claude-sonnet-4-6/i)).toBeInTheDocument();
	});

	it('omits the narrative section when narrative is null', () => {
		render(Page, { props: { data: { gw: 5, audit: makeReport() } } });
		expect(screen.queryByTestId('audit-narrative')).toBeNull();
	});

	it('renders a proposal with consider / dismiss buttons that are no-ops', () => {
		const consoleSpy = vi.spyOn(console, 'info').mockImplementation(() => {});
		const report = makeReport({
			proposals: [
				{
					parameter: 'thresholds.min_ep_delta_for_transfer',
					current_value: 2.0,
					proposed_value: 2.5,
					justification: 'Transfers underperform by mean -0.7 EP.',
					n_observations: 22,
					confidence: 'high',
					bounded_range: null
				}
			]
		});
		render(Page, { props: { data: { gw: 5, audit: report } } });

		const consider = screen.getByRole('button', { name: /consider/i });
		const dismiss = screen.getByRole('button', { name: /dismiss/i });
		consider.click();
		dismiss.click();

		expect(consoleSpy).toHaveBeenCalledTimes(2);
		consoleSpy.mockRestore();
	});
});
