<script lang="ts">
	import type { SquadPlayer } from '$lib/types';
	import { dash, money } from '$lib/format';
	let { player }: { player: SquadPlayer } = $props();
	const flag = $derived(player.status !== 'a');
</script>

<div class="card" class:bench={player.multiplier === 0}>
	<div class="top">
		{#if player.is_captain}<span class="band" aria-label="captain">C</span>{/if}
		{#if player.is_vice_captain}<span class="band v" aria-label="vice-captain">V</span>{/if}
		{#if flag}<span class="status {player.status}" title={player.status}></span>{/if}
	</div>
	<div class="name">{player.web_name}</div>
	<div class="meta tnum">{player.team_short} · {money(player.price)}</div>
	<div class="xp tnum">
		<span>{dash(player.xp_next, 1)}</span><small>{dash(player.xp_next5, 1)}</small>
	</div>
</div>

<style>
	.card { position: relative; background: var(--surface); border: 1px solid var(--border);
		border-radius: 10px; padding: 8px 6px; text-align: center; min-width: 0; }
	.card.bench { opacity: 0.72; }
	.top { position: absolute; top: 4px; left: 4px; display: flex; gap: 3px; }
	.band { font-size: 0.6rem; font-weight: 700; background: var(--accent); color: #00261c;
		border-radius: 4px; padding: 0 3px; }
	.band.v { background: var(--accent-2); color: #fff; }
	.status { position: absolute; top: 4px; right: 4px; width: 7px; height: 7px; border-radius: 50%; background: var(--warning); }
	.status.i, .status.s, .status.u { background: var(--danger); }
	.name { font-size: 0.82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.meta { font-size: 0.66rem; color: var(--text-dim); }
	.xp { margin-top: 4px; font-size: 0.82rem; color: var(--accent); }
	.xp small { color: var(--text-dim); margin-left: 6px; font-size: 0.66rem; }
</style>
