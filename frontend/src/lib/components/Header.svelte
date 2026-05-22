<script lang="ts">
	import type { Status } from '$lib/types';
	import Countdown from './Countdown.svelte';
	let { status }: { status: Status } = $props();
</script>

<header class="hdr">
	<div class="row">
		<strong>GW{status.current_gw}</strong>
		<span class="dot {status.mode}"></span>
		<span class="mode">{status.mode}</span>
		<span class="cd"><Countdown deadlineUtc={status.deadline_utc} /></span>
	</div>
	{#if status.banners.length}
		<ul class="banners">
			{#each status.banners as b}
				<li class="banner {b.level}">{b.text}</li>
			{/each}
		</ul>
	{/if}
</header>

<style>
	.hdr { position: sticky; top: 0; z-index: 10; background: var(--bg); padding: 12px 0 8px; }
	.row { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; }
	.mode { color: var(--text-dim); text-transform: capitalize; font-size: 0.85rem; }
	.cd { margin-left: auto; color: var(--accent); }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
	.dot.frozen { background: var(--text-dim); }
	.dot.deadguard { background: var(--warning); }
	.banners { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
	.banner { font-size: 0.8rem; padding: 8px 10px; border-radius: 8px; }
	.banner.warning { background: rgba(255, 180, 84, 0.12); color: var(--warning); }
	.banner.error { background: rgba(255, 93, 93, 0.12); color: var(--danger); }
	.banner.info { background: var(--surface); color: var(--text-dim); }
</style>
