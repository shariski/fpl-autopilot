<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const gw = $derived(data.gw);
	const audit = $derived(data.audit);

	function formatResidual(r: number): string {
		const sign = r >= 0 ? '+' : '';
		return `${sign}${r.toFixed(1)}`;
	}

	function residualClass(r: number): string {
		if (r >= 2) return 'positive';
		if (r <= -2) return 'negative';
		return 'neutral';
	}

	function dismissProposal(parameter: string) {
		// S-G: no-op. Logging the click is left for S-H when proposals can actually be applied.
		console.info(`[audit] dismissed proposal: ${parameter}`);
	}

	function considerProposal(parameter: string) {
		console.info(`[audit] noted proposal for consideration: ${parameter}`);
	}
</script>

<svelte:head>
	<title>Audit — FPL Autopilot</title>
</svelte:head>

<main class="audit-page">
	<header>
		<a href="/" class="back">← Dashboard</a>
		<h1>Decision Audit</h1>
	</header>

	{#if audit === null}
		<section class="empty">
			<h2>No audit available for GW{gw}</h2>
			<p>
				Run <code>fpl-autopilot review</code> from the command line to generate one. The audit
				compares each past decision against actual outcomes and surfaces systematic biases.
			</p>
		</section>
	{:else}
		<section class="meta" data-testid="audit-meta">
			<dl>
				<dt>Window</dt>
				<dd>GW{audit.gw_range[0]}–GW{audit.gw_range[1]}</dd>
				<dt>Model</dt>
				<dd>{audit.model_version}</dd>
				<dt>Generated</dt>
				<dd>{new Date(audit.generated_at).toLocaleString()}</dd>
				<dt>Residuals analysed</dt>
				<dd>{audit.residuals.length}</dd>
			</dl>
		</section>

		<section class="clusters" data-testid="audit-clusters">
			<h2>Clusters</h2>
			{#if Object.keys(audit.cluster_counts).length === 0}
				<p class="muted">No residuals classified.</p>
			{:else}
				<ul>
					{#each Object.entries(audit.cluster_counts).sort((a, b) => b[1] - a[1]) as [name, count]}
						<li><span class="name">{name}</span><span class="count">{count}</span></li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class="trends" data-testid="audit-trends">
			<h2>Trends</h2>
			{#if Object.keys(audit.aggregate_trends).length === 0}
				<p class="muted">Not enough data for aggregate trends.</p>
			{:else}
				<table>
					<thead><tr><th>Decision</th><th>N</th><th>Mean</th><th>95% CI</th></tr></thead>
					<tbody>
						{#each Object.entries(audit.aggregate_trends) as [dtype, stat]}
							<tr>
								<td>{dtype}</td>
								<td>{stat.n}</td>
								<td class={residualClass(stat.mean_residual)}>{formatResidual(stat.mean_residual)}</td>
								<td>[{formatResidual(stat.ci_95[0])}, {formatResidual(stat.ci_95[1])}]</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="residuals" data-testid="audit-residuals">
			<h2>Top residuals</h2>
			{#if audit.residuals.length === 0}
				<p class="muted">No residuals to report.</p>
			{:else}
				<table>
					<thead><tr><th>GW</th><th>Type</th><th>Subject</th><th>Expected</th><th>Actual</th><th>Residual</th></tr></thead>
					<tbody>
						{#each audit.residuals.slice().sort((a, b) => Math.abs(b.residual) - Math.abs(a.residual)).slice(0, 10) as r}
							<tr>
								<td>{r.gw}</td>
								<td>{r.decision_type}</td>
								<td>{r.inputs_summary?.web_name || r.inputs_summary?.captain_web_name || '—'}</td>
								<td>{r.expected_points.toFixed(1)}</td>
								<td>{r.actual_points.toFixed(1)}</td>
								<td class={residualClass(r.residual)}>{formatResidual(r.residual)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		{#if audit.narrative}
			<section class="narrative" data-testid="audit-narrative">
				<h2>Narrative</h2>
				<p class="provider">via {audit.narrative_provider}</p>
				<div class="prose">{audit.narrative}</div>
			</section>
		{/if}

		<section class="proposals" data-testid="audit-proposals">
			<h2>Proposed adjustments (advisory)</h2>
			{#if audit.proposals.length === 0}
				<p class="muted">No threshold adjustments proposed.</p>
			{:else}
				<ul>
					{#each audit.proposals as p}
						<li class="proposal">
							<div class="header">
								<code>{p.parameter}</code>
								<span class="change">{p.current_value} → {p.proposed_value}</span>
								<span class="confidence" data-confidence={p.confidence}>{p.confidence}</span>
								<span class="n">N={p.n_observations}</span>
							</div>
							<p class="justification">{p.justification}</p>
							<div class="actions">
								<button type="button" onclick={() => considerProposal(p.parameter)}>I'll consider</button>
								<button type="button" onclick={() => dismissProposal(p.parameter)}>Dismiss</button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/if}
</main>

<style>
	.audit-page { max-width: 960px; margin: 0 auto; padding: 1.5rem; }
	header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 1.5rem; }
	.back { font-size: 0.9rem; color: #666; text-decoration: none; }
	.back:hover { text-decoration: underline; }
	h1 { margin: 0; }
	h2 { margin: 1.5rem 0 0.5rem; font-size: 1.1rem; }
	section { margin-bottom: 1.25rem; }
	.empty { padding: 2rem; background: #f7f7f7; border-radius: 6px; text-align: center; }
	.empty code { padding: 2px 6px; background: #fff; border-radius: 3px; }
	.muted { color: #888; font-style: italic; }
	dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 1rem; }
	dt { color: #666; }
	table { width: 100%; border-collapse: collapse; }
	th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #eee; }
	th { font-weight: 600; color: #555; }
	.clusters ul { list-style: none; padding: 0; }
	.clusters li { display: flex; justify-content: space-between; padding: 4px 0; }
	.clusters .name { color: #444; }
	.clusters .count { font-variant-numeric: tabular-nums; color: #888; }
	.positive { color: #0a7d3f; }
	.negative { color: #b00020; }
	.neutral { color: #555; }
	.narrative .provider { font-size: 0.85rem; color: #888; margin: 0 0 0.5rem; }
	.narrative .prose { white-space: pre-wrap; line-height: 1.55; }
	.proposal { padding: 12px; background: #fafafa; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 8px; list-style: none; }
	.proposal .header { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
	.proposal .change { font-weight: 600; }
	.proposal .confidence { font-size: 0.75rem; padding: 2px 8px; border-radius: 10px; background: #eee; text-transform: uppercase; }
	.proposal .confidence[data-confidence='high'] { background: #c8e6c9; color: #1b5e20; }
	.proposal .confidence[data-confidence='medium'] { background: #fff9c4; color: #5d4037; }
	.proposal .confidence[data-confidence='low'] { background: #f0f0f0; color: #555; }
	.proposal .n { font-size: 0.85rem; color: #888; }
	.proposal .justification { margin: 8px 0; color: #444; }
	.proposal .actions { display: flex; gap: 8px; }
	.proposal button { padding: 4px 12px; font-size: 0.85rem; cursor: pointer; }
</style>
