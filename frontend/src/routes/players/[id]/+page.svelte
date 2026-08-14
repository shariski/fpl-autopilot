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

<svelte:head>
	<title>{insight?.player?.name ?? `Player ${data.playerId}`} — FPL Autopilot</title>
</svelte:head>

<div class="insight-page">
	<header class="page-head">
		<h1>{insight?.player?.name ?? `Player ${data.playerId}`}</h1>
		{#if insight}
			<p class="id-line">
				<span class="id-chip">{insight.player?.position ?? '—'}</span>
				<span class="id-chip">{insight.player?.team ?? '—'}</span>
				{#if insight.player?.price != null}
					<span class="id-chip">£{insight.player.price}m</span>
				{/if}
				<span class="gw-label">GW {insight.gw ?? '—'}</span>
			</p>
		{/if}
	</header>

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
					<div class="card-top">
						<span class="chip category">{ins.category}</span>
						<span class="chip confidence-{ins.confidence}">{ins.confidence}</span>
					</div>
					<p class="claim">{ins.claim}</p>
					{#if ins.evidence_used?.length}
						<p class="evidence">
							{#each ins.evidence_used as ev}
								<span class="ev-chip">{ev}</span>
							{/each}
						</p>
					{/if}
					<p class="implication">{ins.implication}</p>
				</li>
			{/each}
		</ul>
		{#if insight.data_limits?.length}
			<div class="limits-box">
				<span class="limits-label">Not in this analysis</span>
				<p class="limits">{insight.data_limits.join('; ')}</p>
			</div>
		{/if}
	{/if}
</div>

<style>
	.insight-page {
		padding: 1.25rem 0 2rem;
		max-width: 680px;
		margin: 0 auto;
	}
	.page-head h1 {
		font-size: 1.5rem;
		margin: 0 0 0.1rem;
		color: var(--text);
	}
	.gw-label {
		margin: 0 0 1rem;
		font-size: 0.78rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-dim);
	}
	.id-line {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin: 0 0 1rem;
	}
	.id-chip {
		display: inline-block;
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--text);
	}
	.gw-label {
		margin: 0 0 0 0.3rem;
		font-size: 0.78rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-dim);
	}
	.summary {
		font-size: 1.05rem;
		line-height: 1.5;
		color: var(--text);
		background: var(--surface);
		border-left: 3px solid var(--accent);
		padding: 0.75rem 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		margin: 0 0 1.25rem;
	}
	.insights {
		padding: 0;
		margin: 0;
		list-style: none;
	}
	.card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		padding: 0.9rem 1rem;
		margin: 0 0 0.6rem;
	}
	.card-top {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-bottom: 0.5rem;
	}
	.chip {
		display: inline-block;
		font-size: 0.68rem;
		font-weight: 600;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--text-dim);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.confidence-high {
		background: color-mix(in srgb, var(--accent) 18%, var(--surface-2));
		color: var(--accent);
	}
	.confidence-medium {
		background: color-mix(in srgb, var(--warning) 18%, var(--surface-2));
		color: var(--warning);
	}
	.confidence-low {
		background: color-mix(in srgb, var(--danger) 18%, var(--surface-2));
		color: var(--danger);
	}
	.claim {
		margin: 0 0 0.45rem;
		font-size: 0.98rem;
		line-height: 1.45;
		color: var(--text);
	}
	.evidence {
		margin: 0 0 0.5rem;
	}
	.ev-chip {
		font-family: var(--mono);
		font-size: 0.74rem;
		background: var(--surface-2);
		color: var(--text-dim);
		padding: 0.12rem 0.4rem;
		margin-right: 0.25rem;
		border-radius: 4px;
	}
	.implication {
		margin: 0;
		font-size: 0.88rem;
		line-height: 1.4;
		color: var(--text-dim);
		font-style: italic;
	}
	.limits-box {
		margin-top: 1.25rem;
		padding: 0.7rem 1rem;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}
	.limits-label {
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
	}
	.limits {
		margin: 0.3rem 0 0;
		font-size: 0.8rem;
		line-height: 1.4;
		color: var(--text-dim);
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
