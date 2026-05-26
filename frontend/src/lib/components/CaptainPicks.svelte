<script lang="ts">
	import type { Captain } from '$lib/types';
	import EmptyState from './EmptyState.svelte';
	let { captain }: { captain: Captain } = $props();
</script>

{#if captain.picks.length === 0}
	<EmptyState message="Captain ranker not yet available — arrives with the decision engine." />
{:else}
	<ol class="picks">
		{#each captain.picks as p, i (p.player_id)}
			<li class="pick">
				<span class="rank tnum">{i + 1}</span>
				<div class="body">
					<div class="line1"><strong>{p.web_name}</strong>
						<span class="fix">{p.fixture}</span>
						<span class="xp tnum">{p.xp.toFixed(1)}</span>
					</div>
					<p class="reason">
						{#if i === 0 && p.reasoning_source === 'ai'}
							<span class="badge badge-ai" aria-label="AI-generated reasoning">AI</span>
						{:else if i === 0 && p.reasoning_source === 'classic'}
							<span class="badge badge-classic" aria-label="Template-based reasoning">classic</span>
						{/if}
						{p.reasoning ?? p.reason}
					</p>
				</div>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.picks { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
	.pick { display: flex; gap: 10px; background: var(--surface); border: 1px solid var(--border);
		border-radius: var(--radius); padding: 10px; }
	.rank { color: var(--text-dim); font-size: 0.9rem; width: 1.2rem; }
	.body { flex: 1; min-width: 0; }
	.line1 { display: flex; align-items: baseline; gap: 8px; }
	.fix { color: var(--text-dim); font-size: 0.74rem; }
	.xp { margin-left: auto; color: var(--accent); }
	.reason { margin: 4px 0 0; font-size: 0.8rem; color: var(--text-dim); }
	.badge { font-size: 0.7em; padding: 0.1em 0.4em; border-radius: 0.3em; margin-left: 0.4em; }
	.badge-ai { background: #2563eb; color: white; }
	.badge-classic { background: #e5e7eb; color: #4b5563; }
</style>
