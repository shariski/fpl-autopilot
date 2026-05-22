<script lang="ts">
	import type { Planner } from '$lib/types';
	import { fdrToken, cellFdr } from '$lib/fdr';
	let { planner }: { planner: Planner } = $props();
	const cols = $derived(planner.horizon.length);
</script>

<div class="grid" style={`--cols:${cols}`}>
	<div class="head name">Player</div>
	{#each planner.horizon as gw}<div class="head tnum">{gw}</div>{/each}

	{#each planner.rows as row (row.player_id)}
		<div class="name">
			<span class="pn">{row.web_name}</span>
			<span class="tm">{row.team_short}</span>
		</div>
		{#each row.cells as cell, i}
			{#if cell === null}
				<div class="cell blank" data-testid={`cell-${row.player_id}-${planner.horizon[i]}`}>—</div>
			{:else}
				{@const v = cellFdr(row.position, cell)}
				<div
					class="cell tnum"
					style={`background:${fdrToken(v)}`}
					data-testid={`cell-${row.player_id}-${cell.gw}`}
					title={`${cell.opponent_short} ${cell.home ? '(H)' : '(A)'} · FDR ${v}`}
				>
					{v}
				</div>
			{/if}
		{/each}
	{/each}
</div>

<style>
	.grid {
		display: grid;
		grid-template-columns: minmax(64px, 1.4fr) repeat(var(--cols), 1fr);
		gap: 3px; align-items: stretch;
	}
	.head { font-size: 0.66rem; color: var(--text-dim); text-align: center; padding: 2px 0; }
	.head.name { text-align: left; }
	.name { display: flex; flex-direction: column; justify-content: center; min-width: 0; }
	.pn { font-size: 0.74rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.tm { font-size: 0.6rem; color: var(--text-dim); }
	.cell { display: flex; align-items: center; justify-content: center; aspect-ratio: 1 / 1;
		border-radius: 6px; font-size: 0.74rem; font-weight: 700; color: #0b0f14; }
	.cell.blank { background: var(--surface-2); color: var(--text-dim); font-weight: 400; }
</style>
