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
		padding: 1rem;
		max-width: 720px;
		margin: 0 auto;
	}
	.summary {
		font-size: 1.1rem;
	}
	.card {
		border: 1px solid var(--border, #ccc);
		border-radius: 8px;
		padding: 0.75rem;
		margin: 0.5rem 0;
	}
	.chip {
		display: inline-block;
		font-size: 0.75rem;
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
		background: #eef;
		margin-right: 0.3rem;
	}
	.confidence {
		background: #efe;
	}
	.ev-chip {
		font-family: monospace;
		background: #ffe;
		padding: 0.1rem 0.3rem;
		margin-right: 0.2rem;
		border-radius: 3px;
	}
	.limits {
		font-size: 0.8rem;
		margin-top: 1rem;
	}
	.muted {
		color: #777;
	}
</style>
