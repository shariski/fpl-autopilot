import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TransferIdeas from './TransferIdeas.svelte';
import { fullMock } from '$lib/mocks/full';

describe('TransferIdeas', () => {
	it('renders suggestions with EP delta and hit cost', () => {
		render(TransferIdeas, { props: { transfers: fullMock.transfers } });
		expect(screen.getByText(/Hall/)).toBeInTheDocument();
		expect(screen.getByText(/Aina/)).toBeInTheDocument();
		expect(screen.getByText(/\+2\.6/)).toBeInTheDocument();
	});
	it('shows empty_reason when there are no suggestions', () => {
		render(TransferIdeas, {
			props: { transfers: { suggestions: [], empty_reason: 'No transfers worth making this GW.' } }
		});
		expect(screen.getByText('No transfers worth making this GW.')).toBeInTheDocument();
	});
	it('shows a forthcoming message when empty and reason is null', () => {
		render(TransferIdeas, { props: { transfers: { suggestions: [], empty_reason: null } } });
		expect(screen.getByText(/Transfer engine not yet available/i)).toBeInTheDocument();
	});
});
