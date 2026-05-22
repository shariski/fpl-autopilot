<script lang="ts">
	import type { PageData } from './$types';
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
</script>

<Header status={d.status} />
<SectionNav {hasChip} />

<Section id="team" title="My Team"><Pitch squad={d.squad} /></Section>
<Section id="captain" title="Captain Pick"><CaptainPicks captain={d.captain} /></Section>
<Section id="transfers" title="Transfer Ideas"><TransferIdeas transfers={d.transfers} /></Section>
{#if hasChip}
	<Section id="chip" title="Chip Recommendation"><ChipRecommendation chips={d.chips} /></Section>
{/if}
<Section id="fixtures" title="Fixture Planner"><FixturePlanner planner={d.planner} /></Section>
<Section id="log" title="Activity Log"><ActivityLog activity={d.activity} /></Section>
