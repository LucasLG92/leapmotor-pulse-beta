<script lang="ts">
	import { dateTime, euros, number } from './format';
	import type { ChargeQuote } from './types';

	let {
		quote,
		sessionStartedAt,
		sessionEndedAt,
		sessionKwh,
		summary = 'Precio y supuestos',
		uniformVariant = 'current'
	}: {
		quote: ChargeQuote;
		sessionStartedAt: string;
		sessionEndedAt: string;
		sessionKwh: number;
		summary?: string;
		uniformVariant?: 'current' | 'history';
	} = $props();

	const pageSize = 20;
	let open = $state(false);
	let page = $state(0);
	let previousQuoteKey = '';
	let distribution = $derived(quote.pricing.accuracy === 'estimated_intervals' ? quote.pricing.energy_distribution : null);
	let isTemporal = $derived(distribution !== null);
	let energyIntervals = $derived(distribution?.intervals ?? []);
	let priceIntervals = $derived(quote.pricing.intervals);
	let detailCount = $derived(priceIntervals.length);
	let pageCount = $derived(Math.max(1, Math.ceil(detailCount / pageSize)));
	let visiblePriceIntervals = $derived(open ? priceIntervals.slice(page * pageSize, (page + 1) * pageSize) : []);
	let key = $derived(JSON.stringify({
		cost_eur: quote.cost_eur,
		tariff_eur_kwh: quote.tariff_eur_kwh,
		pricing: {
			method: quote.pricing.method,
			accuracy: quote.pricing.accuracy,
			region: quote.pricing.region,
			source: quote.pricing.source,
			taxes_included: quote.pricing.taxes_included,
			calculated_at: quote.pricing.calculated_at,
			intervals: priceIntervals.map((interval) => [interval.started_at, interval.ended_at, interval.kwh, interval.price_eur_kwh, interval.cost_eur, interval.price_fetched_at])
		},
		distribution: distribution ? {
			version: distribution.version,
			source_id: distribution.source_id,
			source_version: distribution.source_version,
			basis: distribution.basis,
			intervals: distribution.intervals.map((interval) => [interval.started_at, interval.ended_at, interval.kwh])
		} : null
	}));
	let resolution = $derived.by(() => {
		if (!open || !isTemporal || energyIntervals.length === 0) return '';
		const durations = energyIntervals.map((interval) => (Date.parse(interval.ended_at) - Date.parse(interval.started_at)) / 60_000);
		if (durations.some((duration) => !Number.isFinite(duration) || duration <= 0)) return 'no disponible';
		const minimum = Math.min(...durations);
		const maximum = Math.max(...durations);
		return Math.abs(maximum - minimum) < 1 / 60
			? `${number(minimum)} min por intervalo`
			: `variable, de ${number(minimum)} a ${number(maximum)} min`;
	});

	$effect(() => {
		const currentKey = key;
		if (currentKey !== previousQuoteKey) {
			previousQuoteKey = currentKey;
			page = 0;
			open = false;
		}
	});

	$effect(() => {
		if (!open) page = 0;
	});

	function movePage(nextPage: number) {
		page = Math.max(0, Math.min(nextPage, pageCount - 1));
	}
</script>

