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
		notes = await fetchNotes();
	}

	async function remove(id: number) {
		await deleteNote(id);
		notes = await fetchNotes();
	}
</script>

<svelte:head><title>Speculation — FPL Autopilot</title></svelte:head>

<div class="speculation-page">
	<h1>Speculation Insights</h1>
	<p class="muted">Your match-watching reads (managers, cohesion, traits) — the system
		cross-checks them against its own stats in <code>speculate --json</code> (theses).</p>

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="muted">Could not load notes.</p>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); submit(); }}>
			<label>
				Insight
				<textarea bind:value={noteText} rows="2" placeholder="e.g. xabi alonso is pretty good"></textarea>
			</label>
			<label>
				Team
				<select bind:value={teamId} onchange={onTeamChange}>
					<option value={null}>— none —</option>
					{#each teams as t}
						<option value={t.id}>{t.short_name}</option>
					{/each}
				</select>
			</label>
			{#if teamId}
				<label>
					Player
					<select bind:value={playerId}>
						<option value={null}>— none —</option>
						{#each players as p}
							<option value={p.id}>{p.web_name}</option>
						{/each}
					</select>
				</label>
			{/if}
			<button type="submit" disabled={!noteText.trim()}>Add</button>
		</form>

		<h2>Active insights</h2>
		{#if notes.length === 0}
			<p class="muted">No insights yet — add your first read above.</p>
		{/if}
		<ul>
			{#each notes as n (n.id)}
				<li>
					<span class="scope">{[n.team_short, n.player_name].filter(Boolean).join(' · ')}</span>
					{n.note}
					<button onclick={() => remove(n.id)} aria-label={`remove note ${n.id}`}>✕</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
