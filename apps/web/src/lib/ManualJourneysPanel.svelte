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
	let reportRevision = $state<number | null>(null);
	let loading = $state(false), readError = $state(''), actionError = $state('');
	let days = $state<30 | 90 | 365>(30), offset = $state(0);
	let searchInput = $state(''), appliedSearch = $state('');
	let tagInput = $state(''), appliedTag = $state(''), filterError = $state('');
	let editorOpen = $state(false), editing = $state<ManualJourney | null>(null);
	let form = $state<JourneyForm>(emptyJourneyForm());
	let tagDraft = $state('');
	let tagInputElement = $state<HTMLInputElement | undefined>();
	let errors = $state<Record<string, string>>({}), saveError = $state(''), saving = $state(false);
	let reservation = $state<ManualJourneyCreationRequest | null>(null);
	let sent = $state(false), sentAttempt = '';
	let exportMessage = $state(''), exportError = $state(false), exporting = $state(false);
	let exportButton = $state<HTMLButtonElement | undefined>();
	let reportHeading = $state<HTMLHeadingElement | undefined>();
	let baseline = JSON.stringify({ form: emptyJourneyForm(), tagDraft: '' });
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

	function fingerprint() { return JSON.stringify({ form, tagDraft }); }
	function dirty() { return sent || fingerprint() !== baseline; }
	function validFilterText(value: string): boolean {
		return !/[\u0000-\u001f\u007f-\u009f]/u.test(value) && !/[\ud800-\udfff]/u.test(value.replace(/[\ud800-\udbff][\udc00-\udfff]/gu, ''));
	}

	onMount(() => {
		mounted = true;
		observedReadKey = `${revision}:${online}:${offset}:${days}:${appliedTag}:${appliedSearch}`;
		void load();
		return drafts.register('journey', dirty, () => sent);
	});
	onDestroy(() => { mounted = false; readGeneration++; editGeneration++; reads?.abort(); });
	$effect(() => {
		const key = { revision, online, offset, days, appliedTag, appliedSearch };
		untrack(() => {
			if (!mounted) return;
			const readKey = `${key.revision}:${key.online}:${key.offset}:${key.days}:${key.appliedTag}:${key.appliedSearch}`;
			if (readKey === observedReadKey) return;
			observedReadKey = readKey;
			if (!key.online) {
				readGeneration++; reads?.abort(); page = null; report = null; reportRevision = null; loading = false;
				readError = 'Sin conexión. El diario se consulta en el servidor y no se guarda en este dispositivo.';
			} else void load();
		});
	});

	async function load() {
		if (!mounted || !online) return;
		const request = ++readGeneration;
		const currentOffset = offset, currentDays = days, currentRevision = revision;
		const currentFilters = { ...(appliedTag ? { tag: appliedTag } : {}), ...(appliedSearch ? { search: appliedSearch } : {}) };
		reads?.abort(); reads = new AbortController();
		const signal = reads.signal;
		loading = true; readError = ''; page = null;
		try {
			const [newPage, newReport] = await Promise.all([
				getManualJourneys(pageSize, currentOffset, signal, currentFilters), getManualJourneyReport(currentDays, signal)
			]);
			if (!mounted || request !== readGeneration || currentRevision !== revision || !online) return;
			if (currentOffset && !newPage.items.length) {
				offset = Math.max(0, Math.ceil(newPage.total / pageSize) * pageSize - pageSize); return;
			}
			page = newPage; report = newReport; reportRevision = currentRevision;
		} catch (error) {
			if (!mounted || request !== readGeneration || signal.aborted) return;
			if (error instanceof ApiError && error.status === 401) { report = null; reportRevision = null; onSessionExpired(); return; }
			readError = error instanceof ApiError ? error.message : 'No se pudo abrir el diario. Inténtalo de nuevo.';
		} finally { if (mounted && request === readGeneration) loading = false; }
	}

	function reset() {
		editGeneration++; form = emptyJourneyForm(); tagDraft = ''; baseline = fingerprint(); editing = null;
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
				notes: item.notes ?? '', tags: item.tags ?? [],
				stops: item.stops.map(stop => ({ started: localJourneyTime(stop.started_at, 'utc'), ended: localJourneyTime(stop.ended_at, 'utc') })) };
			tagDraft = ''; baseline = fingerprint();
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
	function addTag(): boolean {
		const tag = tagDraft.trim().normalize('NFC');
		if (!tag) { tagDraft = ''; return true; }
		if ([...tag].length > 32 || !validFilterText(tag)) { errors = { ...errors, tags: 'Cada etiqueta admite hasta 32 caracteres y no puede contener caracteres de control.' }; tagInputElement?.focus(); return false; }
		const key = tag.toLowerCase().replace(/ß/g, 'ss').replace(/ς/g, 'σ');
		if (form.tags.some(existing => existing.normalize('NFC').trim().toLowerCase().replace(/ß/g, 'ss').replace(/ς/g, 'σ') === key)) {
			errors = { ...errors, tags: 'Esa etiqueta ya está añadida.' }; tagInputElement?.focus(); return false;
		}
		if (form.tags.length >= 8) { errors = { ...errors, tags: 'Puedes añadir hasta 8 etiquetas.' }; tagInputElement?.focus(); return false; }
		form.tags = [...form.tags, tag]; tagDraft = ''; clearError('tags'); tagInputElement?.focus(); return true;
	}
	function removeTag(index: number) {
		form.tags = form.tags.filter((_, current) => current !== index);
		clearError('tags');
		tagInputElement?.focus();
	}
	function handleTagKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.isComposing && tagDraft.trim()) { event.preventDefault(); addTag(); }
	}
	function preventNoteBreak(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.isComposing) event.preventDefault();
	}
	function applyFilters() {
		const search = searchInput.trim(), tag = tagInput.trim().normalize('NFC');
		if (!validFilterText(search) || !validFilterText(tag)) { filterError = 'Quita los caracteres de control antes de buscar.'; return; }
		if ([...search].length > 100) { filterError = 'La búsqueda admite hasta 100 caracteres.'; return; }
		if ([...tag].length > 32) { filterError = 'La etiqueta admite hasta 32 caracteres.'; return; }
		filterError = ''; appliedSearch = search; appliedTag = tag; offset = 0;
	}
	function submitFilters(event: SubmitEvent) { event.preventDefault(); applyFilters(); }
	function clearFilters() {
		searchInput = ''; tagInput = ''; filterError = ''; appliedSearch = ''; appliedTag = ''; offset = 0;
	}
	function filterByTag(tag: string) { tagInput = tag; applyFilters(); }
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
		if (tagDraft.trim() && !addTag()) { await tick(); if (mounted) summaryElement?.focus(); return; }
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
				if (result.sessionExpired) { report = null; reportRevision = null; onSessionExpired(); }
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
		<div class="panel-copy"><span class="eyebrow">DIARIO PERSONAL</span><h2 id="manual-journeys-heading">Trayectos manuales</h2><p>Tu registro privado en Pulse, separado de los datos de demostración y del vehículo.</p></div>
		<button class="button button-primary" disabled={busy || saving || !online} onclick={() => open()}>Añadir trayecto</button>
	</div>
	{#if editorOpen}
		<form class="journey-form" onsubmit={save} novalidate aria-labelledby="journey-form-heading">
			<div class="form-intro"><div><span class="eyebrow">{editing ? 'ACTUALIZAR REGISTRO' : 'NUEVO REGISTRO'}</span><h3 id="journey-form-heading">{editing ? 'Editar trayecto manual' : 'Registrar un trayecto terminado'}</h3></div><p>Incluye solo los datos que conoces. No completamos información que no hayas indicado.</p></div>
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
			<div class="field notes-field"><label for="journey-notes">Nota <span class="optional">(opcional)</span></label><textarea id="journey-notes" rows="3" maxlength="4000" disabled={busy || saving} bind:value={form.notes} oninput={() => clearError('notes')} onkeydown={preventNoteBreak} aria-invalid={!!errors.notes} aria-describedby={errors.notes ? 'journey-notes-error journey-notes-hint' : 'journey-notes-hint'}></textarea><p class="field-hint" id="journey-notes-hint">Hasta 2.000 caracteres. Una sola línea, sin caracteres de control. {[...form.notes].length}/2.000</p>{#if errors.notes}<small id="journey-notes-error">{errors.notes}</small>{/if}</div>
			<div class="field tag-field" id="journey-tags" tabindex="-1"><label for="journey-tag-input">Etiquetas <span class="optional">(opcional)</span></label><div class="tag-input-row"><input id="journey-tag-input" bind:this={tagInputElement} maxlength="64" disabled={busy || saving} bind:value={tagDraft} oninput={() => clearError('tags')} onkeydown={handleTagKeydown} aria-invalid={!!errors.tags} aria-describedby={errors.tags ? 'journey-tags-error journey-tags-hint' : 'journey-tags-hint'} placeholder="Por ejemplo: trabajo" /><button type="button" class="button button-secondary" disabled={busy || saving || form.tags.length >= 8 || !tagDraft.trim()} onclick={addTag}>Añadir etiqueta</button></div><p class="field-hint" id="journey-tags-hint">Hasta 8 etiquetas, de 32 caracteres cada una. {form.tags.length}/8</p>{#if form.tags.length}<ul class="tag-list edit-tags" aria-label="Etiquetas añadidas">{#each form.tags as tag, index}<li><span class="tag-chip">{tag}</span><button type="button" class="tag-remove" disabled={busy || saving} onclick={() => removeTag(index)} aria-label={`Quitar etiqueta ${tag}`}>×</button></li>{/each}</ul>{/if}{#if errors.tags}<small id="journey-tags-error">{errors.tags}</small>{/if}</div>
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
	<form class="journey-filters" onsubmit={submitFilters} aria-label="Buscar trayectos manuales">
		<div class="filter-search"><label for="journey-search">Buscar por texto</label><input id="journey-search" type="search" maxlength="200" bind:value={searchInput} placeholder="Origen, destino, nota o etiqueta" aria-invalid={!!filterError} aria-describedby={filterError ? 'journey-filter-error journey-search-hint' : 'journey-search-hint'} /><small id="journey-search-hint">La búsqueda recorre origen, destino, notas y etiquetas.</small></div>
		<div class="filter-tag"><label for="journey-filter-tag">Etiqueta exacta</label><input id="journey-filter-tag" maxlength="64" bind:value={tagInput} placeholder="Por ejemplo: trabajo" aria-invalid={!!filterError} aria-describedby={filterError ? 'journey-filter-error' : undefined} /></div>
		<div class="filter-actions"><button class="button button-primary" type="submit" disabled={busy || saving || !online}>Buscar</button>{#if appliedSearch || appliedTag || searchInput || tagInput}<button class="button button-quiet" type="button" disabled={busy || saving} onclick={clearFilters}>Limpiar</button>{/if}</div>
		{#if filterError}<p class="form-error" id="journey-filter-error" role="alert">{filterError}</p>{/if}
	</form>
	{#if loading}<p role="status">Cargando diario manual…</p>
	{:else if readError}<div class="read-error"><p role="alert">{readError}</p><button class="button button-secondary" disabled={busy || !online || saving} onclick={load}>Actualizar diario</button></div>
	{:else if page}
		<div class="list-heading"><p><strong>{count(page.total)}</strong> {page.total === 1 ? 'trayecto' : 'trayectos'}{appliedSearch || appliedTag ? (page.total === 1 ? ' encontrado' : ' encontrados') : (page.total === 1 ? ' registrado' : ' registrados')}</p><button class="button button-quiet" disabled={busy || saving || !online} onclick={load}>Actualizar</button></div>
		{#if actionError}<p class="form-error" role="alert">{actionError}</p>{/if}
		{#if !page.total}<p class="empty-copy">{appliedSearch || appliedTag ? 'No hay trayectos que coincidan con esa búsqueda. Prueba otro texto o etiqueta.' : 'Aún no has registrado trayectos. La recepción automática del B10 está pendiente; puedes añadir un recorrido que hayas realizado.'}</p>{/if}
		<div class="journey-timeline evolution-list">
		{#each page.items as item (item.id)}
			<article class="journey-entry evolution-item">
				<span class="timeline-pin" aria-hidden="true"></span>
				<div class="journey-entry-body">
					<details class="journey-details">
						<summary><time datetime={item.started_at}>{dateTime(item.started_at)}</time><strong>{item.from_label ?? 'Origen sin indicar'} <span aria-hidden="true">→</span> {item.to_label ?? 'Destino sin indicar'}</strong><span class="journey-brief">{item.distance_km === null ? 'Distancia sin dato' : `${number(item.distance_km)} km`} <span aria-hidden="true">·</span> {item.energy_kwh === null ? 'Energía sin dato' : `${number(item.energy_kwh)} kWh`}</span><span class="details-hint">Ver detalles</span></summary>
						<div class="journey-expanded">
							<dl class="journey-metrics"><div><dt>Distancia</dt><dd>{item.distance_km === null ? 'Sin dato' : `${number(item.distance_km)} km`}</dd></div><div><dt>Energía declarada</dt><dd>{item.energy_kwh === null ? 'Sin dato' : `${number(item.energy_kwh)} kWh`}</dd></div><div><dt>Duración total</dt><dd>{number(item.duration_min)} min</dd></div><div><dt>Paradas declaradas</dt><dd>{number(item.paused_min)} min</dd></div><div><dt>Tiempo sin esas paradas</dt><dd>{number(item.active_min)} min</dd></div><div><dt>Consumo con tus datos</dt><dd>{item.consumption_kwh_100km === null ? 'Sin datos suficientes' : `${number(item.consumption_kwh_100km)} kWh/100 km`}</dd></div></dl>
							<p class="journey-period">Hasta {dateTime(item.ended_at)} · Introducido por ti</p>
							{#if item.notes}<section class="journey-note" aria-label="Nota del trayecto"><h4>Nota</h4><p>{item.notes}</p></section>{/if}
							<div class="actions"><button class="button button-secondary" disabled={busy || saving || !online} onclick={() => open(item)} aria-label={`Editar trayecto de ${item.from_label ?? 'origen sin indicar'} a ${item.to_label ?? 'destino sin indicar'}`}>Editar</button><button class="button button-quiet danger-action" disabled={busy || saving || !online} onclick={() => remove(item)} aria-label={`Eliminar trayecto de ${item.from_label ?? 'origen sin indicar'} a ${item.to_label ?? 'destino sin indicar'}`}>Eliminar</button></div>
						</div>
					</details>
					{#if item.tags?.length}<ul class="tag-list journey-tags" aria-label="Etiquetas del trayecto">{#each item.tags as tag}<li><button type="button" class="tag-chip tag-filter" aria-pressed={appliedTag === tag} aria-label={`Filtrar por etiqueta ${tag}`} onclick={() => filterByTag(tag)}>{tag}</button></li>{/each}</ul>{/if}
					{#if item.notes}<span class="note-indicator">Incluye una nota</span>{/if}
				</div>
			</article>
		{/each}
		</div>
		{#if page.total > pageSize}<nav class="pagination" aria-label="Páginas de trayectos manuales"><button class="button button-secondary" disabled={busy || saving || offset === 0} onclick={() => offset = Math.max(0, offset - pageSize)}>Anteriores</button><span>{offset + 1}–{Math.min(offset + pageSize, page.total)} de {count(page.total)}</span><button class="button button-secondary" disabled={busy || saving || offset + pageSize >= page.total} onclick={() => offset += pageSize}>Siguientes</button></nav>{/if}
	{/if}
	<div class="report-heading"><div><span class="eyebrow">RESUMEN</span><h3 bind:this={reportHeading} tabindex="-1">Informe de tus registros</h3></div><div class="field period-field"><label for="journey-period">Periodo</label><select id="journey-period" bind:value={days} disabled={busy || saving || exporting}><option value={30}>Últimos 30 días</option><option value={90}>Últimos 90 días</option><option value={365}>Últimos 365 días</option></select></div></div>
	{#if report && reportRevision === revision && report.period.days === days && total}
		<div class="report-context"><span class="report-period">{days} días · {report.period.timezone}</span><span class="coverage-note">Cobertura desconocida</span></div>
		<p class="muted">Se incluyen trayectos completos por su fecha de inicio. Este resumen reúne solo registros manuales que has guardado; no confirma que estén incluidos todos tus viajes.</p>
		<dl class="report-totals"><div><dt>Registros</dt><dd>{count(total.count)}</dd></div><div><dt>Distancia conocida</dt><dd>{total.known_distance_count ? `${number(total.distance_km)} km` : 'Sin dato'}</dd></div><div><dt>Energía declarada</dt><dd>{total.known_energy_count ? `${number(total.energy_kwh)} kWh` : 'Sin dato'}</dd></div></dl>
		<p>{count(total.known_distance_count)} con distancia · {count(total.known_energy_count)} con energía. Consumo: {total.consumption_kwh_100km === null ? 'sin datos suficientes' : `${number(total.consumption_kwh_100km)} kWh/100 km`}.</p>
		<p class="muted">El consumo usa sólo {count(total.paired_count)} {total.paired_count === 1 ? 'registro' : 'registros'} con ambos datos y distancia positiva ({number(total.paired_distance_km)} km / {number(total.paired_energy_kwh)} kWh). La energía sin distancia no entra en ese cálculo. Las cifras se muestran redondeadas.</p>
		<details><summary>Detalle mensual</summary><div class="months">{#each report.months as month (month.month_key)}<div><strong>{month.month_key}</strong><span>{count(month.count)} registros</span><span>{month.known_distance_count ? `${number(month.distance_km)} km` : 'Sin distancia'}</span><span>{month.known_energy_count ? `${number(month.energy_kwh)} kWh` : 'Sin energía'}</span></div>{/each}</div></details>
		<div class="document-actions">
			{#if native}
				<button class="button button-secondary" bind:this={exportButton} disabled={busy || saving || exporting || loading || !online} onclick={exportReport}>{exporting ? 'Preparando archivo…' : 'Guardar informe JSON'}</button>
				<p class="muted">CSV y vista imprimible están disponibles al abrir Pulse como PWA en el navegador. Este archivo JSON contiene los agregados del periodo, no los trayectos detallados.</p>
			{:else}
				<a class="button button-secondary" href={`/api/journeys/document?days=${days}&format=csv`} download={`pulse-trayectos-${days}-dias.csv`}>Descargar CSV</a>
				<a class="button button-primary" href={`/api/journeys/document?days=${days}&format=print`} target="_blank" rel="noopener">Abrir informe imprimible</a>
				<p class="muted">En la vista imprimible, usa Imprimir en el navegador y elige «Guardar como PDF».</p>
				<button class="button button-quiet" bind:this={exportButton} disabled={busy || saving || exporting || loading || !online} onclick={exportReport}>Descargar resumen JSON</button>
			{/if}
		</div>
	{/if}
	{#if exportMessage}<p role={exportError ? 'alert' : 'status'}>{exportMessage}</p>{/if}
</section>

<style>
	.manual-journeys { display: grid; gap: 24px; min-width: 0; }
	h2, h3, h4, p { margin: 0; } h2 { font-size: clamp(1.2rem, 2vw, 1.45rem); letter-spacing: -.025em; } h3 { font-size: 1.05rem; letter-spacing: -.015em; }
	p, label, dt, dd { font-size: .875rem; line-height: 1.6; } p, dt, .muted { color: var(--muted); }
	.panel-header, .list-heading, .report-heading { display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap; }
	.panel-copy { display: grid; gap: 6px; flex: 1 1 260px; }
	.panel-copy > p { max-width: 55ch; }
	.eyebrow { color: var(--forest); font-size: .6875rem; font-weight: 750; letter-spacing: .09em; }
	.journey-form { padding: clamp(18px, 3vw, 28px); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--surface-soft); display: grid; gap: 18px; }
	.form-intro { display: flex; justify-content: space-between; align-items: end; flex-wrap: wrap; gap: 12px 22px; }
	.form-intro > div { display: grid; gap: 5px; }
	.form-intro > p { max-width: 44ch; font-size: .8125rem; }
	.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
	label { display: grid; gap: 7px; min-width: 0; color: var(--muted-strong); font-weight: 650; }
	.optional { color: var(--muted); font-size: .75rem; font-weight: 450; }
	input, select { min-height: 48px; min-width: 0; width: 100%; }
	.field-hint { margin: -2px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.field small { color: var(--danger); font-size: .8125rem; font-weight: 550; } .form-error { padding: 12px 14px; border-left: 3px solid var(--danger); background: var(--danger-soft); color: var(--danger); line-height: 1.6; overflow-wrap: anywhere; }
	.form-error ul { margin: 8px 0 0; padding-left: 20px; } .form-error a { color: inherit; }
	.notes-field textarea { min-height: 6rem; resize: vertical; }
	.tag-input-row { display: flex; align-items: stretch; gap: 8px; }
	.tag-input-row input { flex: 1 1 auto; }
	.tag-input-row .button { flex: 0 0 auto; }
	.tag-list { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 0; padding: 0; list-style: none; }
	.tag-list li { display: inline-flex; align-items: center; }
	.tag-chip { display: inline-flex; min-height: 36px; align-items: center; padding: 6px 11px; border: 1px solid var(--line); border-radius: 999px; background: var(--forest-soft); color: var(--muted-strong); font-size: .75rem; font-weight: 650; line-height: 1.2; overflow-wrap: anywhere; }
	.tag-remove { display: grid; width: 48px; min-height: 48px; place-items: center; margin-left: -5px; border: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 1.1rem; }
	.tag-remove:hover:not(:disabled) { color: var(--danger); }
	.stops { border: 0; margin: 0; padding: 0; min-width: 0; display: grid; gap: 14px; }
	.stops legend { padding: 0 0 7px; color: var(--muted-strong); font-size: .9375rem; font-weight: 680; }
	.stop-row { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); padding-bottom: 12px; border-bottom: 1px solid var(--line); }
	.stop-row button { grid-column: 1 / -1; justify-self: start; }
	.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; } .button { min-height: 48px; white-space: normal; }
	.journey-filters { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(160px, .8fr) auto; align-items: end; gap: 12px; padding: 16px 0; border-block: 1px solid var(--line); }
	.journey-filters .filter-search, .journey-filters .filter-tag { display: grid; min-width: 0; gap: 6px; }
	.journey-filters label { font-size: .8125rem; }
	.journey-filters input { width: 100%; min-height: 48px; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface-raised); color: var(--ink); }
	.journey-filters small { color: var(--muted); font-size: .75rem; }
	.filter-actions { display: flex; align-items: center; gap: 4px; }
	.journey-filters > .form-error { grid-column: 1 / -1; }
	.list-heading { margin-top: -8px; }
	.list-heading > p { color: var(--muted-strong); }
	.list-heading strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.empty-copy { max-width: 60ch; padding: 18px 0; }
	.journey-entry.evolution-item { position: relative; display: grid; grid-template-columns: 17px minmax(0, 1fr); gap: 13px; min-width: 0; padding: 13px 0; }
	.journey-entry:not(:last-of-type)::before { position: absolute; top: 27px; bottom: -14px; left: 7px; width: 1px; background: var(--line-strong); content: ''; }
	.timeline-pin { position: relative; z-index: 1; width: 13px; height: 13px; margin-top: 4px; border: 3px solid var(--surface); border-radius: 50%; background: var(--forest); box-shadow: 0 0 0 1px var(--forest); }
	.journey-entry-body { display: grid; min-width: 0; gap: 8px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
	.journey-details { min-width: 0; }
	.journey-details summary { display: grid; min-height: 48px; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 3px 14px; list-style: none; cursor: pointer; }
	.journey-details summary::-webkit-details-marker { display: none; }
	.journey-details summary time { color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.journey-details summary strong { min-width: 0; color: var(--ink); font-size: .975rem; line-height: 1.35; overflow-wrap: anywhere; }
	.journey-details summary strong span { color: var(--forest); }
	.journey-brief { grid-column: 1; color: var(--muted); font-size: .8125rem; font-variant-numeric: tabular-nums; }
	.details-hint { grid-column: 2; grid-row: 1 / span 3; align-self: center; color: var(--forest); font-size: .75rem; font-weight: 650; white-space: nowrap; }
	.details-hint::after { display: inline-block; padding-left: 6px; content: '+'; }
	.journey-details[open] .details-hint::after { content: '−'; }
	.journey-expanded { display: grid; gap: 12px; padding: 15px 0 4px; animation: reveal .18s ease-out; }
	.journey-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 13px 18px; margin: 0; }
	.journey-metrics > div { min-width: 0; }
	.journey-metrics dt { color: var(--muted); font-size: .75rem; }
	.journey-metrics dd { margin: 2px 0 0; color: var(--muted-strong); font-size: .875rem; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.journey-period, .note-indicator { color: var(--muted); font-size: .75rem; }
	.journey-note { display: grid; gap: 4px; padding: 12px 14px; border-left: 2px solid var(--mint); background: var(--surface-soft); }
	.journey-note h4 { color: var(--muted-strong); font-size: .75rem; font-weight: 700; }
	.journey-note p { color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere; }
	.journey-tags { gap: 5px; }
	.tag-filter { min-height: 48px; padding: 5px 11px; font: inherit; cursor: pointer; }
	.tag-filter:hover { border-color: var(--forest); background: var(--forest-soft); }
	.note-indicator { font-style: italic; }
	.danger-action { color: var(--danger); }
	.pagination { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 4px; }
	.pagination > span { color: var(--muted); font-size: .8125rem; font-variant-numeric: tabular-nums; text-align: center; }
	.report-heading { padding-top: 22px; border-top: 1px solid var(--line); }
	.report-heading > div:first-child { display: grid; gap: 4px; }
	.period-field { min-width: 180px; }
	.period-field select { min-height: 48px; }
	.report-context { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
	.report-period, .coverage-note { display: inline-flex; min-height: 32px; align-items: center; padding: 4px 10px; border-radius: 999px; background: var(--surface-soft); color: var(--muted-strong); font-size: .75rem; font-weight: 650; }
	.coverage-note { background: var(--accent-soft); color: var(--muted-strong); }
	.report-totals { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 20px; margin: 0; }
	.report-totals > div { min-width: 0; padding-top: 12px; border-top: 1px solid var(--line-strong); }
	.report-totals dt { color: var(--muted); font-size: .75rem; }
	.report-totals dd { margin: 3px 0 0; color: var(--ink); font-size: 1.2rem; font-weight: 700; font-variant-numeric: tabular-nums; }
	summary { min-height: 48px; align-content: center; cursor: pointer; color: var(--forest); font-size: .875rem; font-weight: 650; }
	.months { display: grid; gap: 0; padding: 6px 0; }
	.months > div { display: grid; grid-template-columns: minmax(100px, 1fr) repeat(3, auto); align-items: baseline; gap: 8px 18px; padding: 10px 0; border-bottom: 1px solid var(--line); color: var(--muted); font-size: .8125rem; }
	.months > div strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.months > div span:not(:first-child) { text-align: right; }
	.document-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
	.document-actions > p { flex: 1 1 100%; max-width: 68ch; font-size: .8125rem; }
	.read-error { display: grid; gap: 12px; }
	@keyframes reveal { from { opacity: .7; transform: translateY(-2px); } to { opacity: 1; transform: translateY(0); } }
	@media (prefers-reduced-motion: reduce) { .journey-expanded { animation: none; } }
	@media (max-width: 760px) { .journey-filters { grid-template-columns: minmax(0, 1fr) minmax(0, .8fr); } .filter-actions { grid-column: 1 / -1; } }
	@media (max-width: 580px) { .form-grid, .stop-row { grid-template-columns: minmax(0, 1fr); } .journey-form { padding: 17px 14px; } .journey-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .report-totals { grid-template-columns: minmax(0, 1fr); gap: 7px; } .report-totals > div { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; } .report-totals dd { font-size: 1rem; text-align: right; } .journey-filters { grid-template-columns: minmax(0, 1fr); } .filter-actions { grid-column: auto; } .journey-details summary { grid-template-columns: minmax(0, 1fr); gap: 4px; } .details-hint { grid-column: 1; grid-row: auto; justify-self: start; } .months > div { grid-template-columns: repeat(2, minmax(0, 1fr)); } .months > div span:not(:first-child) { text-align: left; } }
	@media (max-width: 380px) { .tag-input-row { flex-wrap: wrap; } .tag-input-row .button { width: 100%; } .journey-metrics { grid-template-columns: minmax(0, 1fr); } .pagination { flex-wrap: wrap; } .pagination > span { order: -1; width: 100%; } }
</style>
