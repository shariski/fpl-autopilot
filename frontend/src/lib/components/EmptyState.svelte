<script lang="ts">
	let { message }: { message: string } = $props();
</script>

<p class="empty">{message}</p>

<style>
	.empty {
		color: var(--text-dim);
		font-size: 0.9rem;
		background: var(--surface);
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		padding: 16px;
		margin: 0;
	}
</style>
