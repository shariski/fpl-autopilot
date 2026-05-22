<script lang="ts">
	import type { Squad, Position } from '$lib/types';
	import { dash } from '$lib/format';
	import PlayerCard from './PlayerCard.svelte';
	let { squad }: { squad: Squad } = $props();
	const starters = $derived(squad.players.filter((p) => p.multiplier > 0));
	const bench = $derived(squad.players.filter((p) => p.multiplier === 0));
	const rowFor = (pos: Position) => starters.filter((p) => p.position === pos);
	const order: Position[] = ['GKP', 'DEF', 'MID', 'FWD'];
</script>

<div class="summary tnum">
	Bank {dash(squad.bank, 1)} · Value {dash(squad.team_value, 1)}
	{#if squad.free_transfers !== null}· {squad.free_transfers} FT{/if}
</div>

<div class="pitch">
	{#each order as pos}
		<div class="line">
			{#each rowFor(pos) as p (p.id)}<PlayerCard player={p} />{/each}
		</div>
	{/each}
</div>

<div class="bench">
	{#each bench as p (p.id)}<PlayerCard player={p} />{/each}
</div>

<style>
	.summary { font-size: 0.78rem; color: var(--text-dim); margin-bottom: 8px; }
	.pitch {
		background: linear-gradient(180deg, #0e3b2a, #0a2a1f);
		border: 1px solid var(--border); border-radius: var(--radius);
		padding: 12px 8px; display: grid; gap: 12px;
	}
	.line { display: grid; grid-auto-flow: column; gap: 6px; justify-content: center; }
	.bench {
		margin-top: 8px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;
		padding-top: 8px; border-top: 1px dashed var(--border);
	}
</style>
