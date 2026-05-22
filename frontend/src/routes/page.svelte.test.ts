import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Page from './+page.svelte';
import { fullMock } from '$lib/mocks/full';
import { launchMock } from '$lib/mocks/launch';

describe('+page (composition)', () => {
	it('full scenario renders all seven sections incl. the chip section', () => {
		render(Page, { props: { data: { dashboard: fullMock, scenario: 'full' } } });
		for (const id of ['team', 'captain', 'transfers', 'chip', 'fixtures', 'log'])
			expect(document.getElementById(id)).not.toBeNull();
		expect(screen.getAllByText(/GW38/)[0]).toBeInTheDocument();
	});
	it('launch scenario hides the chip section and shows forthcoming states', () => {
		render(Page, { props: { data: { dashboard: launchMock, scenario: 'launch' } } });
		expect(document.getElementById('chip')).toBeNull();
		expect(screen.getByText(/Captain ranker not yet available/i)).toBeInTheDocument();
		expect(screen.getByText(/No decisions logged yet/i)).toBeInTheDocument();
	});
});
