<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchSquadBuilder } from '$lib/api/client';
	import type { SquadBuilder, SquadPick, SquadPlayer } from '$lib/types';
	import PlayerCard from '$lib/components/PlayerCard.svelte';

	let builder = $state<SquadBuilder | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			builder = await fetchSquadBuilder();
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	const row = (pos: string) => builder?.picks.filter((p) => p.position === pos) ?? [];

	const pickCard = (pk: SquadPick): SquadPlayer => ({
		id: pk.player_id,
		web_name: pk.web_name,
		position: pk.position,
		team_short: pk.team,
		price: pk.price,
		status: 'a',
		is_captain: false,
		is_vice_captain: false,
		multiplier: 1,
		xp_next: null,
		xp_next5: null
	});
</script>

<svelte:head><title>Squad Builder — FPL Autopilot</title></svelte:head>

<div class="builder-page">
	<h1>AI Squad Builder</h1>

	{#if loading}
		<p class="muted">Building your optimal squad — the first time takes up to a minute.</p>
	{:else if error || builder?.status === 'unavailable'}
		<p class="muted">Squad builder unavailable.</p>
		<button onclick={() => location.reload()}>Retry</button>
	{:else if builder}
		<p class="lead">
			<span class="chip">{builder.source === 'ai' ? 'AI' : 'optimizer'}</span>
			{builder.template_rationale}
		</p>
		{#each ['GKP', 'DEF', 'MID', 'FWD'] as pos}
			<section class="line">
				{#each row(pos) as pk}
					<div class="pick">
						<PlayerCard player={pickCard(pk)} />
						<span class="slot">{pk.slot} · {pk.xp_6gw} xP</span>
						<p class="reason">{pk.reason}</p>
					</div>
				{/each}
			</section>
		{/each}
		<p class="budget">Budget: {builder.budget_used}m used / 100m</p>
		{#if builder.risks?.length}
			<ul class="risks">{#each builder.risks as r}<li>{r}</li>{/each}</ul>
		{/if}
		<p class="muted hint">Apply it: run <code>fpl-autopilot apply-squad --live</code> on the server.</p>
	{/if}
</div>

<style>
	.builder-page {
		padding: 1.25rem 0 2rem;
		max-width: 680px;
		margin: 0 auto;
	}
	h1 {
		font-size: 1.5rem;
		margin: 0 0 1rem;
	}
	.lead {
		display: flex;
		gap: 0.5rem;
		align-items: baseline;
		background: var(--surface);
		border-left: 3px solid var(--accent);
		padding: 0.75rem 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		margin-bottom: 1rem;
	}
	.chip {
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--accent);
	}
	.line {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(120px, 1fr);
		gap: 8px;
		overflow-x: auto;
		padding: 10px 0;
		border-bottom: 1px dashed var(--border);
	}
	.pick {
		min-width: 120px;
	}
	.slot {
		display: block;
		margin-top: 4px;
		font-size: 0.7rem;
		color: var(--text-dim);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.reason {
		font-size: 0.78rem;
		color: var(--text-dim);
		margin: 0.3rem 0 0;
		line-height: 1.35;
	}
	.budget {
		margin-top: 1rem;
		font-weight: 600;
	}
	.risks {
		color: var(--text-dim);
		font-size: 0.85rem;
	}
	.hint code {
		background: var(--surface-2);
		padding: 0.1rem 0.3rem;
		border-radius: 4px;
	}
	.muted {
		color: var(--text-dim);
	}
	button {
		background: var(--accent);
		color: #04100b;
		border: none;
		border-radius: var(--radius);
		padding: 0.45rem 1.1rem;
		font-weight: 600;
		cursor: pointer;
	}
</style>
