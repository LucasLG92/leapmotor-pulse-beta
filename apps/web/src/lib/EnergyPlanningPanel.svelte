<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { ApiError, getChargingComparison, simulateChargingPlan } from './api';
	import type { ChargingComparison, ChargingPlan, ChargingPlanInput, PageProps } from './types';

	let { busy, online, revision, drafts, confirmAction, onSessionExpired }:
		Pick<PageProps, 'busy' | 'online' | 'revision' | 'drafts' | 'confirmAction' | 'onSessionExpired'> = $props();
	type ComparisonMonth = ChargingComparison['months'][number];
	type TariffForm = { start: string; end: string; price: string };
	type PlanForm = { energy: string; power: string; efficiency: string; earliest: string; deadline: string; tariffs: TariffForm[] };
	const newPlanForm = (): PlanForm => ({ energy: '', power: '', efficiency: '', earliest: '', deadline: '', tariffs: [{ start: '', end: '', price: '' }] });
	const formatNumber = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2 });
	const euro = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 });
	const kwhFormat = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2 });
	const utcDate = new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' });
	const monthDate = new Intl.DateTimeFormat('es-ES', { month: 'short', timeZone: 'UTC' });
	const MAX_TARIFFS = 336;
	const utcIso = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?Z$/;

	let comparison = $state<ChargingComparison | null>(null);
	let days = $state<30 | 90 | 365>(90), comparisonLoading = $state(false), comparisonError = $state('');
	let planForm = $state<PlanForm>(newPlanForm()), planBaseline = '';
	let planResult = $state<ChargingPlan | null>(null), planLoading = $state(false), planError = $state('');
	let resultFingerprint = $state('');
	let simulatorOpen = $state(false);
	let fieldErrors = $state<Record<string, string>>({}), submitted = $state(false), submittedAttempt = '';
	let mounted = false, comparisonGeneration = 0, observedKey = '', planGeneration = 0;
	let comparisonController: AbortController | null = null;

	function formFingerprint(): string { return JSON.stringify(planForm); }
	function dirty(): boolean { return submitted || formFingerprint() !== planBaseline; }
	function explicitUtc(raw: string): { epoch: number; value: string } | null {
		const match = utcIso.exec(raw.trim());
		if (!match) return null;
		const [, year, month, day, hour, minute, second = '0'] = match;
		const fields = [Number(year), Number(month), Number(day), Number(hour), Number(minute), Number(second)];
		const [y, m, d, h, min, s] = fields;
		if (m < 1 || m > 12 || h > 23 || min > 59 || s > 59) return null;
		const date = new Date(Date.UTC(y, m - 1, d, h, min, s));
		if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d || date.getUTCHours() !== h || date.getUTCMinutes() !== min || date.getUTCSeconds() !== s) return null;
		return { epoch: date.getTime(), value: raw.trim() };
	}
	function decimal(raw: string, min: number, max: number, allowZero = true): number | null {
		const value = raw.trim().replace(',', '.');
		const pattern = min < 0 ? /^-?(?:\d+(?:\.\d+)?|\.\d+)$/ : /^(?:\d+(?:\.\d+)?|\.\d+)$/;
		if (!pattern.test(value)) return null;
		const number = Number(value);
		return Number.isFinite(number) && number >= min && number <= max && (allowZero || number > 0) ? number : null;
	}
	function monthLabel(value: string): string {
		const [year, month] = value.split('-').map(Number);
		if (!year || !month || month < 1 || month > 12) return value;
		return `${monthDate.format(new Date(Date.UTC(year, month - 1, 1))).replace('.', '')} ${String(year).slice(-2)}`;
	}
	function amount(value: number | null, suffix: string): string { return value === null ? 'Sin dato' : `${formatNumber.format(value)} ${suffix}`; }
	function timeText(value: string): string {
		const date = new Date(value);
		return Number.isFinite(date.getTime()) ? `${utcDate.format(date)} UTC` : 'Hora no disponible';
	}

	onMount(() => {
		mounted = true;
		observedKey = `${revision}:${online}:${days}`;
		planBaseline = formFingerprint();
		void loadComparison();
		return drafts.register('energy-plan', dirty, () => submitted);
	});
	onDestroy(() => { mounted = false; comparisonGeneration++; planGeneration++; comparisonController?.abort(); });
	$effect(() => {
		const key = { revision, online, days };
		untrack(() => {
			if (!mounted) return;
			const next = `${key.revision}:${key.online}:${key.days}`;
			if (next === observedKey) return;
			observedKey = next;
			if (!key.online) {
				comparisonGeneration++; comparisonController?.abort(); comparison = null; comparisonLoading = false;
				comparisonError = 'Sin conexión. La comparación consulta únicamente cargas manuales guardadas en el servidor.';
			} else void loadComparison();
		});
	});

	async function loadComparison() {
		if (!mounted || !online) return;
		const request = ++comparisonGeneration, currentDays = days, currentRevision = revision;
		comparisonController?.abort(); comparisonController = new AbortController();
		const signal = comparisonController.signal;
		comparisonLoading = true; comparisonError = '';
		try {
			const value = await getChargingComparison(currentDays, signal);
			if (!mounted || request !== comparisonGeneration || signal.aborted || currentRevision !== revision || !online) return;
			comparison = value;
		} catch (error) {
			if (!mounted || request !== comparisonGeneration || signal.aborted) return;
			if (error instanceof ApiError && error.status === 401) { onSessionExpired(); return; }
			comparisonError = error instanceof ApiError ? error.message : 'No se pudo consultar la comparación de cargas.';
		} finally { if (mounted && request === comparisonGeneration) comparisonLoading = false; }
	}

	function addTariff() {
		if (busy || planLoading || planForm.tariffs.length >= MAX_TARIFFS) return;
		planForm.tariffs = [...planForm.tariffs, { start: '', end: '', price: '' }];
	}
	function removeTariff(index: number) {
		if (busy || planLoading || planForm.tariffs.length <= 1) return;
		planForm.tariffs = planForm.tariffs.filter((_, row) => row !== index);
		fieldErrors = {};
	}
	function resetSimulation() {
		planGeneration++; planForm = newPlanForm(); planResult = null; resultFingerprint = ''; planError = ''; fieldErrors = {};
		submitted = false; submittedAttempt = ''; planBaseline = formFingerprint();
	}

	function validatePlan(): ChargingPlanInput | null {
		const next: Record<string, string> = {};
		const energy = decimal(planForm.energy, 0, 300, false), power = decimal(planForm.power, 0, 350, false), efficiency = decimal(planForm.efficiency, 50, 100);
		if (energy === null) next.energy = 'Indica energía solicitada mayor que 0 y hasta 300 kWh.';
		if (power === null) next.power = 'Indica una potencia mayor que 0 y hasta 350 kW.';
		if (efficiency === null) next.efficiency = 'Indica una eficiencia entre 50 y 100 %.';
		const earliest = explicitUtc(planForm.earliest), deadline = explicitUtc(planForm.deadline);
		if (!earliest) next.earliest = 'Usa una fecha UTC válida, por ejemplo 2026-10-13T21:00:00Z.';
		if (!deadline) next.deadline = 'Usa una fecha UTC válida, por ejemplo 2026-10-14T07:00:00Z.';
		if (earliest && deadline && deadline.epoch <= earliest.epoch) next.deadline = 'El plazo debe ser posterior al inicio.';
		if (!planForm.tariffs.length) next.tariffs = 'Añade al menos un intervalo de precio manual.';
		const normalized: { start: string; end: string; price: number; epochStart: number; epochEnd: number }[] = [];
		planForm.tariffs.forEach((tariff, index) => {
			const start = explicitUtc(tariff.start), end = explicitUtc(tariff.end), price = decimal(tariff.price, -1, 5);
			if (!start) next[`tariff-${index}-start`] = 'Indica un inicio UTC válido terminado en Z.';
			if (!end) next[`tariff-${index}-end`] = 'Indica un fin UTC válido terminado en Z.';
			if (start && end && end.epoch <= start.epoch) next[`tariff-${index}-end`] = 'El fin debe ser posterior al inicio.';
			if (start && end && end.epoch - start.epoch > 3_600_000) next[`tariff-${index}-end`] = 'Cada intervalo manual debe durar como máximo una hora.';
			const priceValue = tariff.price.trim().replace(',', '.');
			if (price === null || !/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(priceValue)) next[`tariff-${index}-price`] = 'Indica un precio entre −1 y 5 €/kWh.';
			if (start && end && end.epoch > start.epoch && price !== null) normalized.push({ start: start.value, end: end.value, price, epochStart: start.epoch, epochEnd: end.epoch });
		});
		const ordered = [...normalized].sort((a, b) => a.epochStart - b.epochStart);
		for (let index = 1; index < ordered.length; index++) {
			if (ordered[index].epochStart < ordered[index - 1].epochEnd) {
				const originalIndex = planForm.tariffs.findIndex(row => explicitUtc(row.start)?.epoch === ordered[index].epochStart);
				next[`tariff-${originalIndex}-start`] = 'Este intervalo se solapa con otro. Ajusta los horarios para separarlos.';
			}
		}
		fieldErrors = next;
		if (Object.keys(next).length) return null;
		return { energy_kwh: energy!, power_kw: power!, efficiency_pct: efficiency!, earliest_at: earliest!.value,
			deadline_at: deadline!.value, tariffs: normalized.map(({ start, end, price }) => ({ start_at: start, end_at: end, price_eur_kwh: price })) };
	}

	async function calculate(event: SubmitEvent) {
		event.preventDefault();
		if (!mounted || busy || planLoading || !online) return;
		const payload = validatePlan();
		if (!payload) { requestAnimationFrame(() => { if (mounted) document.querySelector<HTMLInputElement>('.simulation-form [aria-invalid="true"]')?.focus(); }); return; }
		const attempt = JSON.stringify(payload);
		if (submitted && submittedAttempt !== attempt) {
			const accepted = await confirmAction('Abandonar el cálculo no confirmado', 'El resultado anterior no llegó a mostrarse. Puedes descartarlo y calcular con los nuevos datos; la simulación es informativa y no controla el coche.', 'Usar estos datos');
			if (!accepted || !mounted) return;
			submitted = false; submittedAttempt = '';
		} else if (submitted && submittedAttempt === attempt) {
			const accepted = await confirmAction('Repetir cálculo', 'La petición anterior no devolvió un resultado visible. Se repetirá solo después de tu confirmación.', 'Repetir cálculo');
			if (!accepted || !mounted) return;
		}
		const request = ++planGeneration;
		planLoading = true; planError = ''; planResult = null;
		try {
			submitted = true; submittedAttempt = attempt;
			const result = await simulateChargingPlan(payload);
			if (!mounted || request !== planGeneration) return;
			planResult = result; resultFingerprint = formFingerprint(); submitted = false; submittedAttempt = '';
			planBaseline = formFingerprint();
		} catch (error) {
			if (!mounted || request !== planGeneration) return;
			if (error instanceof ApiError && error.status === 401) { onSessionExpired(); return; }
			planError = error instanceof ApiError ? error.message : 'No se confirmó el cálculo. Conservamos los datos; revisa el mensaje antes de volver a calcular.';
		} finally { if (mounted && request === planGeneration) planLoading = false; }
	}

	function addHistoryText(item: ComparisonMonth): string {
		const details = [];
		if (item.missing_kwh_count) details.push(`${item.missing_kwh_count} sin kWh`);
		if (item.missing_cost_count) details.push(`${item.missing_cost_count} sin coste`);
		return details.length ? details.join(' · ') : 'Registros con datos completos';
	}
	function warningText(value: string): string {
		return ({ efficiency_is_user_estimate: 'La eficiencia es una estimación indicada por ti.', tariffs_are_user_supplied: 'Los precios proceden de intervalos que has introducido.', tariff_coverage_incomplete: 'Hay tramos del plazo sin precio: no se han rellenado.', requested_energy_unreachable: 'No hay suficiente cobertura o tiempo para alcanzar toda la energía solicitada.' } as Record<string, string>)[value] ?? 'El cálculo incluye una advertencia informativa.';
	}
	function durationText(plan: ChargingPlan): string {
		const totalSeconds = plan.intervals.reduce((total, interval) => total + interval.duration_seconds, 0);
		if (totalSeconds <= 0) return 'Sin tiempo programado';
		const totalMinutes = Math.ceil(totalSeconds / 60), hours = Math.floor(totalMinutes / 60), minutes = totalMinutes % 60;
		return hours ? `${hours} h${minutes ? ` ${minutes} min` : ''}` : `${minutes} min`;
	}
	let chartMax = $derived(Math.max(0, ...(comparison?.months ?? []).map(item => item.cost_eur ?? 0)));
	let chartWidth = $derived(Math.max(300, (comparison?.months.length ?? 0) * 58));
	let chartMonths = $derived((comparison?.months ?? []).map((month, index) => ({ month, index, bar: month.cost_eur === null ? 0 : chartMax > 0 ? Math.max(3, month.cost_eur / chartMax * 92) : 3 })));
	let monthCount = $derived(comparison?.months.length ?? 0);
	let comparisonTotal = $derived(comparison?.totals);
