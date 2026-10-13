<script lang="ts">
	import { onMount, onDestroy, tick, untrack } from 'svelte';
	import { ApiError, getManualJourneys, getManualJourneyReport } from './api';
	import { count } from './format';
	import { getNativeBridge, isNativeApp } from './native';
	import { exportNativeJourneyReport, journeyReportExportError } from './journey-report-export';
	import { convertJourneyBasis, emptyJourneyForm, journeyQuantityInput, localJourneyTime, validateJourneyForm, type JourneyForm } from './manual-journey-form';
	import type { ManualJourney, ManualJourneyCreationRequest, ManualJourneyPage, ManualJourneyReport, PageProps } from './types';

	let { busy, online, revision, drafts, mutate, reserveJourneyCreation, confirmAction, onSessionExpired }:
		Pick<PageProps, 'busy' | 'online' | 'revision' | 'drafts' | 'mutate' | 'reserveJourneyCreation' | 'confirmAction' | 'onSessionExpired'> = $props();
	let page = $state<ManualJourneyPage | null>(null);
	let report = $state<ManualJourneyReport | null>(null);
	let loading = $state(false), readError = $state(''), actionError = $state('');
	let days = $state<30 | 90 | 365>(30), offset = $state(0);
	let editorOpen = $state(false), editing = $state<ManualJourney | null>(null);
	let form = $state<JourneyForm>(emptyJourneyForm());
	let errors = $state<Record<string, string>>({}), saveError = $state(''), saving = $state(false);
	let reservation = $state<ManualJourneyCreationRequest | null>(null);
	let sent = $state(false), sentAttempt = '';
	let exportMessage = $state(''), exportError = $state(false), exporting = $state(false);
	let exportButton = $state<HTMLButtonElement | undefined>();
	let reportHeading = $state<HTMLHeadingElement | undefined>();
	let baseline = JSON.stringify(emptyJourneyForm());
	let mounted = false, readGeneration = 0, editGeneration = 0, observedReadKey = '';
	let reads: AbortController | null = null;
	let summaryElement = $state<HTMLDivElement | undefined>();
	const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
	const native = isNativeApp();
	const pageSize = 20;
	const regularNumber = new Intl.NumberFormat('es-ES', { maximumSignificantDigits: 7 });
	const scientificNumber = new Intl.NumberFormat('es-ES', { maximumSignificantDigits: 7, notation: 'scientific' });
	const instantFormat = new Intl.DateTimeFormat('es-ES', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'shortOffset' });
	let fieldErrors = $derived(Object.entries(errors));
	let total = $derived(report?.totals);
	function number(value: number): string {
		return (value !== 0 && (value < 0.000001 || value >= 10000000) ? scientificNumber : regularNumber).format(value);
	}
	function dateTime(value: string): string { return instantFormat.format(new Date(value)); }

	function fingerprint() { return JSON.stringify(form); }
	function dirty() { return sent || fingerprint() !== baseline; }

	onMount(() => {
		mounted = true;
		observedReadKey = `${revision}:${online}:${offset}:${days}`;
		void load();
		return drafts.register('journey', dirty, () => sent);
	});
	onDestroy(() => { mounted = false; readGeneration++; editGeneration++; reads?.abort(); });
	$effect(() => {
		const key = { revision, online, offset, days };
		untrack(() => {
			if (!mounted) return;
			const readKey = `${key.revision}:${key.online}:${key.offset}:${key.days}`;
			if (readKey === observedReadKey) return;
			observedReadKey = readKey;
			if (!key.online) {
				readGeneration++; reads?.abort(); page = null; report = null; loading = false;
				readError = 'Sin conexión. El diario se consulta en el servidor y no se guarda en este dispositivo.';
			} else void load();
		});
	});

	async function load() {
		if (!mounted || !online) return;
		const request = ++readGeneration;
		const currentOffset = offset, currentDays = days, currentRevision = revision;
		reads?.abort(); reads = new AbortController();
		const signal = reads.signal;
		loading = true; readError = ''; page = null; report = null;
		try {
			const [newPage, newReport] = await Promise.all([
				getManualJourneys(pageSize, currentOffset, signal), getManualJourneyReport(currentDays, signal)
			]);
			if (!mounted || request !== readGeneration || currentRevision !== revision || !online) return;
			if (currentOffset && !newPage.items.length) {
				offset = Math.max(0, Math.ceil(newPage.total / pageSize) * pageSize - pageSize); return;
			}
			page = newPage; report = newReport;
		} catch (error) {
			if (!mounted || request !== readGeneration || signal.aborted) return;
			if (error instanceof ApiError && error.status === 401) { onSessionExpired(); return; }
			readError = error instanceof ApiError ? error.message : 'No se pudo abrir el diario. Inténtalo de nuevo.';
		} finally { if (mounted && request === readGeneration) loading = false; }
	}

	function reset() {
		editGeneration++; form = emptyJourneyForm(); baseline = fingerprint(); editing = null;
		errors = {}; saveError = ''; reservation = null; sent = false; sentAttempt = ''; editorOpen = false;
	}
	async function approveChange() {
		if (!dirty()) return true;
		return confirmAction(sent ? 'Guardado sin confirmar' : 'Cambios sin guardar', sent
			? 'El trayecto podría haberse guardado. Revisa el diario antes de descartarlo o registrar otro. ¿Quieres descartar este formulario?'
			: 'Se descartarán los cambios de este formulario.', 'Descartar formulario');
	}
	async function open(item?: ManualJourney) {
		if (busy || saving) return;
		const generation = editGeneration;
		if (!await approveChange() || !mounted || busy || saving || generation !== editGeneration) return;
		reset(); editing = item ?? null; editorOpen = true;
		if (item) {
			form = { timeBasis: 'utc', started: localJourneyTime(item.started_at, 'utc'), ended: localJourneyTime(item.ended_at, 'utc'),
				from: item.from_label ?? '', to: item.to_label ?? '',
				distance: journeyQuantityInput(item.distance_km),
				energy: journeyQuantityInput(item.energy_kwh),
				stops: item.stops.map(stop => ({ started: localJourneyTime(stop.started_at, 'utc'), ended: localJourneyTime(stop.ended_at, 'utc') })) };
			baseline = fingerprint();
		}
		await tick(); if (mounted) document.getElementById('journey-started')?.focus();
	}
	async function cancel() {
		if (busy || saving) return;
		const generation = editGeneration;
		if (await approveChange() && mounted && !busy && !saving && generation === editGeneration) reset();
	}
	function clearError(key: string) {
		if (errors[key]) { const next = { ...errors }; delete next[key]; errors = next; }
	}
	function changeBasis(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		const next = select.value === 'utc' ? 'utc' : 'local';
		const converted = convertJourneyBasis(form, next);
		if (converted) { form = converted; errors = {}; }
		else { form.timeBasis = next; errors = {}; saveError = 'No se pudieron convertir las fechas inválidas o ambiguas. Se conserva el texto: revisa las horas en la zona que has elegido.'; }
	}
	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!mounted || busy || saving || !online) return;
		const target = event.currentTarget as HTMLFormElement;
		const previousFocus = document.activeElement instanceof HTMLElement && target.contains(document.activeElement) ? document.activeElement : null;
		const result = validateJourneyForm(form, Date.now(), editing ?? undefined);
		errors = result.errors; saveError = '';
		if (!result.values) { await tick(); if (mounted) summaryElement?.focus(); return; }
		const payload = result.values;
		const edit = editing ? { id: editing.id, revision: editing.revision } : null;
		const attempt = JSON.stringify({ edit, payload });
		if (sent && sentAttempt !== attempt) {
			saveError = 'El guardado anterior no está confirmado. Vuelve a esos datos para reintentarlo o revisa el diario antes de descartar el formulario.'; return;
		}
		const draft = fingerprint(), generation = editGeneration;
		saving = true;
		try {
			if (!edit && !reservation) {
				const prepared = await reserveJourneyCreation();
				if (!mounted || generation !== editGeneration) return;
				reservation = prepared;
			}
			if (!mounted || generation !== editGeneration) return;
			if (draft !== fingerprint()) { saveError = 'Los datos cambiaron mientras se preparaba el guardado. Revisa el formulario.'; return; }
			sent = true; sentAttempt = attempt;
			const saved = await mutate(edit ? `/api/journeys/${edit.id}` : '/api/journeys', edit ? 'PUT' : 'POST',
				edit ? { ...payload, revision: edit.revision } : { ...payload, request_id: reservation!.id },
				edit ? 'El trayecto manual se ha actualizado.' : 'El trayecto manual se ha guardado.');
			if (!mounted || generation !== editGeneration) return;
			if (saved) { reset(); if (offset) offset = 0; else void load(); }
			else saveError = 'No se pudo confirmar el guardado. Conservamos el formulario: revisa el diario o repite el mismo intento.';
		} catch {
			if (!mounted || generation !== editGeneration) return;
			saveError = sent ? 'No se pudo confirmar el guardado. Conservamos el formulario: revisa el diario o repite el mismo intento.'
				: 'No se pudo preparar el guardado. No se envió el trayecto; el formulario sigue intacto.';
		} finally {
			if (mounted) {
				saving = false; await tick();
				if (mounted && saveError && document.hasFocus() && document.activeElement === document.body && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
			}
		}
	}
	async function remove(item: ManualJourney) {
		if (busy || saving) return;
		const generation = editGeneration;
		const accepted = await confirmAction('Eliminar trayecto manual', `Se eliminará este registro introducido por ti. Esta operación no se puede deshacer.${editing?.id === item.id && dirty() ? ' También se descartará el formulario de edición de este trayecto.' : ''}`, 'Eliminar registro');
		if (!accepted || !mounted || busy || saving || generation !== editGeneration) return;
		actionError = ''; saving = true;
		try {
			const removed = await mutate(`/api/journeys/${item.id}`, 'DELETE', { revision: item.revision }, 'El trayecto manual se ha eliminado.');
			if (!mounted) return;
			if (removed) { if (editing?.id === item.id) reset(); void load(); }
			else actionError = 'No se pudo confirmar la eliminación. Actualiza el diario antes de volver a intentarlo.';
		} catch { if (mounted) actionError = 'No se pudo confirmar la eliminación. Actualiza el diario antes de volver a intentarlo.'; }
		finally { if (mounted) saving = false; }
	}
	async function exportReport() {
		if (!report || loading || busy || saving || exporting || !online) return;
		exportMessage = ''; exportError = false;
		if (native) {
			const currentDays = days, focused = document.activeElement === exportButton;
			exporting = true;
			try {
				const result = await exportNativeJourneyReport(getNativeBridge(), currentDays);
				if (!mounted) return;
				exportMessage = result.status === 'cancelled' ? 'Guardado cancelado. El informe sigue disponible.'
					: `Selector de archivos finalizado para el informe de ${currentDays} días. Comprueba la copia en la ubicación elegida.`;
			} catch (error) {
				if (!mounted) return;
				const result = journeyReportExportError(error);
				exportMessage = result.message; exportError = true;
				if (result.sessionExpired) onSessionExpired();
			} finally {
				if (mounted) {
					exporting = false; await tick();
					if (mounted && focused && document.hasFocus() && document.activeElement === document.body) {
						if (exportButton && !exportButton.disabled) exportButton.focus();
						else reportHeading?.focus();
					}
				}
			}
			return;
		}
		let url = '';
		try {
			url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
			const link = document.createElement('a'); link.href = url; link.download = `pulse-trayectos-manuales-${days}dias.json`;
			document.body.append(link); link.click(); link.remove();
			exportMessage = 'Informe preparado. Comprueba la descarga en el navegador. La copia completa de los registros está en Ajustes.';
		} catch { exportMessage = 'No se pudo preparar el archivo. Puedes volver a intentarlo.'; exportError = true; }
		finally { if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
	}
</script>

<section class="manual-journeys" aria-labelledby="manual-journeys-heading">
	<div class="panel-header">
		<div><h2 id="manual-journeys-heading">Trayectos manuales</h2><p>Tu registro, separado de los datos de demostración y del vehículo.</p></div>
		<button class="button button-secondary" disabled={busy || saving || !online} onclick={() => open()}>Añadir trayecto</button>
	</div>
	{#if editorOpen}
		<form class="journey-form" onsubmit={save} novalidate aria-labelledby="journey-form-heading">
			<h3 id="journey-form-heading">{editing ? 'Editar trayecto manual' : 'Registrar un trayecto terminado'}</h3>
			<div class="field"><label for="journey-time-basis">Zona de las fechas</label><select id="journey-time-basis" value={form.timeBasis} onchange={changeBasis} disabled={busy || saving}><option value="local">Hora del dispositivo ({timezone})</option><option value="utc">UTC</option></select></div>
			<p class="muted">Fechas en {form.timeBasis === 'utc' ? 'UTC' : timezone}. Si cambias la zona, se convierten las horas válidas. Distancia y energía son opcionales: vacío significa que no tienes ese dato.</p>
			{#if fieldErrors.length}
				<div class="form-error" role="alert" tabindex="-1" bind:this={summaryElement}><strong>Revisa estos campos</strong><ul>{#each fieldErrors as [key, message]}<li><a href={`#journey-${key}`}>{message}</a></li>{/each}</ul></div>
			{/if}
			{#if saveError}<p class="form-error" role="alert" aria-atomic="true">{saveError}</p>{/if}
			<div class="form-grid">
				<div class="field"><label for="journey-started">Inicio</label><input id="journey-started" type="datetime-local" step="any" required disabled={busy || saving} bind:value={form.started} oninput={() => clearError('started')} aria-invalid={!!errors.started} aria-describedby={errors.started ? 'journey-started-error' : undefined} />{#if errors.started}<small id="journey-started-error">{errors.started}</small>{/if}</div>
				<div class="field"><label for="journey-ended">Fin</label><input id="journey-ended" type="datetime-local" step="any" required disabled={busy || saving} bind:value={form.ended} oninput={() => clearError('ended')} aria-invalid={!!errors.ended} aria-describedby={errors.ended ? 'journey-ended-error' : undefined} />{#if errors.ended}<small id="journey-ended-error">{errors.ended}</small>{/if}</div>
				<div class="field"><label for="journey-from">Origen (opcional)</label><input id="journey-from" maxlength="120" disabled={busy || saving} bind:value={form.from} oninput={() => clearError('from')} aria-invalid={!!errors.from} aria-describedby={errors.from ? 'journey-from-error' : undefined} />{#if errors.from}<small id="journey-from-error">{errors.from}</small>{/if}</div>
				<div class="field"><label for="journey-to">Destino (opcional)</label><input id="journey-to" maxlength="120" disabled={busy || saving} bind:value={form.to} oninput={() => clearError('to')} aria-invalid={!!errors.to} aria-describedby={errors.to ? 'journey-to-error' : undefined} />{#if errors.to}<small id="journey-to-error">{errors.to}</small>{/if}</div>
				<div class="field"><label for="journey-distance">Distancia en km (opcional)</label><input id="journey-distance" inputmode="decimal" disabled={busy || saving} bind:value={form.distance} oninput={() => clearError('distance')} aria-invalid={!!errors.distance} aria-describedby={errors.distance ? 'journey-distance-error' : undefined} />{#if errors.distance}<small id="journey-distance-error">{errors.distance}</small>{/if}</div>
				<div class="field"><label for="journey-energy">Energía en kWh (opcional)</label><input id="journey-energy" inputmode="decimal" disabled={busy || saving} bind:value={form.energy} oninput={() => clearError('energy')} aria-invalid={!!errors.energy} aria-describedby={errors.energy ? 'journey-energy-error' : undefined} />{#if errors.energy}<small id="journey-energy-error">{errors.energy}</small>{/if}</div>
			</div>
			<fieldset id="journey-stops" class="stops"><legend>Paradas declaradas (opcional)</legend><p class="muted">Sólo se descuentan las paradas que indiques; el resto del tiempo no confirma conducción.</p>
				{#each form.stops as stop, index}
					<div class="stop-row">
						<div class="field"><label for={`journey-stop-${index}-started`}>Inicio de parada {index + 1}</label><input id={`journey-stop-${index}-started`} type="datetime-local" step="any" disabled={busy || saving} bind:value={stop.started} oninput={() => clearError(`stop-${index}-started`)} aria-invalid={!!errors[`stop-${index}-started`]} aria-describedby={errors[`stop-${index}-started`] ? `journey-stop-${index}-started-error` : undefined} />{#if errors[`stop-${index}-started`]}<small id={`journey-stop-${index}-started-error`}>{errors[`stop-${index}-started`]}</small>{/if}</div>
						<div class="field"><label for={`journey-stop-${index}-ended`}>Fin de parada {index + 1}</label><input id={`journey-stop-${index}-ended`} type="datetime-local" step="any" disabled={busy || saving} bind:value={stop.ended} oninput={() => clearError(`stop-${index}-ended`)} aria-invalid={!!errors[`stop-${index}-ended`]} aria-describedby={errors[`stop-${index}-ended`] ? `journey-stop-${index}-ended-error` : undefined} />{#if errors[`stop-${index}-ended`]}<small id={`journey-stop-${index}-ended-error`}>{errors[`stop-${index}-ended`]}</small>{/if}</div>
						<button type="button" class="button button-secondary" disabled={busy || saving} onclick={() => { form.stops = form.stops.filter((_, current) => current !== index); errors = {}; }}>Quitar parada {index + 1}</button>
					</div>
				{/each}
				{#if errors.stops}<p class="form-error">{errors.stops}</p>{/if}
				<button type="button" class="button button-secondary" disabled={busy || saving || form.stops.length >= 32} onclick={() => { form.stops = [...form.stops, { started: '', ended: '' }]; }}>Añadir parada</button>
			</fieldset>
			<div class="actions"><button class="button button-primary" disabled={busy || saving || !online} type="submit">{saving ? 'Guardando…' : sent ? 'Reintentar el mismo guardado' : editing ? 'Guardar cambios' : 'Guardar trayecto'}</button><button class="button button-secondary" type="button" disabled={busy || saving} onclick={cancel}>Descartar formulario</button></div>
		</form>
	{/if}
	{#if loading}<p role="status">Cargando diario manual…</p>
	{:else if readError}<div class="read-error"><p role="alert">{readError}</p><button class="button button-secondary" disabled={busy || !online || saving} onclick={load}>Actualizar diario</button></div>
	{:else if page}
		<div class="list-heading"><p>{count(page.total)} {page.total === 1 ? 'registro manual' : 'registros manuales'}</p><button class="button button-secondary" disabled={busy || saving || !online} onclick={load}>Actualizar diario</button></div>
		{#if actionError}<p class="form-error" role="alert">{actionError}</p>{/if}
		{#if !page.total}<p class="empty-copy">Aún no has registrado trayectos. La recepción automática del B10 está pendiente; puedes añadir un recorrido que hayas realizado.</p>{/if}
		{#each page.items as item (item.id)}
			<article class="manual-row">
				<div><h3>{item.from_label ?? 'Origen sin indicar'} → {item.to_label ?? 'Destino sin indicar'}</h3><p>{dateTime(item.started_at)} → {dateTime(item.ended_at)} · Introducido por ti</p>
					<dl><div><dt>Distancia</dt><dd>{item.distance_km === null ? 'Sin dato' : `${number(item.distance_km)} km`}</dd></div><div><dt>Energía declarada</dt><dd>{item.energy_kwh === null ? 'Sin dato' : `${number(item.energy_kwh)} kWh`}</dd></div><div><dt>Duración total</dt><dd>{number(item.duration_min)} min</dd></div><div><dt>Paradas declaradas</dt><dd>{number(item.paused_min)} min</dd></div><div><dt>Tiempo sin esas paradas</dt><dd>{number(item.active_min)} min</dd></div><div><dt>Consumo con tus datos</dt><dd>{item.consumption_kwh_100km === null ? 'Sin datos suficientes' : `${number(item.consumption_kwh_100km)} kWh/100 km`}</dd></div></dl>
				</div><div class="actions"><button class="button button-secondary" disabled={busy || saving || !online} onclick={() => open(item)} aria-label={`Editar trayecto de ${item.from_label ?? 'origen sin indicar'} a ${item.to_label ?? 'destino sin indicar'}`}>Editar</button><button class="button button-secondary" disabled={busy || saving || !online} onclick={() => remove(item)} aria-label={`Eliminar trayecto de ${item.from_label ?? 'origen sin indicar'} a ${item.to_label ?? 'destino sin indicar'}`}>Eliminar</button></div>
			</article>
		{/each}
		{#if page.total > pageSize}<nav class="actions" aria-label="Páginas de trayectos manuales"><button class="button button-secondary" disabled={busy || saving || offset === 0} onclick={() => offset = Math.max(0, offset - pageSize)}>Anteriores</button><span>{offset + 1}–{Math.min(offset + pageSize, page.total)} de {count(page.total)}</span><button class="button button-secondary" disabled={busy || saving || offset + pageSize >= page.total} onclick={() => offset += pageSize}>Siguientes</button></nav>{/if}
	{/if}
	<div class="report-heading"><h3 bind:this={reportHeading} tabindex="-1">Informe de tus registros</h3><div class="field"><label for="journey-period">Periodo</label><select id="journey-period" bind:value={days} disabled={busy || saving || exporting}><option value={30}>Últimos 30 días</option><option value={90}>Últimos 90 días</option><option value={365}>Últimos 365 días</option></select></div></div>
	{#if report && total}
		<p class="muted">Se incluyen recorridos completos por su fecha de inicio. Meses en Europe/Madrid. El informe sólo reúne entradas manuales; no confirma que hayas registrado todos los viajes.</p>
		<dl class="report-totals"><div><dt>Registros</dt><dd>{count(total.count)}</dd></div><div><dt>Distancia conocida</dt><dd>{total.known_distance_count ? `${number(total.distance_km)} km` : 'Sin dato'}</dd></div><div><dt>Energía declarada</dt><dd>{total.known_energy_count ? `${number(total.energy_kwh)} kWh` : 'Sin dato'}</dd></div></dl>
		<p>{count(total.known_distance_count)} con distancia · {count(total.known_energy_count)} con energía. Consumo: {total.consumption_kwh_100km === null ? 'sin datos suficientes' : `${number(total.consumption_kwh_100km)} kWh/100 km`}.</p>
		<p class="muted">El consumo usa sólo {count(total.paired_count)} {total.paired_count === 1 ? 'registro' : 'registros'} con ambos datos y distancia positiva ({number(total.paired_distance_km)} km / {number(total.paired_energy_kwh)} kWh). La energía sin distancia no entra en ese cálculo. Las cifras se muestran redondeadas.</p>
		<details><summary>Detalle mensual</summary><div class="months">{#each report.months as month (month.month_key)}<div><strong>{month.month_key}</strong><span>{count(month.count)} registros</span><span>{month.known_distance_count ? `${number(month.distance_km)} km` : 'Sin distancia'}</span><span>{month.known_energy_count ? `${number(month.energy_kwh)} kWh` : 'Sin energía'}</span></div>{/each}</div></details>
		<button class="button button-secondary" bind:this={exportButton} disabled={busy || saving || exporting || loading || !online} onclick={exportReport}>{exporting ? 'Preparando archivo…' : native ? 'Guardar informe JSON' : 'Exportar informe JSON'}</button>
		{#if native}<p class="muted">El selector de archivos te permite elegir dónde guardar una copia actual de los agregados. No incluye etiquetas de trayectos ni credenciales.</p>{/if}
	{/if}
	{#if exportMessage}<p role={exportError ? 'alert' : 'status'}>{exportMessage}</p>{/if}
</section>

<style>
	.manual-journeys { display: grid; gap: 18px; min-width: 0; }
	h2, h3, p { margin: 0; } h2 { font-size: 1.125rem; } h3 { font-size: .9375rem; }
	p, label, dt, dd { font-size: .875rem; line-height: 1.6; } p, dt, .muted { color: var(--muted); }
	.panel-header, .list-heading, .report-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
	.panel-header > div { display: grid; gap: 6px; flex: 1 1 240px; }
	.journey-form { padding: 20px; border: 1px solid var(--line-strong); border-radius: 12px; display: grid; gap: 18px; }
	.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
	label { display: grid; gap: 7px; min-width: 0; font-weight: 600; }
	input, select { min-height: 48px; min-width: 0; width: 100%; }
	.field small { color: var(--danger); font-weight: 500; } .form-error { padding: 14px; border: 1px solid var(--danger); border-radius: 8px; color: var(--danger); line-height: 1.6; overflow-wrap: anywhere; }
	.form-error ul { margin: 8px 0 0; padding-left: 20px; } .form-error a { color: inherit; }
	.stops { border: 0; margin: 0; padding: 0; min-width: 0; display: grid; gap: 14px; } .stops legend { font-size: .9375rem; font-weight: 650; padding: 0 0 8px; }
	.stop-row { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); } .stop-row button { grid-column: 1 / -1; justify-self: start; }
	.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; } .button { min-height: 48px; white-space: normal; }
	.manual-row { display: grid; gap: 14px; border-bottom: 1px solid var(--line); padding: 18px 0; min-width: 0; }
	.manual-row h3 { overflow-wrap: anywhere; } .manual-row > div:first-child { display: grid; gap: 10px; }
	dl { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; } dd { margin: 3px 0 0; font-weight: 650; overflow-wrap: anywhere; }
	.report-totals { grid-template-columns: repeat(3, minmax(0, 1fr)); } .report-totals dd { font-size: 1.125rem; }
	summary { min-height: 44px; align-content: center; cursor: pointer; color: var(--forest); font-size: .875rem; font-weight: 650; }
	.months { display: grid; gap: 12px; padding: 12px 0; } .months > div { display: flex; gap: 12px; flex-wrap: wrap; justify-content: space-between; font-size: .8125rem; }
	.read-error { display: grid; gap: 12px; }
	@media (max-width: 580px) { .form-grid, .stop-row { grid-template-columns: minmax(0, 1fr); } .journey-form { padding: 16px; } .report-totals { grid-template-columns: minmax(0, 1fr); } }
</style>
