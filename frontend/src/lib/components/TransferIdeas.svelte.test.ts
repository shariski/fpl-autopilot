import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TransferIdeas from './TransferIdeas.svelte';
import type { Transfers } from '$lib/types';
import { fullMock } from '$lib/mocks/full';

describe('TransferIdeas', () => {
	it('renders suggestions with EP delta and hit cost', () => {
		render(TransferIdeas, { props: { transfers: fullMock.transfers } });
		// Hall and Aina appear in both the player name chips and the AI prose; use getAllByText
		expect(screen.getAllByText(/Hall/).length).toBeGreaterThan(0);
		expect(screen.getAllByText(/Aina/).length).toBeGreaterThan(0);
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

describe('TransferIdeas AI/classic badge + prose', () => {
	it('shows AI badge and prose on the top suggestion when reasoning_source is ai', () => {
		const transfers = {
			suggestions: [
				{ out: { player_id: 1, web_name: 'Salah', price: 13.1 },
				  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
				  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
				  reasoning: 'AI transfer prose here.', reasoning_source: 'ai' as const },
				{ out: { player_id: 3, web_name: 'Isak',     price: 9.3 },
				  in:  { player_id: 4, web_name: 'Watkins',  price: 9.0 },
				  ep_delta_5gw: 1.2, hit_cost: 0, confidence: 65,
				  reasoning: '', reasoning_source: 'classic' as const },
			],
			empty_reason: null,
			free_transfers: 1,
		};
		render(TransferIdeas, { transfers: transfers as Transfers });
		expect(screen.getByText('AI transfer prose here.')).toBeInTheDocument();
		expect(screen.getByText('AI')).toBeInTheDocument();
	});

	it('shows no prose line on top suggestion when reasoning is empty', () => {
		const transfers = {
			suggestions: [
				{ out: { player_id: 1, web_name: 'Salah', price: 13.1 },
				  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
				  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
				  reasoning: '', reasoning_source: 'classic' as const },
			],
			empty_reason: null,
			free_transfers: 1,
		};
		render(TransferIdeas, { transfers: transfers as Transfers });
		expect(screen.queryByText('AI')).not.toBeInTheDocument();
		expect(screen.queryByText('classic')).not.toBeInTheDocument();
	});

	it('does not show badge on suggestions other than the top', () => {
		const transfers = {
			suggestions: [
				{ out: { player_id: 1, web_name: 'Salah', price: 13.1 },
				  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
				  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
				  reasoning: 'AI top.', reasoning_source: 'ai' as const },
				{ out: { player_id: 3, web_name: 'Isak',     price: 9.3 },
				  in:  { player_id: 4, web_name: 'Watkins',  price: 9.0 },
				  ep_delta_5gw: 1.2, hit_cost: 0, confidence: 65,
				  reasoning: 'AI second.', reasoning_source: 'ai' as const },
			],
			empty_reason: null,
			free_transfers: 1,
		};
		render(TransferIdeas, { transfers: transfers as Transfers });
		expect(screen.getAllByText('AI')).toHaveLength(1);
		expect(screen.queryByText('AI second.')).not.toBeInTheDocument();
	});

	it('renders backwards-compat when reasoning fields are absent', () => {
		const transfers = {
			suggestions: [
				{ out: { player_id: 1, web_name: 'Salah', price: 13.1 },
				  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
				  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78 },
			],
			empty_reason: null,
			free_transfers: 1,
		};
		render(TransferIdeas, { transfers: transfers as Transfers });
		expect(screen.getByText('Salah')).toBeInTheDocument();
		expect(screen.queryByText('AI')).not.toBeInTheDocument();
	});
});
