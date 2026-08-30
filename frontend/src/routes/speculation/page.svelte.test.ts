import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

afterEach(() => vi.unstubAllGlobals());

describe('speculation page', () => {
	it('lists notes and submits a new one', async () => {
		const notes = [
			{ id: 1, note: 'xabi alonso is pretty good', team_id: 1, player_id: null,
			  team_short: 'CHE', player_name: null, created_at: 't', active: true }
		];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ notes }) })       // GET notes
			.mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [] }) })   // GET teams
			.mockResolvedValueOnce({ ok: true, json: async () => ({ note: notes[0] }) }) // POST
			.mockResolvedValue({ ok: true, json: async () => ({ notes }) });             // reload
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/xabi alonso is pretty good/));
		await fireEvent.input(screen.getByLabelText(/insight/i), { target: { value: 'new note' } });
		await fireEvent.click(screen.getByRole('button', { name: /add/i }));
		await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'POST')).toBe(true));
	});

	it('removes a note', async () => {
		const notes = [
			{ id: 5, note: 'rogers takes long shots', team_id: null, player_id: null,
			  team_short: null, player_name: null, created_at: 't', active: true }
		];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ notes }) })    // GET notes
			.mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [] }) }) // GET teams
			.mockResolvedValueOnce({ ok: true })                                    // DELETE
			.mockResolvedValue({ ok: true, json: async () => ({ notes: [] }) });    // reload
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/rogers takes long shots/));
		await fireEvent.click(screen.getByRole('button', { name: /remove note 5/i }));
		await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'DELETE')).toBe(true));
	});
});
