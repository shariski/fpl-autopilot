import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/svelte';
import FixturePlanner from './FixturePlanner.svelte';
import { fullMock } from '$lib/mocks/full';

describe('FixturePlanner', () => {
	it('renders the GW horizon header', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		for (const gw of fullMock.planner.horizon)
			expect(screen.getByText(String(gw))).toBeInTheDocument();
	});
	it('colours an attacker cell by fdr_attack and a defender cell by fdr_defense', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		// Mbeumo (MID): GW39 fdr_attack 4 vs fdr_defense 3 -> must use attack -> var(--fdr-4)
		const mbe = screen.getByTestId('cell-9-39');
		expect(mbe.getAttribute('style')).toContain('--fdr-4');
		// Gabriel (DEF): GW38 fdr_attack 2 vs fdr_defense 3 -> must use defense -> var(--fdr-3)
		const gab = screen.getByTestId('cell-2-38');
		expect(gab.getAttribute('style')).toContain('--fdr-3');
	});
	it('renders the FDR number inside each cell', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		const haa = screen.getByTestId('cell-10-38');
		expect(within(haa).getByText('3')).toBeInTheDocument();
	});
	it('renders a blank-GW cell as an em-dash', () => {
		render(FixturePlanner, { props: { planner: fullMock.planner } });
		// Hall (id 5) has a null cell at GW40 in the fixture
		const blank = screen.getByTestId('cell-5-40');
		expect(blank).toHaveTextContent('—');
	});
});
