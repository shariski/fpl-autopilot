import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

vi.mock('echarts/core', () => ({
	use: vi.fn(),
	init: vi.fn(() => ({
		setOption: vi.fn(),
		dispose: vi.fn(),
		resize: vi.fn()
	}))
}));
vi.mock('echarts/charts', () => ({ LineChart: {}, BarChart: {}, HeatmapChart: {} }));
vi.mock('echarts/components', () => ({
	GridComponent: {}, TooltipComponent: {}, LegendComponent: {}, TitleComponent: {}
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

afterEach(() => vi.unstubAllGlobals());

const payload = {
	cohort: [{
		entry_id: 1, player_name: 'Harman Messi', entry_name: 'shadi', rank: 1, total: 227,
		last_gw_points: 91, transfers: 0, hit_cost: 0, bank: 0, value: 1000,
		chips_used: ['3xc'], past_rank: 12310989
	}],
	patterns: {
		chip_timing: { rows: [{ chip: '3xc', gw: 2, count: 23 }], first_chip: { '3xc': { gw: 2, count: 23 } } },
		transfers: { mean_per_gw: 1.1, median_per_gw: 1.0, hit_freq: 0.08, mean_hit_cost: 2.1,
					 histogram: [{ transfers: 0, count: 410 }] },
		bank_value: { bank: [{ gw: 1, mean: 3.2, median: 2.5 }], value: [{ gw: 1, mean: 1005, median: 1004 }] },
		momentum: { top_movers: [{ entry_id: 1, player_name: 'Harman Messi', from_gw: 1, to_gw: 2, rank_gain: 900 }],
					sustained_elite: [] }
	}
};

const emptyPayload = {
	cohort: [],
	patterns: {
		chip_timing: { rows: [], first_chip: {} },
		transfers: { mean_per_gw: null, median_per_gw: null, hit_freq: null, mean_hit_cost: null, histogram: [] },
		bank_value: { bank: [], value: [] },
		momentum: { top_movers: [], sustained_elite: [] }
	}
};

describe('leaders page', () => {
	it('renders the empty state before any snapshot', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => emptyPayload }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/No snapshots yet/i));
		expect(screen.queryByText(/Chip timing/)).not.toBeInTheDocument();
	});

	it('renders cohort and pattern sections', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => payload }));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/Harman Messi/));
		expect(screen.getByText(/Chip timing/)).toBeInTheDocument();
		expect(screen.getByText(/Transfer discipline/)).toBeInTheDocument();
		expect(screen.getByText(/Bank & value/)).toBeInTheDocument();
		expect(screen.getByText(/Rank momentum/)).toBeInTheDocument();
		expect(screen.getByText(/1.1/)).toBeInTheDocument();           // mean transfers/GW stat
		expect(screen.getByText(/TC/)).toBeInTheDocument();            // chip label in cohort
	});
});