</script>

<section class="energy-planning" aria-labelledby="energy-planning-heading">
	<header class="planning-heading"><div><p class="eyebrow">Costes manuales y escenarios</p><h2 id="energy-planning-heading">Planificación de energía</h2><p>Compara lo que registraste y prueba un horario con precios que aportes tú.</p></div></header>

	<section class="comparison-section" aria-labelledby="comparison-heading">
		<div class="planning-section-heading"><div><p class="eyebrow">Histórico registrado</p><h3 id="comparison-heading">Cargas manuales por mes</h3><p>Sin estimar datos ausentes ni mezclar fuentes automáticas o de demostración.</p></div>
			<label class="range-select" for="comparison-days">Ventana<select id="comparison-days" bind:value={days} disabled={busy || comparisonLoading || !online}><option value={30}>Últimos 30 días</option><option value={90}>Últimos 90 días</option><option value={365}>Últimos 365 días</option></select></label>
		</div>
		{#if comparisonError}<div class="planning-error" role="alert"><span>{comparisonError}</span><button class="button button-secondary" type="button" onclick={() => void loadComparison()} disabled={!online || comparisonLoading}>Reintentar</button></div>{/if}
		{#if comparisonLoading}<p class="state-message" role="status">Leyendo cargas manuales…</p>{/if}
		{#if comparison && !comparisonLoading}
			<div class="comparison-summary">
				<div><span>Energía conocida</span><strong>{amount(comparisonTotal?.kwh ?? null, 'kWh')}</strong><small>{comparisonTotal?.known_kwh_count ?? 0} de {comparisonTotal?.charge_count ?? 0} cargas con kWh</small></div>
				<div><span>Coste conocido</span><strong>{amount(comparisonTotal?.cost_eur ?? null, '€')}</strong><small>{comparisonTotal?.known_cost_count ?? 0} con precio</small></div>
				<div><span>Coste unitario comparable</span><strong>{comparisonTotal?.effective_cost_eur_kwh === null || comparisonTotal?.effective_cost_eur_kwh === undefined ? 'Sin parejas kWh/precio' : `${euro.format(comparisonTotal.effective_cost_eur_kwh)} / kWh`}</strong><small>{comparisonTotal?.paired_count ?? 0} cargas con ambos datos</small></div>
			</div>
			{#if monthCount}
				<figure class="cost-chart evolution-chart" aria-labelledby="cost-chart-title">
					<svg viewBox={`0 0 ${chartWidth} 132`} role="img" aria-labelledby="cost-chart-title cost-chart-desc" preserveAspectRatio="xMinYMin meet">
						<title id="cost-chart-title">Coste conocido por mes</title><desc id="cost-chart-desc">Cada barra representa el coste que consta en registros manuales. Los valores numéricos y los datos que faltan aparecen debajo.</desc>
						<line x1="8" y1="101" x2={chartWidth - 8} y2="101" stroke="currentColor" opacity=".28" />
						{#each chartMonths as item (item.month.month)}
							<rect x={item.index * 58 + 17} y={101 - item.bar} width="25" height={item.bar} rx="5" fill="var(--forest)" opacity={item.month.cost_eur === null ? 0 : 0.82} />
							<text x={item.index * 58 + 29.5} y="120" text-anchor="middle" fill="currentColor" font-size="11">{monthLabel(item.month.month)}</text>
						{/each}
					</svg>
					<figcaption>Costes parciales posibles: se cuentan los importes conocidos y se indican los registros sin coste en cada mes.</figcaption>
				</figure>
				<ol class="month-list evolution-list" aria-label="Detalle mensual">{#each comparison.months as month (month.month)}
					<li class="evolution-item"><div class="month-heading"><strong>{monthLabel(month.month)}</strong><span>{month.charge_count} {month.charge_count === 1 ? 'carga' : 'cargas'}</span></div><div class="month-values"><span>{amount(month.kwh, 'kWh')}<small> {month.known_kwh_count} con energía</small></span><span>{amount(month.cost_eur, '€')}<small> {month.known_cost_count} con coste</small></span></div><p>{addHistoryText(month)}</p><details><summary>Desglose por ubicación</summary><ul>{#each month.locations as location, index (`${month.month}-${location.location ?? 'unknown'}-${index}`)}<li><strong>{location.location ?? 'Ubicación sin indicar'}</strong><span>{location.charge_count} cargas · {amount(location.kwh, 'kWh')} · {amount(location.cost_eur, '€')}</span></li>{/each}</ul></details></li>
				{/each}</ol>
			{:else}<div class="empty-state"><h4>Sin cargas manuales en esta ventana</h4><p>No se han encontrado registros para comparar durante los últimos {comparison.days} días.</p></div>{/if}
			{#if comparison.excluded_non_manual_count}<p class="small-note">Se excluyeron {comparison.excluded_non_manual_count} registros que no son manuales.</p>{/if}
		{:else if !comparisonLoading && !comparisonError}<p class="state-message">Conéctate para comparar las cargas guardadas en el servidor.</p>{/if}
	</section>

	<section class="simulator-section" aria-labelledby="simulator-heading">
		<div class="planning-section-heading"><div><p class="eyebrow">Escenario bajo tu control</p><h3 id="simulator-heading">Simular una carga</h3><p>Estimación constante por intervalos; no controla el coche, el cargador ni inicia ninguna operación.</p></div><button class="simulator-toggle" type="button" aria-expanded={simulatorOpen} aria-controls="energy-simulator-content" onclick={() => simulatorOpen = !simulatorOpen}>{simulatorOpen ? 'Cerrar simulador' : planResult ? 'Ver simulación guardada' : 'Abrir simulador'}</button></div>
		<div id="energy-simulator-content" class="simulator-content" hidden={!simulatorOpen}>
		<form class="simulation-form" onsubmit={calculate} novalidate>
			<div class="form-grid">
				<div class="field"><label for="plan-energy">Energía que quieres añadir</label><div class="input-unit"><input id="plan-energy" type="text" inputmode="decimal" placeholder="Sin valor predeterminado" bind:value={planForm.energy} disabled={busy || planLoading} aria-invalid={!!fieldErrors.energy} /><span>kWh</span></div>{#if fieldErrors.energy}<small>{fieldErrors.energy}</small>{/if}</div>
				<div class="field"><label for="plan-power">Potencia constante asumida</label><div class="input-unit"><input id="plan-power" type="text" inputmode="decimal" placeholder="Sin valor predeterminado" bind:value={planForm.power} disabled={busy || planLoading} aria-invalid={!!fieldErrors.power} /><span>kW</span></div>{#if fieldErrors.power}<small>{fieldErrors.power}</small>{/if}</div>
				<div class="field"><label for="plan-efficiency">Eficiencia estimada por ti</label><div class="input-unit"><input id="plan-efficiency" type="text" inputmode="decimal" placeholder="Sin valor predeterminado" bind:value={planForm.efficiency} disabled={busy || planLoading} aria-invalid={!!fieldErrors.efficiency} /><span>%</span></div>{#if fieldErrors.efficiency}<small>{fieldErrors.efficiency}</small>{/if}</div>
				<div class="field"><label for="plan-earliest">Primer momento posible · UTC</label><input id="plan-earliest" type="text" maxlength="64" autocomplete="off" placeholder="2026-10-13T21:00:00Z" bind:value={planForm.earliest} disabled={busy || planLoading} aria-invalid={!!fieldErrors.earliest} /><small>Escribe la Z final para dejar claro que la hora está en UTC.</small>{#if fieldErrors.earliest}<small class="field-error">{fieldErrors.earliest}</small>{/if}</div>
				<div class="field"><label for="plan-deadline">Plazo máximo · UTC</label><input id="plan-deadline" type="text" maxlength="64" autocomplete="off" placeholder="2026-10-14T07:00:00Z" bind:value={planForm.deadline} disabled={busy || planLoading} aria-invalid={!!fieldErrors.deadline} /><small>La fecha debe ser posterior al primer momento.</small>{#if fieldErrors.deadline}<small class="field-error">{fieldErrors.deadline}</small>{/if}</div>
			</div>
			<fieldset class="tariff-fieldset">
				<legend>Intervalos de precio introducidos manualmente</legend>
				<p>Deja fuera los huecos sin precio. Máximo {MAX_TARIFFS} intervalos; cada hora usa UTC explícito.</p>
				{#if fieldErrors.tariffs}<p class="field-error">{fieldErrors.tariffs}</p>{/if}
				<div class="tariff-list">{#each planForm.tariffs as tariff, index (index)}
					<div class="tariff-row">
						<div class="field"><label for={`tariff-${index}-start`}>Inicio UTC</label><input id={`tariff-${index}-start`} type="text" maxlength="64" placeholder="p. ej. 2026-10-13T23:00:00Z" bind:value={tariff.start} disabled={busy || planLoading} aria-invalid={!!fieldErrors[`tariff-${index}-start`]} />{#if fieldErrors[`tariff-${index}-start`]}<small class="field-error">{fieldErrors[`tariff-${index}-start`]}</small>{/if}</div>
						<div class="field"><label for={`tariff-${index}-end`}>Fin UTC</label><input id={`tariff-${index}-end`} type="text" maxlength="64" placeholder="p. ej. 2026-10-14T01:00:00Z" bind:value={tariff.end} disabled={busy || planLoading} aria-invalid={!!fieldErrors[`tariff-${index}-end`]} />{#if fieldErrors[`tariff-${index}-end`]}<small class="field-error">{fieldErrors[`tariff-${index}-end`]}</small>{/if}</div>
						<div class="field price-field"><label for={`tariff-${index}-price`}>Precio manual</label><div class="input-unit"><input id={`tariff-${index}-price`} type="text" inputmode="decimal" placeholder="Sin precio" bind:value={tariff.price} disabled={busy || planLoading} aria-invalid={!!fieldErrors[`tariff-${index}-price`]} /><span>€/kWh</span></div>{#if fieldErrors[`tariff-${index}-price`]}<small class="field-error">{fieldErrors[`tariff-${index}-price`]}</small>{/if}</div>
						<button class="remove-interval" type="button" aria-label={`Quitar intervalo ${index + 1}`} onclick={() => removeTariff(index)} disabled={busy || planLoading || planForm.tariffs.length <= 1}>Quitar</button>
					</div>
				{/each}</div>
				<button class="button button-secondary add-interval" type="button" onclick={addTariff} disabled={busy || planLoading || planForm.tariffs.length >= MAX_TARIFFS}>Añadir intervalo <span>{planForm.tariffs.length} / {MAX_TARIFFS}</span></button>
			</fieldset>
			{#if planError}<div class="planning-error" role="alert"><span>{planError}</span>{#if submitted}<button class="button button-secondary" type="button" onclick={() => void calculate(new SubmitEvent('submit', { cancelable: true }))} disabled={busy || planLoading}>Revisar y repetir</button>{/if}</div>{/if}
			<div class="simulation-actions"><p>Los precios y la eficiencia son tus entradas; los tramos no cubiertos quedan visibles.</p><button class="button button-primary" type="submit" disabled={busy || planLoading || !online}>{planLoading ? 'Calculando…' : 'Calcular escenario'}</button></div>
		</form>
		{#if planResult && resultFingerprint === formFingerprint()}
			<section class="result-card" aria-labelledby="plan-result-heading" aria-live="polite">
				<div class="result-top"><div><p class="eyebrow">Resultado informativo</p><h4 id="plan-result-heading">{planResult.satisfied_energy_kwh > 0 ? `${kwhFormat.format(planResult.satisfied_energy_kwh)} kWh de batería cubiertos` : 'Sin energía cubierta'}</h4></div><span class="result-state">{planResult.coverage.complete ? 'Cobertura tarifaria completa' : 'Cobertura tarifaria parcial'}</span></div>
				<div class="result-metrics"><div><span>Red estimada</span><strong>{kwhFormat.format(planResult.grid_energy_kwh)} kWh</strong></div><div><span>Coste estimado</span><strong>{euro.format(planResult.estimated_cost_eur)}</strong></div><div><span>Quedaría sin cubrir</span><strong>{kwhFormat.format(planResult.unsatisfied_energy_kwh)} kWh</strong></div><div><span>Tiempo programado</span><strong>{durationText(planResult)}</strong></div></div>
				<p class="coverage-note">{kwhFormat.format(planResult.coverage.priced_seconds / 3600)} de {kwhFormat.format(planResult.coverage.window_seconds / 3600)} horas con precio. La potencia se supone constante y el horario es una estimación.</p>
				{#if planResult.estimated_savings_eur !== null}<p class="savings-note">Diferencia estimada frente a cargar toda la misma energía desde el primer momento: {euro.format(planResult.estimated_savings_eur)}.</p>{:else}<p class="savings-note">No se calcula ahorro porque los precios no cubren toda la ventana comparable.</p>{/if}
				{#if planResult.warnings.length}<ul class="warnings">{#each planResult.warnings as warning}<li>{warningText(warning)}</li>{/each}</ul>{/if}
				{#if planResult.intervals.length}<details class="schedule-details"><summary>Ver horario estimado · {planResult.intervals.length} {planResult.intervals.length === 1 ? 'tramo' : 'tramos'}</summary><ol>{#each planResult.intervals as interval, index (`${interval.started_at}-${index}`)}<li><div><strong>{timeText(interval.started_at)}</strong><span>{timeText(interval.ended_at)}</span></div><div><span>{kwhFormat.format(interval.battery_energy_kwh)} kWh batería · {kwhFormat.format(interval.grid_energy_kwh)} kWh red</span><strong>{euro.format(interval.estimated_cost_eur)}</strong></div>{#if interval.partial}<small>Tramo parcial: termina antes del fin de la tarifa.</small>{/if}</li>{/each}</ol></details>{/if}
				<details class="baseline-details"><summary>Comparar con carga más temprana de la misma energía</summary><p>{kwhFormat.format(planResult.earliest_feasible.grid_energy_kwh)} kWh de red · {euro.format(planResult.earliest_feasible.estimated_cost_eur)}. Esta referencia también usa los mismos precios aportados.</p></details>
			</section>
		{/if}
		</div>
	</section>
</section>

<style>
	.energy-planning { display: grid; gap: 24px; min-width: 0; }
	.planning-heading h2 { margin: 2px 0 4px; font-size: clamp(1.35rem, 2vw, 1.75rem); letter-spacing: -.025em; }
	.planning-heading p:not(.eyebrow) { max-width: 62ch; margin: 0; color: var(--muted); }
	.eyebrow { margin: 0; color: var(--forest); font-size: .75rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
	.comparison-section, .simulator-section { display: grid; gap: 15px; min-width: 0; }
	.planning-section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 14px; }
	.simulator-toggle { display: inline-flex; min-width: 150px; min-height: 48px; align-items: center; justify-content: center; flex: 0 0 auto; padding: 9px 14px; border: 1px solid var(--line-strong); border-radius: 10px; background: transparent; color: var(--forest); font: inherit; font-size: .875rem; font-weight: 680; cursor: pointer; }
	.simulator-toggle:hover { background: var(--forest-soft); }
	.simulator-toggle:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
	.simulator-content { display: grid; gap: 15px; min-width: 0; }
	.simulator-content[hidden] { display: none; }
	.planning-section-heading h3 { margin: 3px 0 3px; font-size: 1.15rem; letter-spacing: -.015em; }
	.planning-section-heading p:not(.eyebrow) { max-width: 68ch; margin: 0; color: var(--muted); font-size: .9rem; }
	.range-select { display: grid; gap: 4px; min-width: 170px; color: var(--muted-strong); font-size: .8125rem; font-weight: 650; }
	select, input { min-height: 48px; min-width: 0; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface); color: var(--ink); font: inherit; }
	.comparison-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
	.comparison-summary > div { display: grid; gap: 4px; min-width: 0; padding: 14px 0; border-top: 1px solid var(--line); }
	.comparison-summary span, .comparison-summary small { color: var(--muted); font-size: .8125rem; }
	.comparison-summary strong { font-size: clamp(.95rem, 2vw, 1.2rem); overflow-wrap: anywhere; }
	.cost-chart { display: grid; gap: 5px; min-width: 0; margin: 0; padding: 12px 0 10px; overflow-x: auto; border-block: 1px solid var(--line); border-inline: 0; border-radius: 0; background: transparent; }
	.cost-chart svg { width: 100%; min-width: min(100%, 320px); max-height: 180px; color: var(--muted-strong); }
	.cost-chart figcaption { color: var(--muted); font-size: .8125rem; line-height: 1.4; }
	.month-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
	.month-list > li { display: grid; gap: 8px; min-width: 0; padding: 17px 0; border-top: 1px solid var(--line); }
	.month-heading, .month-values { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px 14px; }
	.month-heading span, .month-values small, .month-list p { color: var(--muted); font-size: .8125rem; }
	.month-values > span { font-weight: 650; }
	.month-values small { font-weight: 450; }
	.month-list p { margin: 0; }
	.month-list details { border-top: 1px solid var(--line); }
	.month-list summary, .schedule-details summary, .baseline-details summary { display: flex; align-items: center; min-height: 48px; color: var(--muted-strong); font-size: .875rem; font-weight: 640; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
	.month-list details ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
	.month-list details li { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 5px 12px; padding: 8px; border-radius: 8px; background: var(--surface-soft); }
	.month-list details li span { color: var(--muted); font-size: .8125rem; }
	.empty-state, .state-message { margin: 0; padding: 15px; border: 1px dashed var(--line-strong); border-radius: 13px; color: var(--muted); }
	.empty-state h4, .empty-state p { margin: 0; }
	.empty-state { display: grid; gap: 4px; }
	.small-note { margin: 0; color: var(--muted); font-size: .8125rem; }
	.simulation-form { display: grid; gap: 15px; min-width: 0; padding: clamp(14px, 2.4vw, 22px); border: 1px solid var(--line); border-radius: 18px; background: var(--surface); }
	.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px; }
	.field { display: grid; align-content: start; gap: 6px; min-width: 0; }
	.field label, .tariff-fieldset legend { color: var(--muted-strong); font-size: .875rem; font-weight: 670; }
	.field small { color: var(--muted); font-size: .75rem; line-height: 1.4; }
	.field .field-error, .field-error { color: var(--danger); }
	.field input[aria-invalid="true"] { border-color: var(--danger); }
	.input-unit { display: flex; align-items: center; gap: 8px; min-width: 0; }
	.input-unit span { flex: 0 0 auto; color: var(--muted); font-size: .875rem; }
	.input-unit input { flex: 1; width: 1%; }
	.tariff-fieldset { display: grid; gap: 10px; min-width: 0; margin: 0; padding: 12px; border: 1px solid var(--line); border-radius: 13px; }
	.tariff-fieldset legend { padding-inline: 6px; }
	.tariff-fieldset > p { margin: 0; color: var(--muted); font-size: .8125rem; }
	.tariff-list { display: grid; gap: 9px; }
	.tariff-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(130px, .75fr) auto; align-items: end; gap: 9px; min-width: 0; padding: 10px; border-radius: 11px; background: var(--surface-soft); }
	.tariff-row .field label { font-size: .75rem; }
	.tariff-row input { padding-inline: 9px; font-size: .875rem; }
	.remove-interval { min-height: 48px; min-width: 48px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); color: var(--muted-strong); cursor: pointer; }
	.add-interval { justify-self: start; }
	.add-interval span { color: inherit; opacity: .75; font-size: .75rem; }
	.button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 48px; padding: 9px 15px; border: 1px solid transparent; border-radius: 11px; font: inherit; font-weight: 680; cursor: pointer; transition: background-color 150ms ease, transform 150ms ease; }
	.button-primary { background: var(--forest); color: var(--on-primary); }
	.button-primary:hover:not(:disabled) { background: var(--forest-hover); transform: translateY(-1px); }
	.button-secondary { border-color: var(--line-strong); background: var(--surface); color: var(--ink); }
	.button-secondary:hover:not(:disabled) { background: var(--surface-soft); }
	.button:disabled, .remove-interval:disabled { opacity: .58; cursor: not-allowed; }
	.simulation-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
	.simulation-actions p { flex: 1 1 280px; margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.4; }
	.planning-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--danger) 32%, var(--line)); border-radius: 12px; background: var(--danger-soft); color: var(--danger); }
	.result-card { display: grid; gap: 13px; padding: clamp(14px, 2.4vw, 22px); border: 1px solid var(--line-strong); border-radius: 18px; background: var(--surface); }
	.result-top { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
	.result-top h4 { margin: 2px 0 0; font-size: 1.15rem; }
	.result-state { padding: 5px 9px; border-radius: 999px; background: var(--forest-soft); color: var(--forest); font-size: .75rem; font-weight: 720; }
	.result-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
	.result-metrics > div { display: grid; gap: 3px; min-width: 0; padding: 11px 0; border-top: 1px solid var(--line); }
	.result-metrics span, .coverage-note, .savings-note { color: var(--muted); font-size: .8125rem; }
	.result-metrics strong { font-size: 1.375rem; overflow-wrap: anywhere; }
	.coverage-note, .savings-note { margin: 0; line-height: 1.5; }
	.savings-note { padding-left: 10px; border-left: 2px solid var(--accent); color: var(--muted-strong); }
	.warnings { display: grid; gap: 5px; margin: 0; padding: 11px 11px 11px 30px; border-radius: 10px; background: var(--accent-soft); color: var(--muted-strong); font-size: .8125rem; }
	.schedule-details, .baseline-details { border-top: 1px solid var(--line); }
	.schedule-details ol { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
	.schedule-details li { display: grid; gap: 5px; padding: 10px; border-radius: 10px; background: var(--surface-soft); }
	.schedule-details li > div { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 4px 10px; }
	.schedule-details li span, .schedule-details li small, .baseline-details p { color: var(--muted); font-size: .8125rem; }
	.schedule-details li small { margin: 0; }
	.baseline-details p { margin: 0 0 9px; line-height: 1.5; }
	@media (max-width: 700px) { .planning-section-heading { align-items: stretch; flex-direction: column; }.simulator-toggle { width: 100%; }.range-select { min-width: 0; }.comparison-summary, .result-metrics { grid-template-columns: 1fr; }.form-grid { grid-template-columns: 1fr; }.tariff-row { grid-template-columns: 1fr 1fr; }.price-field { grid-column: 1 / 2; }.remove-interval { grid-column: 2; grid-row: 3; justify-self: end; min-width: 90px; }.cost-chart svg { width: max(100%, 300px); } }
	@media (prefers-reduced-motion: reduce) { .button { transition: none; }.button-primary:hover:not(:disabled) { transform: none; } }
</style>
