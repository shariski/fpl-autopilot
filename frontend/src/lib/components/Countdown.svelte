<script lang="ts">
	import { countdown } from '$lib/format';
	let { deadlineUtc }: { deadlineUtc: string } = $props();
	let now = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 30000);
		return () => clearInterval(t);
	});
	let label = $derived(countdown(deadlineUtc, now));
</script>

<span class="tnum">{label}</span>
