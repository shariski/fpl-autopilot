import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ChipRecommendation from './ChipRecommendation.svelte';
import type { Chips } from '$lib/types';

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

describe('ChipRecommendation AI/classic tag + prose', () => {
    it('shows AI prose and AI tag when reasoning_source is ai', () => {
        const chips: Chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.',
                              reasoning: 'AI chip prose here.', reasoning_source: 'ai' },
        };
        render(ChipRecommendation, { chips });
        expect(screen.getByText('AI chip prose here.')).toBeInTheDocument();
        expect(screen.getByText('AI')).toBeInTheDocument();
        expect(screen.queryByText('GW39 DGW: Haaland DGW-xP 14.8.')).not.toBeInTheDocument();
    });

    it('shows engine reason with no AI tag when reasoning_source is classic', () => {
        const chips: Chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.',
                              reasoning: 'GW39 DGW: Haaland DGW-xP 14.8.', reasoning_source: 'classic' },
        };
        render(ChipRecommendation, { chips });
        expect(screen.getByText('GW39 DGW: Haaland DGW-xP 14.8.')).toBeInTheDocument();
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
    });

    it('renders backwards-compat when reasoning fields are absent', () => {
        const chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.' },
        } as Chips;
        render(ChipRecommendation, { chips });
        expect(screen.getByText('GW39 DGW: Haaland DGW-xP 14.8.')).toBeInTheDocument();
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
    });

    it('renders nothing when no recommendation', () => {
        const chips = { recommendation: null };
        const { container } = render(ChipRecommendation, { chips });
        expect(container.querySelector('.chip-rec')).not.toBeInTheDocument();
    });
});
