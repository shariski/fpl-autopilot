<script lang="ts">
	import type { Snippet } from 'svelte';
	let { id, title, children }: { id: string; title: string; children: Snippet } = $props();
</script>

<section {id} class="section">
	<header class="section-head"><h2>{title}</h2></header>
	{@render children()}
</section>

<style>
	.section { margin-top: 20px; scroll-margin-top: 96px; }
	.section-head { margin-bottom: 8px; }
</style>
