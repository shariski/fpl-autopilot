<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchLeaders } from '$lib/api/client';
	import type { LeadersPayload } from '$lib/types';
	import LeaderChart from '$lib/components/LeaderChart.svelte';

	let payload = $state<LeadersPayload | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			payload = await fetchLeaders();
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	const CHIP_LABELS: Record<string, string> = {
		wildcard: 'WC', free_hit: 'FH', bench_boost: 'BB', '3xc': 'TC'
	};

	const chipLabel = (c: string) => CHIP_LABELS[c] ?? c;

	function chipHeatOption() {
		const rows = payload?.patterns.chip_timing.rows ?? [];
		const chips = [...new Set(rows.map((r) => r.chip))];
		const gws = [...new Set(rows.map((r) => r.gw))].sort((a, b) => a - b);
		return {
			tooltip: { position: 'top' },
			grid: { left: 60, right: 20, top: 30, bottom: 30 },
			xAxis: { type: 'category', data: gws, name: 'GW' },
			yAxis: { type: 'category', data: chips.map(chipLabel), name: 'Chip' },
			visualMap: {
				min: 0, max: Math.max(1, ...rows.map((r) => r.count)),
				inRange: { color: ['#121821', '#00e6a8'] }
			},
			series: [{
				type: 'heatmap',
				data: rows.map((r) => [gws.indexOf(r.gw), chips.indexOf(r.chip), r.count]),
				label: { show: true }
			}]
		};
	}

	function transferOption() {
		const hist = payload?.patterns.transfers.histogram ?? [];
		return {
			tooltip: {},
			grid: { left: 50, right: 20, top: 20, bottom: 30 },
			xAxis: { type: 'category', data: hist.map((h) => String(h.transfers)) },
			yAxis: { type: 'value', name: 'leader-GWs' },
			series: [{ type: 'bar', data: hist.map((h) => h.count), itemStyle: { color: '#00e6a8' } }]
		};
	}

	function bankOption() {
		const bank = payload?.patterns.bank_value.bank ?? [];
		return {
			tooltip: { trigger: 'axis' },
			legend: { data: ['bank mean', 'bank median'] },
			grid: { left: 60, right: 20, top: 30, bottom: 30 },
			xAxis: { type: 'category', data: bank.map((b) => `GW${b.gw}`) },
			yAxis: { type: 'value', name: 'bank (£m)' },
			series: [
				{ name: 'bank mean', type: 'line', data: bank.map((b) => b.mean), smooth: true },
				{ name: 'bank median', type: 'line', data: bank.map((b) => b.median), smooth: true }
			]
		};
	}

	function progressionOption() {
		const series = payload?.patterns.progression.series ?? [];
		if (series.length === 0) return {};
		const gws = [...new Set(series.flatMap((s) => s.points.map((p) => p.gw)))].sort((a, b) => a - b);
		return {
			tooltip: { trigger: 'axis' },
			legend: { type: 'scroll', data: series.map((s) => s.player_name) },
			grid: { left: 70, right: 20, top: 30, bottom: 30 },
			xAxis: { type: 'category', data: gws.map((g) => `GW${g}`) },
			yAxis: { type: 'log', name: 'rank' },
			series: series.map((s) => ({
				name: s.player_name, type: 'line', smooth: true,
				data: gws.map((g) => {
					const pt = s.points.find((p) => p.gw === g);
					return pt ? pt.rank : null;
				})
			}))
		};
	}

	function retentionOption() {
		const byGw = payload?.patterns.retention.by_gw ?? [];
		if (byGw.length === 0) return {};
		return {
			tooltip: { trigger: 'axis' },
			grid: { left: 50, right: 20, top: 20, bottom: 30 },
			xAxis: { type: 'category', data: byGw.map((b) => `GW${b.gw}`) },
			yAxis: { type: 'value', name: '% retained', max: 100 },
			series: [{
				name: 'GW1 cohort still top-100', type: 'line', smooth: true,
				data: byGw.map((b) => b.pct * 100), itemStyle: { color: '#00e6a8' },
				areaStyle: { color: 'rgba(0,230,168,0.15)' }
			}]
		};
	}

	function captaincyOption() {
		const rows = payload?.patterns.captaincy.rows ?? [];
		if (rows.length === 0) return {};
		return {
			tooltip: {},
			grid: { left: 100, right: 20, top: 20, bottom: 30 },
			xAxis: { type: 'value' },
			yAxis: { type: 'category', data: rows.slice(0, 8).map((r) => r.web_name) },
			series: [{ type: 'bar', data: rows.slice(0, 8).map((r) => r.count), itemStyle: { color: '#1f6feb' } }]
		};
	}

	function formationsOption() {
		const rows = payload?.patterns.formations.rows ?? [];
		if (rows.length === 0) return {};
		return {
			tooltip: {},
			grid: { left: 50, right: 20, top: 20, bottom: 30 },
			xAxis: { type: 'category', data: rows.map((r) => r.formation) },
			yAxis: { type: 'value' },
			series: [{ type: 'bar', data: rows.map((r) => r.count), itemStyle: { color: '#ffb454' } }]
		};
	}

	function hasSnapshots() { return (payload?.cohort.length ?? 0) > 0; }
