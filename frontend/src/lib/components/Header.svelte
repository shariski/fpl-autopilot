<script lang="ts">
	import type { Status } from '$lib/types';
	import Countdown from './Countdown.svelte';
	let { status, onaction, onmode }: {
		status: Status;
		onaction?: (endpoint: string) => void;
		onmode?: (mode: string) => Promise<boolean>;
	} = $props();
	let feedback = $state<string | null>(null);
	let feedbackTimer: ReturnType<typeof setTimeout> | undefined;

	async function changeMode(m: string) {
		if (m === status.mode) return;
		feedback = null;
		const ok = await onmode?.(m);
		feedback = ok ? `Mode set to ${m}` : 'Mode switch failed';
		clearTimeout(feedbackTimer);
		feedbackTimer = setTimeout(() => (feedback = null), 4000);
	}
</script>

<header class="hdr">
	<div class="row">
		<strong>GW{status.current_gw}</strong>
		<span class="dot {status.frozen ? 'frozen' : status.mode}"></span>
		<select
			class="mode-select"
			value={status.frozen ? 'frozen' : status.mode}
			disabled={status.frozen}
			onchange={(e) => changeMode((e.target as HTMLSelectElement).value)}
			title={status.frozen ? 'Unfreeze before changing mode' : 'Operating mode (manual | hybrid | auto)'}
		>
			<option value="manual">manual</option>
			<option value="hybrid">hybrid</option>
			<option value="auto">auto</option>
		</select>
		{#if feedback}
			<span class="feedback" class:bad={feedback.includes('failed')}>{feedback}</span>
		{/if}
		<button class="toggle" onclick={() => onaction?.(status.frozen ? '/api/unfreeze' : '/api/freeze')}>
			{status.frozen ? 'Unfreeze' : 'Freeze'}
		</button>
		<span class="cd"><Countdown deadlineUtc={status.deadline_utc} /></span>
	</div>
	{#if status.banners.length}
		<ul class="banners">
			{#each status.banners as b}
				<li class="banner {b.level}">
					<span>{b.text}</span>
					{#if b.action}
						<button class="action" onclick={() => onaction?.(b.action!.endpoint)}>{b.action.label}</button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</header>

<style>
	.hdr { position: sticky; top: 0; z-index: 10; background: var(--bg); padding: 12px 0 8px; }
	.row { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; }
	.mode { color: var(--text-dim); text-transform: capitalize; font-size: 0.85rem; }
	.mode-select {
		font-size: 0.8rem;
		padding: 2px 6px;
		border-radius: 6px;
		border: 1px solid var(--text-dim);
		background: var(--surface);
		color: var(--text);
		cursor: pointer;
		text-transform: capitalize;
	}
	.feedback {
		font-size: 0.75rem;
		color: var(--accent);
	}
	.feedback.bad {
		color: var(--danger);
	}
	.toggle { font-size: 0.75rem; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--text-dim);
		background: var(--surface); color: var(--text); cursor: pointer; }
	.cd { margin-left: auto; color: var(--accent); }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
	.dot.frozen { background: var(--text-dim); }
	.dot.deadguard { background: var(--warning); }
	.banners { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
	.banner { font-size: 0.8rem; padding: 8px 10px; border-radius: 8px; display: flex; align-items: center; gap: 8px; }
	.banner .action { margin-left: auto; font-size: 0.75rem; padding: 3px 8px; border-radius: 6px;
		border: 1px solid currentColor; background: transparent; color: inherit; cursor: pointer; }
	.banner.warning { background: rgba(255, 180, 84, 0.12); color: var(--warning); }
	.banner.error { background: rgba(255, 93, 93, 0.12); color: var(--danger); }
	.banner.info { background: var(--surface); color: var(--text-dim); }
</style>
