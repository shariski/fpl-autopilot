<script lang="ts">
	import { onMount } from 'svelte';
	import { deleteNote, fetchNotes, postNote } from '$lib/api/client';
	import type { SpeculationNote } from '$lib/types';

	let notes = $state<SpeculationNote[]>([]);
	let noteText = $state('');
	let teamId = $state<number | null>(null);
	let playerId = $state<number | null>(null);
	let teams = $state<{ id: number; short_name: string }[]>([]);
	let players = $state<{ id: number; web_name: string }[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			[notes, teams] = await Promise.all([fetchNotes(), fetchTeams()]);
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	async function fetchTeams() {
		const res = await fetch('/api/speculation/teams');
		return res.ok ? (await res.json()).teams : [];
	}

	async function onTeamChange() {
		playerId = null;
		players = teamId ? await fetchPlayers(teamId) : [];
	}

	async function fetchPlayers(tid: number) {
		const res = await fetch(`/api/speculation/players?team_id=${tid}`);
		return res.ok ? (await res.json()).players : [];
	}

	async function submit() {
		if (!noteText.trim()) return;
		await postNote({ note: noteText.trim(), team_id: teamId, player_id: playerId });
		noteText = '';
		teamId = null;
		playerId = null;
		notes = await fetchNotes();
	}

	async function remove(id: number) {
		if (!confirm('Delete this insight?')) return;
		await deleteNote(id);
		notes = await fetchNotes();
	}
</script>

<svelte:head><title>Speculation — FPL Autopilot</title></svelte:head>

<div class="speculation-page">
	<h1>Speculation</h1>
	<p class="muted lead-note">
		Your match-watching reads — managers, cohesion, player traits. The system
		cross-checks each insight against its own stats in
		<code>speculate --json</code> (theses).
	</p>

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<div class="error-card">
			<p class="muted">Could not load notes.</p>
			<button onclick={() => location.reload()}>Retry</button>
		</div>
	{:else}
		<form class="note-form" onsubmit={(e) => { e.preventDefault(); submit(); }}>
			<label class="field">
				<span class="field-label">Insight</span>
				<textarea
					bind:value={noteText}
					rows="2"
					placeholder="e.g. xabi alonso is pretty good — Chelsea under a new manager"
				></textarea>
			</label>
			<div class="field-row">
				<label class="field">
					<span class="field-label">Team</span>
					<select bind:value={teamId} onchange={onTeamChange}>
						<option value={null}>— none —</option>
						{#each teams as t}
							<option value={t.id}>{t.short_name}</option>
						{/each}
					</select>
				</label>
				{#if teamId}
					<label class="field">
						<span class="field-label">Player</span>
						<select bind:value={playerId}>
							<option value={null}>— none —</option>
							{#each players as p}
								<option value={p.id}>{p.web_name}</option>
							{/each}
						</select>
					</label>
				{/if}
			</div>
			<button type="submit" class="primary" disabled={!noteText.trim()}>Add insight</button>
		</form>

		<h2 class="list-label">Active insights</h2>
		{#if notes.length === 0}
			<p class="muted empty">No insights yet — add your first read above.</p>
		{:else}
			<ul class="note-list">
				{#each notes as n (n.id)}
					<li class="note-card">
						<div class="note-main">
							{#if n.team_short || n.player_name}
								<span class="chips">
									{#if n.team_short}<span class="chip team">{n.team_short}</span>{/if}
									{#if n.player_name}<span class="chip player">{n.player_name}</span>{/if}
								</span>
							{/if}
							<p class="note-text">{n.note}</p>
						</div>
						<button
							class="del"
							onclick={() => remove(n.id)}
							aria-label={`remove note ${n.id}`}
							title="Delete this insight"
						>✕</button>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>

<style>
	.speculation-page {
		padding: 1.25rem 0 2rem;
	}
	h1 {
		font-size: 1.5rem;
		margin: 0 0 0.4rem;
	}
	.lead-note {
		font-size: 0.85rem;
		line-height: 1.5;
		margin: 0 0 1.25rem;
	}
	.lead-note code {
		background: var(--surface-2);
		padding: 0.1rem 0.3rem;
		border-radius: 4px;
		word-break: break-all;
	}
	.error-card {
		background: var(--surface);
		border-left: 3px solid var(--danger);
		padding: 0.9rem 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	.error-card .muted {
		margin: 0;
		flex: 1;
	}
	.note-form {
		background: var(--surface);
		border-left: 3px solid var(--accent);
		padding: 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		margin-bottom: 1.25rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		flex: 1;
		min-width: 0;
	}
	.field-label {
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-dim);
	}
	.field-row {
		display: flex;
		gap: 0.85rem;
	}
	textarea,
	select {
		background: var(--surface-2);
		color: var(--text);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 0.5rem 0.6rem;
		font: inherit;
		font-size: 0.88rem;
		resize: vertical;
	}
	textarea:focus,
	select:focus {
		outline: none;
		border-color: var(--accent);
	}
	select {
		width: 100%;
	}
	button {
		font: inherit;
		cursor: pointer;
		border: none;
		border-radius: var(--radius);
	}
	button.primary {
		align-self: flex-start;
		background: var(--accent);
		color: #04100b;
		padding: 0.45rem 1.1rem;
		font-weight: 600;
	}
	button.primary:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.list-label {
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-dim);
		margin: 0 0 0.5rem;
	}
	.empty {
		margin: 0.25rem 0;
	}
	.note-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}
	.note-card {
		display: flex;
		align-items: flex-start;
		gap: 0.6rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.75rem 0.9rem;
	}
	.note-main {
		flex: 1;
		min-width: 0;
	}
	.chips {
		display: inline-flex;
		gap: 0.35rem;
		margin-bottom: 0.35rem;
	}
	.chip {
		font-size: 0.66rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
	}
	.chip.team {
		background: color-mix(in srgb, var(--accent) 18%, var(--surface-2));
		color: var(--accent);
	}
	.chip.player {
		background: color-mix(in srgb, var(--accent-2) 22%, var(--surface-2));
		color: #7fb2ff;
	}
	.note-text {
		margin: 0;
		font-size: 0.88rem;
		line-height: 1.45;
	}
	button.del {
		flex-shrink: 0;
		background: transparent;
		color: var(--text-dim);
		font-size: 0.8rem;
		padding: 0.3rem 0.5rem;
		border-radius: 8px;
		line-height: 1;
	}
	button.del:hover {
		background: color-mix(in srgb, var(--danger) 18%, transparent);
		color: var(--danger);
	}
	.muted {
		color: var(--text-dim);
	}
</style>
