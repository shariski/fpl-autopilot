<script lang="ts">
	import type { Activity } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { activity }: { activity: Activity } = $props();
	const fmtTs = (iso: string) =>
		new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
</script>

{#if activity.entries.length === 0}
	<EmptyState message="No decisions logged yet." />
{:else}
	<ul class="log">
		{#each activity.entries as e (e.ts_utc + e.action_taken)}
			<li class="entry">
				<span class="type {e.decision_type}">{e.decision_type}</span>
				<span class="action">{e.action_taken}</span>
				<span class="ts tnum">{fmtTs(e.ts_utc)}</span>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.log { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
	.entry { display: flex; align-items: center; gap: 8px; font-size: 0.8rem;
		background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
	.type { text-transform: uppercase; font-size: 0.62rem; letter-spacing: 0.05em; color: var(--accent-2);
		border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }
	.action { flex: 1; min-width: 0; }
	.ts { color: var(--text-dim); font-size: 0.7rem; }
</style>
