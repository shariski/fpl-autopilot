import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

afterEach(() => vi.unstubAllGlobals());

const note = (id: number, text: string) => ({
	id, note: text, team_id: null, player_id: null,
	team_short: 'CHE', player_name: null, created_at: 't', active: true
});

describe('speculation page', () => {
	it('lists notes and submits a new one', async () => {
		const notes = [note(1, 'xabi alonso is pretty good')];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ notes }) })       // GET notes
			.mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [] }) })   // GET teams
			.mockResolvedValueOnce({ ok: true, json: async () => ({ note: notes[0] }) }) // POST
			.mockResolvedValue({ ok: true, json: async () => ({ notes }) });             // reload
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/xabi alonso is pretty good/));
		await fireEvent.input(screen.getByLabelText(/insight/i), { target: { value: 'new note' } });
		await fireEvent.click(screen.getByRole('button', { name: /add insight/i }));
		await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'POST')).toBe(true));
	});

	it('deletes a note only after confirmation', async () => {
		const notes = [note(5, 'rogers takes long shots')];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ notes }) })    // GET notes
			.mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [] }) }) // GET teams
			.mockResolvedValueOnce({ ok: true })                                    // DELETE
			.mockResolvedValue({ ok: true, json: async () => ({ notes: [] }) });    // reload
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('confirm', vi.fn(() => true));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/rogers takes long shots/));
		await fireEvent.click(screen.getByRole('button', { name: /remove note 5/i }));
		await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'DELETE')).toBe(true));
		expect(vi.mocked(confirm)).toHaveBeenCalledWith('Delete this insight?');
	});

	it('keeps the note when the confirmation is declined', async () => {
		const notes = [note(5, 'rogers takes long shots')];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ notes }) })    // GET notes
			.mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [] }) }); // GET teams (only)
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('confirm', vi.fn(() => false));
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/rogers takes long shots/));
		await fireEvent.click(screen.getByRole('button', { name: /remove note 5/i }));
		await new Promise((r) => setTimeout(r, 50));
		expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'DELETE')).toBe(false);
		expect(screen.getByText(/rogers takes long shots/)).toBeInTheDocument();
	});
});
