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

	const POS_LABELS: Record<string, string> = {
		GKP: 'Goalkeepers',
		DEF: 'Defenders',
		MID: 'Midfielders',
		FWD: 'Forwards'
	};

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

	const spikeName = (pid: number) =>
		builder?.picks.find((p) => p.player_id === pid)?.web_name ?? `#${pid}`;

	const signalName = (s: { web_name?: string; player_id: number }) =>
		s.web_name ?? spikeName(s.player_id);
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
			<section class="line-block">
				<h2 class="pos-label">{POS_LABELS[pos]}</h2>
				<div class="line">
					{#each row(pos) as pk}
						<div class="pick">
							<PlayerCard player={pickCard(pk)} />
							<span class="slot">
								{pk.slot} · {pk.xp_6gw} xP
								{#if pk.spike_bonus}
									<span class="bonus-chip">+{pk.spike_bonus} AI</span>
								{/if}
							</span>
							<p class="reason">{pk.reason}</p>
						</div>
					{/each}
				</div>
			</section>
		{/each}
		<p class="budget">Budget: {builder.budget_used}m used / 100m</p>
		{#if builder.speculation}
			<section class="spec-block">
				<h2 class="pos-label">AI speculation</h2>
				<p class="market-read">{builder.speculation.market_read}</p>
				{#if builder.speculation.spikes?.length}
					<ul class="spec-list">
						{#each builder.speculation.spikes as s}
							<li class="spec-item spike">
								<span class="spec-badge">{s.level}</span>
								<strong>{signalName(s)}</strong>
								<span class="spec-reason">{s.reason}</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if builder.speculation.drops?.length}
					<ul class="spec-list">
						{#each builder.speculation.drops as s}
							<li class="spec-item drop">
								<span class="spec-badge">{s.level}</span>
								<strong>{signalName(s)}</strong>
								<span class="spec-reason">{s.reason}</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if builder.speculation.differentials?.length}
					<h3 class="diff-label">Differential calls — spiked, but left out of the XI</h3>
					<ul class="spec-list">
						{#each builder.speculation.differentials as s}
							<li class="spec-item spike">
								<span class="spec-badge">{s.level}</span>
								<strong>{signalName(s)}</strong>
								<span class="spec-reason">{s.reason}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/if}
		{#if builder.risks?.length}
			<ul class="risks">{#each builder.risks as r}<li>{r}</li>{/each}</ul>
		{/if}
		<p class="muted hint">Apply it: run <code>fpl-autopilot apply-squad --live</code> on the server.</p>
	{/if}
</div>

<style>
	.builder-page {
		padding: 1.25rem 0 2rem;
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
		margin-bottom: 1.25rem;
		line-height: 1.5;
	}
	.chip {
		flex-shrink: 0;
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--accent);
		align-self: flex-start;
		margin-top: 0.2rem;
	}
	.pos-label {
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-dim);
		margin: 1.1rem 0 0.4rem;
	}
	.line {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
	}
	.pick {
		min-width: 0;
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
		margin: 0.25rem 0 0;
		line-height: 1.35;
	}
	.budget {
		margin-top: 1.25rem;
		font-weight: 600;
	}
	.risks {
		color: var(--text-dim);
		font-size: 0.85rem;
		padding-left: 1.1rem;
		line-height: 1.5;
	}
	.spec-block {
		margin-top: 1.25rem;
	}
	.market-read {
		background: var(--surface);
		border-left: 3px solid var(--warning);
		padding: 0.75rem 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		line-height: 1.5;
		margin: 0 0 0.5rem;
	}
	.spec-list {
		list-style: none;
		padding: 0;
		margin: 0.4rem 0;
	}
	.spec-item {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		font-size: 0.88rem;
		padding: 0.35rem 0;
		border-bottom: 1px dashed var(--border);
	}
	.spec-item:last-child {
		border-bottom: none;
	}
	.spec-badge {
		flex-shrink: 0;
		font-size: 0.66rem;
		font-weight: 700;
		text-transform: uppercase;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
	}
	.spec-item.spike .spec-badge {
		background: color-mix(in srgb, var(--accent) 18%, var(--surface-2));
		color: var(--accent);
	}
	.spec-item.drop .spec-badge {
		background: color-mix(in srgb, var(--danger) 18%, var(--surface-2));
		color: var(--danger);
	}
	.spec-reason {
		color: var(--text-dim);
		font-size: 0.82rem;
	}
	.diff-label {
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--warning);
		margin: 0.8rem 0 0.2rem;
	}
	.bonus-chip {
		display: inline-block;
		margin-left: 0.35rem;
		font-size: 0.64rem;
		font-weight: 700;
		padding: 0.08rem 0.4rem;
		border-radius: 999px;
		background: color-mix(in srgb, var(--accent) 20%, var(--surface-2));
		color: var(--accent);
	}
	.hint {
		margin-top: 1.25rem;
	}
	.hint code {
		background: var(--surface-2);
		padding: 0.1rem 0.3rem;
		border-radius: 4px;
		word-break: break-all;
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
