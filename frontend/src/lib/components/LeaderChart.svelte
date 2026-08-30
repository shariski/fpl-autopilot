<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import * as echarts from 'echarts/core';
	import { BarChart, HeatmapChart, LineChart } from 'echarts/charts';
	import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
	import { CanvasRenderer } from 'echarts/renderers';

	echarts.use([BarChart, HeatmapChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

	let { option, height = '240px' }: { option: Record<string, unknown>; height?: string } = $props();
	let el: HTMLDivElement | undefined = $state();
	let chart: echarts.ECharts | undefined;

	onMount(() => {
		if (!el) return;
		chart = echarts.init(el, 'dark');
		chart.setOption(option);
	});

	$effect(() => {
		chart?.setOption(option);
	});

	onDestroy(() => {
		chart?.dispose();
	});
</script>

<div bind:this={el} style="width:100%; height:{height}"></div>
