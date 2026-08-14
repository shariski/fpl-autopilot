<script lang="ts">
	import { onMount } from 'svelte';
	import type { PageData } from './$types';
	import { fetchInsight } from '$lib/api/client';
	import type { PlayerInsight } from '$lib/types';

	let { data }: { data: PageData } = $props();

	let insight = $state<PlayerInsight | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			insight = await fetchInsight(data.playerId);
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head><title>Player {data.playerId} — FPL Autopilot</title></svelte:head>

<div class="insight-page">
	<h1>Player {data.playerId}</h1>

	{#if loading}
		<p class="muted">Analyzing patterns in this player's data — the first time takes up to a minute.</p>
	{:else if error || insight?.status === 'unavailable'}
		<p class="muted">Analysis unavailable.</p>
		<button onclick={() => location.reload()}>Retry</button>
	{:else if insight}
		<p class="summary">{insight.summary}</p>
		<ul class="insights">
			{#each insight.insights as ins}
				<li class="card">
					<span class="chip">{ins.category}</span>
					<span class="chip confidence">{ins.confidence}</span>
					<p class="claim">{ins.claim}</p>
					<p class="evidence">
						{#each ins.evidence_used as ev}
							<span class="ev-chip">{ev}</span>
						{/each}
					</p>
					<p class="implication">{ins.implication}</p>
				</li>
			{/each}
		</ul>
		{#if insight.data_limits?.length}
			<p class="limits muted">{insight.data_limits.join('; ')}</p>
		{/if}
	{/if}
</div>

<style>
	.insight-page {
		padding: 1rem 0;
		max-width: 720px;
		margin: 0 auto;
	}
	.summary {
		font-size: 1.05rem;
		margin: 0 0 1rem;
	}
	.card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		padding: 0.75rem;
		margin: 0.5rem 0;
		list-style: none;
	}
	.insights {
		padding: 0;
		margin: 0;
	}
	.chip {
		display: inline-block;
		font-size: 0.72rem;
		padding: 0.15rem 0.45rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--text-dim);
		margin-right: 0.35rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.chip.confidence {
		background: var(--surface-2);
		color: var(--accent);
	}
	.claim {
		margin: 0.5rem 0 0.35rem;
		color: var(--text);
	}
	.evidence {
		margin: 0 0 0.35rem;
	}
	.ev-chip {
		font-family: var(--mono);
		font-size: 0.75rem;
		background: var(--surface-2);
		color: var(--accent-2);
		padding: 0.1rem 0.35rem;
		margin-right: 0.25rem;
		border-radius: 4px;
	}
	.implication {
		margin: 0;
		font-size: 0.9rem;
		color: var(--text-dim);
	}
	.limits {
		font-size: 0.8rem;
		margin-top: 1rem;
	}
	.muted {
		color: var(--text-dim);
	}
	button {
		background: var(--accent);
		color: #04100b;
		border: none;
		border-radius: var(--radius);
		padding: 0.45rem 1rem;
		font-weight: 600;
		cursor: pointer;
	}
</style>
