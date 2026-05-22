import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ChipRecommendation from './ChipRecommendation.svelte';

describe('ChipRecommendation', () => {
	it('renders the chip name and reason when present', () => {
		render(ChipRecommendation, {
			props: { chips: { recommendation: { chip: 'bench_boost', reason: 'DGW: bench xP 5.2 (> 4).' } } }
		});
		expect(screen.getByText(/Bench Boost/i)).toBeInTheDocument();
		expect(screen.getByText(/DGW: bench xP/)).toBeInTheDocument();
	});
	it('renders nothing when recommendation is null', () => {
		const { container } = render(ChipRecommendation, { props: { chips: { recommendation: null } } });
		expect(container.textContent?.trim()).toBe('');
	});
});
