<script lang="ts">
	import { onMount } from 'svelte';
	import type { PageData } from './$types';
	import type { Status } from '$lib/types';
	import { fetchStatus, postAction } from '$lib/api/client';
	import Header from '$lib/components/Header.svelte';
	import SectionNav from '$lib/components/SectionNav.svelte';
	import Section from '$lib/components/Section.svelte';
	import Pitch from '$lib/components/Pitch.svelte';
	import CaptainPicks from '$lib/components/CaptainPicks.svelte';
	import TransferIdeas from '$lib/components/TransferIdeas.svelte';
	import ChipRecommendation from '$lib/components/ChipRecommendation.svelte';
	import FixturePlanner from '$lib/components/FixturePlanner.svelte';
	import ActivityLog from '$lib/components/ActivityLog.svelte';

	let { data }: { data: PageData } = $props();
	const d = $derived(data.dashboard);
	const hasChip = $derived(d.chips.recommendation !== null);

	let status = $state<Status>(data.dashboard.status);
	const live = $derived(data.source === 'live');

	async function handleAction(endpoint: string) {
		try {
			status = await postAction(endpoint);
		} catch (e) {
			console.warn('[dashboard] action failed', endpoint, e);
		}
	}

	async function refreshStatus() {
		try {
			status = await fetchStatus();
		} catch (e) {
			console.warn('[dashboard] status refresh failed', e);
		}
	}

	onMount(() => {
		if (!live) return; // mock mode: no polling
		const id = setInterval(refreshStatus, 30000);
		const onFocus = () => refreshStatus();
		window.addEventListener('focus', onFocus);
		return () => {
			clearInterval(id);
			window.removeEventListener('focus', onFocus);
		};
	});
</script>

<Header {status} onaction={handleAction} />
<SectionNav {hasChip} />

<Section id="team" title="My Team"><Pitch squad={d.squad} /></Section>
<Section id="captain" title="Captain Pick"><CaptainPicks captain={d.captain} /></Section>
<Section id="transfers" title="Transfer Ideas"><TransferIdeas transfers={d.transfers} /></Section>
{#if hasChip}
	<Section id="chip" title="Chip Recommendation"><ChipRecommendation chips={d.chips} /></Section>
{/if}
<Section id="fixtures" title="Fixture Planner"><FixturePlanner planner={d.planner} /></Section>
<Section id="log" title="Activity Log"><ActivityLog activity={d.activity} /></Section>
