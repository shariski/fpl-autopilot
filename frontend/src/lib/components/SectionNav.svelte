<script lang="ts">
	let { hasChip }: { hasChip: boolean } = $props();
	let items = $derived([
		{ id: 'team', label: 'Team' },
		{ id: 'captain', label: 'Captain' },
		{ id: 'transfers', label: 'Transfers' },
		...(hasChip ? [{ id: 'chip', label: 'Chip' }] : []),
		{ id: 'fixtures', label: 'Fixtures' },
		{ id: 'log', label: 'Log' }
	]);
</script>

<nav class="nav">
	{#each items as it}
		<a class="chip" href={`#${it.id}`}>{it.label}</a>
	{/each}
</nav>

<style>
	.nav {
		position: sticky; top: 48px; z-index: 9; background: var(--bg);
		display: flex; gap: 6px; overflow-x: auto; padding: 6px 0 10px; scrollbar-width: none;
	}
	.nav::-webkit-scrollbar { display: none; }
	.chip {
		flex: 0 0 auto; font-size: 0.78rem; color: var(--text-dim); text-decoration: none;
		background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px;
	}
	.chip:active { color: var(--text); border-color: var(--accent); }
</style>
