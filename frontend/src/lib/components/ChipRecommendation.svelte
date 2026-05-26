<script lang="ts">
	import type { Chips } from '$lib/types';
	let { chips }: { chips: Chips } = $props();
	const label: Record<string, string> = {
		wildcard: 'Wildcard',
		free_hit: 'Free Hit',
		bench_boost: 'Bench Boost',
		triple_captain: 'Triple Captain'
	};
	const rec = $derived(chips.recommendation);
</script>

{#if rec}
	<div class="chip-rec">
		<div class="badge">{label[rec.chip] ?? rec.chip}</div>
		{#if rec.reasoning && rec.reasoning_source === 'ai'}
			<p class="reason">{rec.reasoning} <span class="ai-tag" aria-label="AI-generated reasoning">AI</span></p>
		{:else}
			<p class="reason">{rec.reasoning || rec.reason}</p>
		{/if}
	</div>
{/if}

<style>
	.chip-rec { background: linear-gradient(180deg, rgba(0,230,168,0.10), var(--surface));
		border: 1px solid var(--accent); border-radius: var(--radius); padding: 12px; }
	.badge { display: inline-block; font-weight: 700; color: #00261c; background: var(--accent);
		border-radius: 6px; padding: 2px 8px; font-size: 0.82rem; }
	.reason { margin: 8px 0 0; font-size: 0.85rem; color: var(--text); }
	.ai-tag { font-size: 0.7em; padding: 0.1em 0.4em; border-radius: 0.3em; margin-left: 0.4em;
		background: #2563eb; color: white; }
</style>