</script>

<svelte:head><title>Leaders — FPL Autopilot</title></svelte:head>

<div class="leaders-page">
	<h1>Leaders</h1>
	<p class="muted lead-note">
		How the global top-100 play — chip timing, transfer discipline, bank management,
		rank momentum. Updated after each gameweek settles.
	</p>

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<div class="error-card">
			<p class="muted">Could not load leader data.</p>
			<button onclick={() => location.reload()}>Retry</button>
		</div>
	{:else if !hasSnapshots()}
		<div class="empty-card">
			<p class="muted">No snapshots yet — the first lands shortly after the next
				gameweek settles.</p>
		</div>
	{:else}
		<section class="card">
			<h2 class="section-label">Chip timing</h2>
			<LeaderChart option={chipHeatOption()} />
			<p class="muted small">How many of the top-100 played each chip in each GW.</p>
		</section>

		<section class="card">
			<h2 class="section-label">Transfer discipline</h2>
			<div class="stat-row">
				<span class="stat"><strong>{payload?.patterns.transfers.mean_per_gw}</strong> mean transfers/GW</span>
				<span class="stat"><strong>{payload?.patterns.transfers.median_per_gw}</strong> median</span>
				<span class="stat"><strong>{((payload?.patterns.transfers.hit_freq ?? 0) * 100).toFixed(0)}%</strong> GWs with a hit</span>
				<span class="stat"><strong>{payload?.patterns.transfers.mean_hit_cost}</strong> mean hit cost</span>
			</div>
			<LeaderChart option={transferOption()} />
		</section>

		<section class="card">
			<h2 class="section-label">Bank &amp; value</h2>
			<LeaderChart option={bankOption()} />
		</section>

		<section class="card">
			<h2 class="section-label">Rank momentum</h2>
			{#if payload?.patterns.momentum.sustained_elite.length}
				<p class="muted small">
					<strong>{payload.patterns.momentum.sustained_elite.length}</strong> of the cohort
					were also elite in 25-26 (sustained performers).
				</p>
			{/if}
			{#if payload?.patterns.momentum.top_movers.length}
				<table class="movers">
					<thead><tr><th>#</th><th>GWs</th><th>Rank gain</th></tr></thead>
					<tbody>
						{#each payload.patterns.momentum.top_movers.slice(0, 8) as m, i}
							<tr>
								<td>{i + 1}</td>
								<td>GW{m.from_gw} → GW{m.to_gw}</td>
								<td class="tnum gain">+{m.rank_gain}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="card">
			<h2 class="section-label">Progression</h2>
			{#if payload?.patterns.progression.series.length}
				<LeaderChart option={progressionOption()} height="300px" />
				<p class="muted small">Rank over GWs (log scale) — top-5 by current rank + sustained elite.</p>
			{:else}
				<p class="muted small">Rank series appear after the second GW settles.</p>
			{/if}
		</section>

		<section class="card">
			<h2 class="section-label">Retention</h2>
			{#if payload?.patterns.retention.by_gw.length}
				<LeaderChart option={retentionOption()} height="220px" />
				<p class="muted small">Of the GW1 top-100, how many are still top-100.</p>
			{:else}
				<p class="muted small">Needs at least two settled GWs.</p>
			{/if}
		</section>

		<section class="card">
			<h2 class="section-label">Most-owned</h2>
			{#if payload?.patterns.ownership.rows.length}
				<div class="table-wrap">
					<table class="cohort">
						<thead>
							<tr><th>Player</th><th>Team</th><th>Owned by</th><th>%</th><th></th></tr>
						</thead>
						<tbody>
							{#each payload.patterns.ownership.rows.slice(0, 20) as o}
								<tr>
									<td>{o.web_name}</td>
									<td class="muted">{o.team_short}</td>
									<td class="tnum">{o.count}/{payload.patterns.ownership.cohort}</td>
									<td class="tnum">{(o.pct * 100).toFixed(0)}%</td>
									<td>{#if o.differential}<span class="chip diff">differential</span>{/if}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="muted small">Squads arrive with the next snapshot.</p>
			{/if}
		</section>

		<section class="card">
			<h2 class="section-label">Captaincy &amp; formations</h2>
			{#if payload?.patterns.captaincy.rows.length}
				<LeaderChart option={captaincyOption()} height="220px" />
			{/if}
			{#if payload?.patterns.formations.rows.length}
				<LeaderChart option={formationsOption()} height="180px" />
			{/if}
			{#if !payload?.patterns.captaincy.rows.length && !payload?.patterns.formations.rows.length}
				<p class="muted small">Squads arrive with the next snapshot.</p>
			{/if}
		</section>

		<section class="card">
			<h2 class="section-label">Cohort</h2>
			<div class="table-wrap">
				<table class="cohort">
					<thead>
						<tr><th>Rank</th><th>Manager</th><th>Team</th><th>Total</th><th>Last GW</th>
							<th>Bank</th><th>Value</th><th>Chips</th><th>Past</th></tr>
					</thead>
					<tbody>
						{#each payload?.cohort ?? [] as c}
							<tr>
								<td class="tnum">#{c.rank}</td>
								<td>{c.player_name}</td>
								<td class="muted">{c.entry_name}</td>
								<td class="tnum">{c.total}</td>
								<td class="tnum">{c.last_gw_points ?? '—'}</td>
								<td class="tnum">{c.bank == null ? '—' : (c.bank / 10).toFixed(1)}</td>
								<td class="tnum">{c.value == null ? '—' : (c.value / 10).toFixed(1)}</td>
								<td>{c.chips_used.map(chipLabel).join(' ') || '—'}</td>
								<td>{c.past_rank ? (c.past_rank <= 250000 ? 'elite' : '—') : '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
</div>

<style>
	.leaders-page {
		padding: 1.25rem 0 2rem;
	}
	h1 {
		font-size: 1.5rem;
		margin: 0 0 0.4rem;
	}
	.lead-note {
		font-size: 0.85rem;
		line-height: 1.5;
		margin: 0 0 1.25rem;
	}
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1rem;
		margin-bottom: 1rem;
	}
	.section-label {
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-dim);
		margin: 0 0 0.6rem;
	}
	.stat-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.25rem;
		margin-bottom: 0.6rem;
	}
	.stat {
		font-size: 0.8rem;
		color: var(--text-dim);
	}
	.stat strong {
		color: var(--text);
		font-size: 1rem;
	}
	.small {
		font-size: 0.75rem;
		margin: 0.5rem 0 0;
	}
	.error-card, .empty-card {
		background: var(--surface);
		border-left: 3px solid var(--danger);
		padding: 0.9rem 1rem;
		border-radius: 0 var(--radius) var(--radius) 0;
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	.empty-card {
		border-left-color: var(--accent);
	}
	.error-card .muted, .empty-card .muted {
		margin: 0;
		flex: 1;
	}
	button {
		font: inherit;
		cursor: pointer;
		border: none;
		border-radius: var(--radius);
		background: var(--accent);
		color: #04100b;
		padding: 0.45rem 1.1rem;
		font-weight: 600;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	th {
		text-align: left;
		font-size: 0.68rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-dim);
		padding: 0.35rem 0.5rem;
		border-bottom: 1px solid var(--border);
	}
	td {
		padding: 0.4rem 0.5rem;
		border-bottom: 1px dashed var(--border);
		white-space: nowrap;
	}
	tr:last-child td {
		border-bottom: none;
	}
	.tnum {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
	}
	.gain {
		color: var(--accent);
	}
	.chip.diff {
		background: color-mix(in srgb, var(--warning) 20%, var(--surface-2));
		color: var(--warning);
	}
	.movers {
		max-width: 420px;
	}
	.muted {
		color: var(--text-dim);
	}
</style>