<details class="charger-details session-cost-details" bind:open>
	<summary>{summary}</summary>
	{#if distribution}
		<p>Estimación por intervalos vinculada a esta sesión ({dateTime(sessionStartedAt)} → {dateTime(sessionEndedAt)}).</p>
		<p>Cobertura completa validada por Pulse: {number(energyIntervals.length)} intervalos. Energía total de la sesión: {number(sessionKwh)} kWh.</p>
		<p>Fuente de energía: {distribution.source_id} · versión {distribution.source_version}. Resolución real: {resolution}.</p>
		<p>Fuente del precio: {quote.pricing.source} · {quote.pricing.region === 'peninsula' ? 'Península' : 'zona indicada por el servidor'}; precio medio {quote.tariff_eur_kwh === null ? '—' : `${euros(quote.tariff_eur_kwh)} / kWh`}.</p>
		<p>Se estima una potencia uniforme dentro de cada intervalo. Valora energía total del cargador; no acredita gasto de red ni autoconsumo. No incluye impuestos ni potencia fija y no es una factura exacta.</p>
		<p>{number(priceIntervals.length)} intervalos de precio en este desglose.</p>
		{#if priceIntervals.length > 0}
			{#if open}
				<ul class="pricing-intervals" aria-label="Intervalos de precio PVPC">
					{#each visiblePriceIntervals as interval, intervalIndex (`temporal-${quote.pricing.calculated_at}-${page}-${intervalIndex}`)}
						<li><span><strong>Tramo {page * pageSize + intervalIndex + 1}</strong><small>{dateTime(interval.started_at)} → {dateTime(interval.ended_at)}</small><small>{number(interval.kwh)} kWh · {euros(interval.price_eur_kwh)} / kWh · Precio consultado: {dateTime(interval.price_fetched_at)}</small></span><b>{euros(interval.cost_eur)}</b></li>
					{/each}
				</ul>
				{#if priceIntervals.length > pageSize}
					{@render intervalPager(priceIntervals.length)}
				{/if}
			{/if}
		{:else}
			<p>No hay intervalos de precio disponibles para mostrar.</p>
		{/if}
	{:else}
		<p>Precio medio: {quote.tariff_eur_kwh === null ? '—' : `${euros(quote.tariff_eur_kwh)} / kWh`} · {quote.pricing.region === 'peninsula' ? 'Península' : 'zona indicada por el servidor'}.</p>
		{#if uniformVariant === 'current'}
			<p>Estimación uniforme de {number(sessionKwh)} kWh a lo largo de la sesión comunicada. Supone energía de red; no es una medición horaria y no incluye impuestos, potencia fija ni autoconsumo.</p>
		{:else}
			<p>Estimación uniforme de {number(sessionKwh)} kWh entre {dateTime(sessionStartedAt)} y {dateTime(sessionEndedAt)}. Supone energía de red; no representa una curva medida y no incluye impuestos, potencia fija ni autoconsumo.</p>
		{/if}
		<p>Fuente del precio: {quote.pricing.source}.</p>
		<p>{number(priceIntervals.length)} intervalos de precio en este desglose.</p>
		{#if open}
			<ul class="pricing-intervals">
				{#each visiblePriceIntervals as interval, intervalIndex (`uniform-${quote.pricing.calculated_at}-${page}-${intervalIndex}`)}
					<li><span><strong>Tramo {page * pageSize + intervalIndex + 1}</strong><small>{dateTime(interval.started_at)} → {dateTime(interval.ended_at)}</small><small>{number(interval.kwh)} kWh · {euros(interval.price_eur_kwh)} / kWh · Precio consultado: {dateTime(interval.price_fetched_at)}</small></span><b>{euros(interval.cost_eur)}</b></li>
				{/each}
			</ul>
			{#if priceIntervals.length > pageSize}
				{@render intervalPager(priceIntervals.length)}
			{/if}
		{/if}
	{/if}
</details>

{#snippet intervalPager(total: number)}
	<nav class="pricing-interval-pagination" aria-label="Paginación de intervalos de precio PVPC">
		<span>Página {page + 1} de {pageCount} · {number(page * pageSize + 1)}–{number(Math.min((page + 1) * pageSize, total))} de {number(total)} intervalos</span>
		<div>
			<button class="button button-secondary" type="button" aria-label="Intervalos anteriores" disabled={page === 0} onclick={() => movePage(page - 1)}>Anterior</button>
			<button class="button button-secondary" type="button" aria-label="Intervalos siguientes" disabled={page + 1 >= pageCount} onclick={() => movePage(page + 1)}>Siguiente</button>
		</div>
	</nav>
{/snippet}

<style>
	.session-cost-details { min-width: 0; margin-top: 2px; padding: 0 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
	.session-cost-details summary { display: flex; min-height: 44px; align-items: center; color: var(--forest); cursor: pointer; font-size: .875rem; font-weight: 650; overflow-wrap: anywhere; }
	.session-cost-details summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 6px; }
	.session-cost-details > p { margin: 0 0 10px; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	.pricing-intervals { display: grid; min-width: 0; gap: 5px; max-height: none; margin: 0 0 10px; padding: 0; list-style: none; }
	.pricing-intervals li { display: flex; min-width: 0; align-items: flex-start; justify-content: space-between; gap: 8px; padding: 7px 0; border-top: 1px solid var(--line); }
	.pricing-intervals li > span { display: grid; min-width: 0; gap: 3px; }
	.pricing-intervals strong, .pricing-intervals b { color: var(--ink); font-size: .875rem; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.pricing-intervals b { flex-shrink: 0; white-space: nowrap; }
	.pricing-intervals small { color: var(--muted); font-size: .75rem; line-height: 1.4; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.pricing-interval-pagination { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; padding: 8px 0; color: var(--muted); font-size: .875rem; line-height: 1.45; }
	.pricing-interval-pagination > div { display: flex; flex-wrap: wrap; gap: 8px; }
	.pricing-interval-pagination .button { min-width: 100px; min-height: 44px; }
	@media (min-width: 420px) { .pricing-interval-pagination { grid-template-columns: minmax(0, 1fr) auto; align-items: center; } }
</style>
