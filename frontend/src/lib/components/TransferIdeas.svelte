<script lang="ts">
	import type { Transfers } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { transfers }: { transfers: Transfers } = $props();
	const fmtDelta = (n: number) => (n >= 0 ? `+${n.toFixed(1)}` : n.toFixed(1));
</script>

{#if transfers.suggestions.length === 0}
	<EmptyState
		message={transfers.empty_reason ??
			'Transfer engine not yet available — arrives with the decision engine.'}
	/>
{:else}
	<ul class="xfers">
		{#each transfers.suggestions as s (s.out.player_id + '-' + s.in.player_id)}
			<li class="xfer">
				<div class="move">
					<span class="out">{s.out.web_name}</span>
					<span class="arrow">→</span>
					<span class="in">{s.in.web_name}</span>
				</div>
				<div class="nums tnum">
					<span class="delta">{fmtDelta(s.ep_delta_5gw)} EP</span>
					<span class="hit" class:free={s.hit_cost === 0}>
						{s.hit_cost === 0 ? 'free' : s.hit_cost}
					</span>
					<span class="conf">{s.confidence}%</span>
				</div>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.xfers { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
	.xfer { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
		padding: 10px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
	.move { display: flex; align-items: center; gap: 8px; font-weight: 600; min-width: 0; }
	.out { color: var(--danger); }
	.in { color: var(--accent); }
	.arrow { color: var(--text-dim); }
	.nums { display: flex; gap: 10px; font-size: 0.78rem; align-items: center; }
	.delta { color: var(--text); }
	.hit { color: var(--danger); }
	.hit.free { color: var(--text-dim); }
	.conf { color: var(--text-dim); }
</style>
