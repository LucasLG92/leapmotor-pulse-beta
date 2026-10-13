<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { ApiError, getMaintenancePlans } from './api';
	import Glyph from './Glyph.svelte';
	import type { MaintenancePlan, MaintenancePlanInput, MaintenancePlanPage, PageProps } from './types';

	let { busy, online, revision, drafts, mutate, confirmAction, onSessionExpired }:
		Pick<PageProps, 'busy' | 'online' | 'revision' | 'drafts' | 'mutate' | 'confirmAction' | 'onSessionExpired'> = $props();

	type PlanForm = { title: string; intervalMonths: string; intervalKm: string; lastDate: string; lastOdometer: string; notes: string };
	type CompletionForm = { date: string; odometer: string; cost: string };
	type FormMode = 'plan' | 'completion' | null;
	const PAGE_SIZE = 50;
	const MAX_PLANS = 1000;
	const formatNumber = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 });
	const formatCost = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
	const dateFormat = new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeZone: 'UTC' });
	const emptyPlan = (): PlanForm => ({ title: '', intervalMonths: '', intervalKm: '', lastDate: '', lastOdometer: '', notes: '' });
	const emptyCompletion = (): CompletionForm => ({ date: '', odometer: '', cost: '' });
	const integerPattern = /^(0|[1-9]\d*)$/;

	let page = $state<MaintenancePlanPage | null>(null);
	let loading = $state(false), readError = $state(''), actionError = $state('');
	let manualOdometer = $state(''), appliedOdometer = $state<number | undefined>();
	let offset = $state(0), mode = $state<FormMode>(null), editing = $state<MaintenancePlan | null>(null);
	let completionPlan = $state<MaintenancePlan | null>(null);
	let form = $state<PlanForm>(emptyPlan()), completion = $state<CompletionForm>(emptyCompletion());
	let baseline = '';
	let errors = $state<Record<string, string>>({}), saveError = $state(''), saving = $state(false);
	let sent = $state(false), sentAttempt = '';
	let createRequestId = $state<string | null>(null);
	let mounted = false, readGeneration = 0, formGeneration = 0, observedReadKey = '';
	let readController: AbortController | null = null;
	let errorSummary = $state<HTMLDivElement | undefined>();

	function snapshot(): string { return mode === 'completion' ? JSON.stringify(completion) : JSON.stringify(form); }
	function dirty(): boolean { return mode !== null && (sent || snapshot() !== baseline); }
	function nullableInteger(raw: string, min: number, max: number): number | null | 'invalid' {
		const clean = raw.trim();
		if (!clean) return null;
		if (!integerPattern.test(clean)) return 'invalid';
		const number = Number(clean);
		return Number.isSafeInteger(number) && number >= min && number <= max ? number : 'invalid';
	}

	onMount(() => {
		mounted = true;
		observedReadKey = `${revision}:${online}:${offset}:${appliedOdometer ?? ''}`;
		void load();
		return drafts.register('maintenance-plan', dirty, () => sent);
	});
	onDestroy(() => { mounted = false; readGeneration++; formGeneration++; readController?.abort(); });
	$effect(() => {
		const key = { revision, online, offset, appliedOdometer };
		untrack(() => {
			if (!mounted) return;
			const next = `${key.revision}:${key.online}:${key.offset}:${key.appliedOdometer ?? ''}`;
			if (next === observedReadKey) return;
			observedReadKey = next;
			if (!key.online) {
				readGeneration++; readController?.abort(); page = null; loading = false;
				readError = 'Sin conexión. Los planes solo se consultan en el servidor y no se guardan sin conexión.';
			} else void load();
		});
	});

	async function load() {
		if (!mounted || !online) return;
		const request = ++readGeneration, currentRevision = revision, currentOffset = offset;
		readController?.abort(); readController = new AbortController();
		const signal = readController.signal;
		loading = true; readError = ''; actionError = '';
		try {
			const next = await getMaintenancePlans(appliedOdometer, PAGE_SIZE, currentOffset, signal);
			if (!mounted || request !== readGeneration || signal.aborted || currentRevision !== revision || !online) return;
			if (currentOffset > 0 && !next.items.length && currentOffset >= next.total) {
				offset = Math.max(0, Math.floor(Math.max(0, next.total - 1) / PAGE_SIZE) * PAGE_SIZE); return;
			}
			page = next;
		} catch (error) {
			if (!mounted || request !== readGeneration || signal.aborted) return;
			if (error instanceof ApiError && error.status === 401) { onSessionExpired(); return; }
			readError = error instanceof ApiError ? error.message : 'No se pudieron cargar los planes. Inténtalo de nuevo.';
		} finally { if (mounted && request === readGeneration) loading = false; }
	}

	function applyOdometer(event: SubmitEvent) {
		event.preventDefault();
		const parsed = nullableInteger(manualOdometer, 0, 2_000_000);
		if (parsed === 'invalid') { actionError = 'Escribe un odómetro entero entre 0 y 2.000.000 km, o deja el campo vacío.'; return; }
		actionError = '';
		appliedOdometer = parsed === null ? undefined : parsed;
		offset = 0;
	}

	async function approveDiscard(): Promise<boolean> {
		if (!dirty()) return true;
		if (sent) return confirmAction('Resultado sin confirmar', 'El servidor pudo haber recibido esta operación. Actualiza la lista antes de descartar el formulario; si no aparece, puedes decidir expresamente si quieres reenviar exactamente los mismos datos.', 'Descartar formulario');
		return confirmAction('Cambios sin guardar', 'Se descartarán los cambios de este formulario.', 'Descartar formulario');
	}

	function resetForm() {
		formGeneration++; mode = null; editing = null; completionPlan = null;
		form = emptyPlan(); completion = emptyCompletion(); errors = {}; saveError = '';
		baseline = ''; sent = false; sentAttempt = ''; createRequestId = null;
	}

	async function openPlan(item?: MaintenancePlan) {
		if (busy || saving) return;
		const generation = formGeneration;
		if (!await approveDiscard() || !mounted || busy || saving || generation !== formGeneration) return;
		resetForm(); mode = 'plan'; editing = item ?? null;
		if (item) {
			form = { title: item.title, intervalMonths: item.interval_months === null ? '' : String(item.interval_months),
				intervalKm: item.interval_km === null ? '' : String(item.interval_km), lastDate: item.last_date ?? '',
				lastOdometer: item.last_odometer_km === null ? '' : String(item.last_odometer_km), notes: item.notes };
		}
		baseline = snapshot();
		await tick(); if (mounted) document.getElementById('maintenance-plan-title')?.focus();
	}

	async function openCompletion(item: MaintenancePlan) {
		if (busy || saving) return;
		const generation = formGeneration;
		if (!await approveDiscard() || !mounted || busy || saving || generation !== formGeneration) return;
		resetForm(); mode = 'completion'; completionPlan = item; completion = emptyCompletion(); baseline = snapshot();
		await tick(); if (mounted) document.getElementById('maintenance-plan-performed-date')?.focus();
	}

	async function closeForm() {
		if (busy || saving) return;
		const generation = formGeneration;
		if (await approveDiscard() && mounted && !busy && !saving && generation === formGeneration) resetForm();
	}

	function errorTargetId(key: string): string {
		if (mode === 'completion') return `maintenance-plan-${key === 'date' ? 'performed-date' : key === 'odometer' ? 'odometer-completion' : key}`;
		return `maintenance-plan-${key}`;
	}

	function validatePlan(): MaintenancePlanInput | null {
		const next: Record<string, string> = {};
		const title = form.title.trim(), notes = form.notes.trim();
		if (!title) next.title = 'Escribe un nombre para la tarea.';
		else if (title.length > 120) next.title = 'El nombre admite hasta 120 caracteres.';
		if (form.notes.length > 2000) next.notes = 'Las notas admiten hasta 2.000 caracteres.';
		const months = nullableInteger(form.intervalMonths, 1, 120);
		const km = nullableInteger(form.intervalKm, 1, 200_000);
		const lastKm = nullableInteger(form.lastOdometer, 0, 2_000_000);
		if (months === 'invalid') next.intervalMonths = 'Indica meses enteros entre 1 y 120.';
		if (km === 'invalid') next.intervalKm = 'Indica kilómetros enteros entre 1 y 200.000.';
		if (lastKm === 'invalid') next.lastOdometer = 'Indica una lectura entera entre 0 y 2.000.000 km.';
		if (months === null && km === null) next.intervalMonths = 'Configura al menos un intervalo, por fecha o por kilómetros.';
		if (km !== null && km !== 'invalid' && lastKm === null) next.lastOdometer = 'Para calcular el intervalo por kilómetros, indica una lectura base manual.';
		if (form.lastDate && (!/^\d{4}-\d{2}-\d{2}$/.test(form.lastDate) || !Number.isFinite(Date.parse(`${form.lastDate}T00:00:00Z`)) || new Date(`${form.lastDate}T00:00:00Z`).toISOString().slice(0, 10) !== form.lastDate)) next.lastDate = 'Indica una fecha válida.';
		else if (form.lastDate && form.lastDate > new Date().toISOString().slice(0, 10)) next.lastDate = 'La fecha base no puede estar en el futuro.';
		errors = next;
		if (Object.keys(next).length) return null;
		return { title, interval_months: months as number | null, interval_km: km as number | null,
			last_date: form.lastDate || null, last_odometer_km: lastKm as number | null, notes: form.notes };
	}

	function validateCompletion(): { performed_date: string; odometer_km: number | null; cost_eur: number | null } | null {
		const next: Record<string, string> = {};
		const date = completion.date;
		if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(`${date}T00:00:00Z`)) || new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) next.date = 'Indica una fecha válida de realización.';
		else if (date > new Date().toISOString().slice(0, 10)) next.date = 'La fecha de realización no puede estar en el futuro.';
		const odometer = nullableInteger(completion.odometer, 0, 2_000_000);
		if (odometer === 'invalid') next.odometer = 'Indica una lectura entera entre 0 y 2.000.000 km.';
		if (completionPlan?.interval_km !== null && completionPlan?.interval_km !== undefined && odometer === null) next.odometer = 'Este plan por kilómetros requiere una lectura de odómetro manual.';
		if (odometer !== null && odometer !== 'invalid' && completionPlan?.last_odometer_km !== null && completionPlan?.last_odometer_km !== undefined && odometer < completionPlan.last_odometer_km) next.odometer = 'La lectura no puede ser menor que la base anterior.';
		if (date && completionPlan?.last_date && date < completionPlan.last_date) next.date = 'La fecha no puede preceder a la base anterior.';
		let cost: number | null = null;
		if (completion.cost.trim()) {
			const normalized = completion.cost.trim().replace(',', '.');
			const parsed = Number(normalized);
			if (!/^(?:\d+(?:\.\d{1,2})?|\.\d{1,2})$/.test(normalized) || !Number.isFinite(parsed) || parsed < 0 || parsed > 100_000) next.cost = 'Indica un coste entre 0 y 100.000 €, con hasta dos decimales.';
			else cost = parsed;
		}
		errors = next;
		return Object.keys(next).length ? null : { performed_date: date, odometer_km: odometer as number | null, cost_eur: cost };
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!mounted || busy || saving || !online || !mode) return;
		const generation = formGeneration, originalMode = mode, originalSnapshot = snapshot();
		const values = mode === 'plan' ? validatePlan() : validateCompletion();
		if (!values) { await tick(); if (mounted) errorSummary?.focus(); return; }
		const edit = mode === 'plan' ? editing : completionPlan;
		if (mode === 'plan' && !editing && !createRequestId) {
			createRequestId = crypto.randomUUID().replace(/-/g, '').toLowerCase();
		}
		const payload = mode === 'completion'
			? { ...(values as ReturnType<typeof validateCompletion> & object), revision: edit!.revision }
			: editing ? { ...(values as MaintenancePlanInput), revision: editing.revision }
				: { ...(values as MaintenancePlanInput), request_id: createRequestId! } as MaintenancePlanInput & { request_id: string };
		const attempt = JSON.stringify({ mode, id: edit?.id ?? null, payload });
		if (sent && sentAttempt !== attempt) { saveError = 'El intento anterior sigue sin confirmarse. Actualiza la lista antes de cambiar los datos.'; return; }
		if (sent && sentAttempt === attempt && !await confirmAction('Reenviar operación sin confirmar', 'La primera solicitud pudo haberse aplicado. Actualiza la lista para comprobarlo; solo si decides continuar se enviarán los mismos datos otra vez.', 'Reenviar mismos datos')) return;
		if (!mounted || generation !== formGeneration || originalMode !== mode || originalSnapshot !== snapshot()) return;
		saving = true; saveError = '';
		try {
			sent = true; sentAttempt = attempt;
			const saved = mode === 'completion'
				? await mutate(`/api/maintenance-plans/${edit!.id}/complete`, 'POST', payload, 'El mantenimiento realizado se ha registrado.')
				: await mutate(edit ? `/api/maintenance-plans/${edit.id}` : '/api/maintenance-plans', editing ? 'PUT' : 'POST', payload, editing ? 'El plan de mantenimiento se ha actualizado.' : 'El plan de mantenimiento se ha creado.');
			if (!mounted || generation !== formGeneration) return;
			if (saved) { resetForm(); if (offset) offset = 0; else void load(); }
			else saveError = 'El resultado no está confirmado; el servidor pudo haber guardado el plan. Conservamos los datos y el identificador del intento. Actualiza la lista antes de decidir si reintentas.';
		} catch {
			if (mounted && generation === formGeneration) saveError = 'No se pudo confirmar el resultado; el servidor pudo haber guardado el plan. Conservamos los datos y el identificador del intento. Actualiza la lista antes de decidir si reenvías.';
		} finally { if (mounted) saving = false; }
	}

	async function remove(item: MaintenancePlan) {
		if (busy || saving || !online) return;
		const generation = formGeneration;
		const accepted = await confirmAction('Eliminar plan de mantenimiento', `Se archivará «${item.title}» y dejará de aparecer en la lista. Sus registros asociados permanecen vinculados al plan.`, 'Eliminar plan');
		if (!accepted || !mounted || busy || saving || !online || generation !== formGeneration) return;
		actionError = ''; saving = true;
		try {
			const removed = await mutate(`/api/maintenance-plans/${item.id}`, 'DELETE', { revision: item.revision }, 'El plan de mantenimiento se ha eliminado.');
			if (!mounted) return;
			if (removed) { if (editing?.id === item.id || completionPlan?.id === item.id) resetForm(); void load(); }
			else actionError = 'No se pudo confirmar la eliminación. Actualiza la lista antes de volver a intentarlo.';
		} catch { if (mounted) actionError = 'No se pudo confirmar la eliminación. Actualiza la lista antes de volver a intentarlo.'; }
		finally { if (mounted) saving = false; }
	}

	function dateText(value: string | null): string { return value ? dateFormat.format(new Date(`${value}T00:00:00Z`)) : 'Sin fecha base'; }
	function money(value: number | null): string { return value === null ? 'Sin dato de coste' : formatCost.format(value); }
	function stateText(value: string): string {
		return ({ due: 'Vencido', upcoming: 'Dentro del intervalo', unknown: 'Sin datos suficientes', not_configured: 'No configurado' } as Record<string, string>)[value] ?? 'Estado no reconocido';
	}
	function planStatusText(plan: MaintenancePlan): string {
		return plan.status === 'due' ? 'Revisión pendiente' : plan.status === 'upcoming' ? 'En seguimiento' : 'Cálculo incompleto';
	}
	function reasonText(reason: string): string {
		return ({ date_due: 'La fecha estimada ya llegó.', date_unknown: 'Falta una fecha base para calcular el intervalo.', km_due: 'La lectura manual alcanza el intervalo.', km_unknown: 'Añade una lectura manual para evaluar el intervalo por kilómetros.' } as Record<string, string>)[reason] ?? 'Hay información de mantenimiento pendiente de revisar.';
	}
	let pageStart = $derived(page ? Math.min(page.total, offset + 1) : 0);
	let pageEnd = $derived(page ? Math.min(page.total, offset + page.items.length) : 0);
</script>

<section class="maintenance-panel workspace-panel" aria-labelledby="maintenance-plans-heading">
	<header class="maintenance-header premium-heading">
		<span class="maintenance-mark" aria-hidden="true"><Glyph name="settings" size={18} /></span><div class="maintenance-heading-copy"><p class="eyebrow">Mantenimiento introducido por ti</p><h2 id="maintenance-plans-heading">Planes de mantenimiento</h2><p>Organiza revisiones por fecha y lecturas que indiques. Pulse no lee el odómetro del vehículo.</p></div>
		<button class="button button-primary" type="button" disabled={busy || saving || !online || (page?.total ?? 0) >= MAX_PLANS} onclick={() => openPlan()}>
			<span aria-hidden="true">＋</span> Añadir plan
		</button>
	</header>

	<div class="maintenance-tools">
		<form class="odometer-form" onsubmit={applyOdometer}>
			<label for="maintenance-plan-odometer">Lectura actual manual <span>km</span></label>
			<div class="odometer-controls"><input id="maintenance-plan-odometer" type="text" inputmode="numeric" placeholder="Sin lectura" bind:value={manualOdometer} disabled={busy || saving || !online} /><button class="button button-secondary" type="submit" disabled={busy || saving || !online}>Aplicar km</button></div>
			<p>{appliedOdometer === undefined ? 'Sin lectura enviada: los estados por kilómetros quedan desconocidos.' : `Evaluación con ${formatNumber.format(appliedOdometer)} km, indicada manualmente.`}</p>
		</form>
		<div class="quota" aria-label={`Planes activos: ${page?.total ?? 'sin consultar'}`}><span>Planes activos</span><strong>{page ? formatNumber.format(page.total) : '—'}</strong><details class="quota-details"><summary>Sobre el límite</summary><small>Tope de cuenta: {formatNumber.format(MAX_PLANS)}, incluidos los planes archivados.</small></details></div>
	</div>

	{#if actionError}<div class="maintenance-error" role="alert"><span>{actionError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></div>{/if}
	{#if readError}<div class="maintenance-error" role="alert"><span>{readError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Reintentar consulta</button></div>{/if}
	{#if loading}<p class="maintenance-state" role="status">Consultando tus planes…</p>{/if}
	<form class="maintenance-form" hidden={mode === null} onsubmit={save} novalidate aria-labelledby="maintenance-plan-form-heading">
			<div class="form-title"><div><p class="eyebrow">{mode === 'completion' ? 'Registro manual' : editing ? 'Edición manual' : 'Nuevo registro'}</p><h3 id="maintenance-plan-form-heading">{mode === 'completion' ? `Registrar «${completionPlan?.title ?? 'mantenimiento'}» como realizado` : editing ? 'Editar plan' : 'Crear plan'}</h3></div><button class="icon-button" type="button" aria-label="Cerrar formulario" onclick={closeForm} disabled={busy || saving}>×</button></div>
			{#if Object.keys(errors).length}<div class="form-errors" role="alert" tabindex="-1" bind:this={errorSummary}><strong>Revisa estos datos</strong><ul>{#each Object.entries(errors) as [key, message]}<li><a href={`#${errorTargetId(key)}`}>{message}</a></li>{/each}</ul></div>{/if}
			{#if saveError}<p class="maintenance-error" role="alert" aria-atomic="true">{saveError}<button type="button" class="text-button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></p>{/if}
			{#if mode === 'plan'}
				<div class="form-grid">
					<div class="field full"><label for="maintenance-plan-title">Nombre de la tarea</label><input id="maintenance-plan-title" maxlength="120" required bind:value={form.title} disabled={busy || saving || sent} aria-invalid={!!errors.title} />{#if errors.title}<small>{errors.title}</small>{/if}</div>
					<fieldset class="interval-field"><legend>Repetir por calendario</legend><label for="maintenance-plan-intervalMonths">Cada</label><div class="input-with-unit"><input id="maintenance-plan-intervalMonths" type="text" inputmode="numeric" placeholder="Sin intervalo" bind:value={form.intervalMonths} disabled={busy || saving || sent} aria-invalid={!!errors.intervalMonths} /><span>meses</span></div>{#if errors.intervalMonths}<small>{errors.intervalMonths}</small>{/if}</fieldset>
					<fieldset class="interval-field"><legend>Repetir por uso</legend><label for="maintenance-plan-intervalKm">Cada</label><div class="input-with-unit"><input id="maintenance-plan-intervalKm" type="text" inputmode="numeric" placeholder="Sin intervalo" bind:value={form.intervalKm} disabled={busy || saving || sent} aria-invalid={!!errors.intervalKm} /><span>km</span></div>{#if errors.intervalKm}<small>{errors.intervalKm}</small>{/if}</fieldset>
					<div class="field"><label for="maintenance-plan-lastDate">Fecha de la última realización <span>(opcional)</span></label><input id="maintenance-plan-lastDate" type="date" bind:value={form.lastDate} disabled={busy || saving || sent} aria-invalid={!!errors.lastDate} />{#if errors.lastDate}<small>{errors.lastDate}</small>{/if}</div>
					<div class="field"><label for="maintenance-plan-lastOdometer">Odómetro de referencia <span>(solo lectura manual)</span></label><div class="input-with-unit"><input id="maintenance-plan-lastOdometer" type="text" inputmode="numeric" placeholder="Sin lectura" bind:value={form.lastOdometer} disabled={busy || saving || sent} aria-invalid={!!errors.lastOdometer} /><span>km</span></div>{#if errors.lastOdometer}<small>{errors.lastOdometer}</small>{/if}</div>
					<div class="field full"><label for="maintenance-plan-notes">Notas <span>(opcional)</span></label><textarea id="maintenance-plan-notes" maxlength="2000" rows="3" bind:value={form.notes} disabled={busy || saving || sent} aria-invalid={!!errors.notes}></textarea><small>{form.notes.length} / 2.000</small>{#if errors.notes}<small>{errors.notes}</small>{/if}</div>
				</div>
				<p class="form-hint">Si falta una fecha base o un odómetro, el cálculo correspondiente quedará como desconocido. No se estimará una lectura del vehículo.</p>
			{:else}
				<div class="form-grid">
					<div class="field"><label for="maintenance-plan-performed-date">Fecha de realización</label><input id="maintenance-plan-performed-date" type="date" required bind:value={completion.date} disabled={busy || saving || sent} aria-invalid={!!errors.date} />{#if errors.date}<small>{errors.date}</small>{/if}</div>
					<div class="field"><label for="maintenance-plan-odometer-completion">Odómetro de la realización <span>{completionPlan?.interval_km ? '(necesario para este plan)' : '(opcional)'}</span></label><div class="input-with-unit"><input id="maintenance-plan-odometer-completion" type="text" inputmode="numeric" placeholder="Sin lectura" bind:value={completion.odometer} disabled={busy || saving || sent} aria-invalid={!!errors.odometer} /><span>km</span></div>{#if errors.odometer}<small>{errors.odometer}</small>{/if}</div>
					<div class="field"><label for="maintenance-plan-cost">Coste <span>(opcional)</span></label><div class="input-with-unit"><input id="maintenance-plan-cost" type="text" inputmode="decimal" placeholder="Sin dato" bind:value={completion.cost} disabled={busy || saving || sent} aria-invalid={!!errors.cost} /><span>€</span></div>{#if errors.cost}<small>{errors.cost}</small>{/if}</div>
				</div>
				<p class="form-hint">Al registrar el trabajo, esta fecha y, si se indica, el kilometraje pasan a ser la nueva base del intervalo.</p>
			{/if}
			<div class="form-actions"><button class="button button-secondary" type="button" onclick={closeForm} disabled={busy || saving}>Cancelar</button><button class="button button-primary" type="submit" disabled={busy || saving || !online}>{saving ? 'Guardando…' : sent ? 'Revisar y reintentar' : mode === 'completion' ? 'Registrar realización' : editing ? 'Guardar cambios' : 'Crear plan'}</button></div>
	</form>

	{#if page && !loading}
		{#if page.items.length}
			<ol class="plan-list evolution-list" aria-label="Planes de mantenimiento">
				{#each page.items as plan (plan.id)}
					<li class="evolution-item" class:plan-due={plan.status === 'due'} class:plan-unknown={plan.status === 'unknown'}>
						<article class="plan-card">
							<div class="plan-accent" aria-hidden="true"></div>
							<div class="plan-body">
								<div class="plan-top"><div><p class="plan-label">{planStatusText(plan)}</p><h3>{plan.title}</h3></div><span class="status-chip" class:chip-due={plan.status === 'due'} class:chip-unknown={plan.status === 'unknown'}>{plan.status === 'due' ? 'Pendiente' : plan.status === 'unknown' ? 'Incompleto' : 'En seguimiento'}</span></div>
								<div class="plan-detail-grid">
									<div><span>Calendario</span><strong>{plan.interval_months === null ? 'No configurado' : `Cada ${plan.interval_months} meses`}</strong><small>{plan.due_date ? `Siguiente fecha estimada: ${dateText(plan.due_date)}` : plan.interval_months !== null ? 'Falta una fecha base' : 'Sin intervalo por fecha'}</small></div>
									<div><span>Kilometraje</span><strong>{plan.interval_km === null ? 'No configurado' : `Cada ${formatNumber.format(plan.interval_km)} km`}</strong><small>{plan.due_odometer_km === null ? plan.interval_km !== null ? 'Falta una lectura base' : 'Sin intervalo por km' : `Siguiente referencia: ${formatNumber.format(plan.due_odometer_km)} km`}</small></div>
								</div>
								<div class="coverage" aria-label="Cobertura del cálculo"><span>Fecha: {stateText(plan.coverage.date ?? 'unknown')}</span><span>Km: {stateText(plan.coverage.km ?? 'unknown')}</span></div>
								{#if plan.due_reasons.length}<ul class="due-reasons">{#each plan.due_reasons as reason}<li>{reasonText(reason)}</li>{/each}</ul>{/if}
								{#if plan.notes}<p class="plan-notes">{plan.notes}</p>{/if}
								<div class="plan-actions"><button class="button button-secondary" type="button" disabled={busy || saving || !online} onclick={() => openPlan(plan)}>Editar plan</button><button class="button button-primary" type="button" disabled={busy || saving || !online} onclick={() => openCompletion(plan)}>Registrar realizado</button><button class="button button-quiet danger-text" type="button" disabled={busy || saving || !online} onclick={() => void remove(plan)}>Eliminar</button></div>
								{#if plan.history.length}
									<details class="history-details"><summary>Historial · {plan.history.length}{plan.history.length === 100 ? '+' : ''} registros</summary><ol class="history-list">{#each plan.history.slice(-3).reverse() as entry (entry.id)}<li><strong>{dateText(entry.performed_date)}</strong><span>{entry.odometer_km === null ? 'Odómetro sin dato' : `${formatNumber.format(entry.odometer_km)} km`}</span><span>{money(entry.cost_eur)}</span></li>{/each}</ol>{#if plan.history.length > 3}<p class="history-note">Se muestran las 3 realizaciones más recientes de esta lista.</p>{/if}</details>
								{:else}<p class="history-empty">Aún no hay realizaciones registradas.</p>{/if}
							</div>
						</article>
					</li>
				{/each}
			</ol>
			<nav class="pagination" aria-label="Páginas de mantenimiento"><p>Mostrando {pageStart}–{pageEnd} de {formatNumber.format(page.total)}</p><div><button class="button button-secondary" type="button" disabled={offset === 0 || loading} onclick={() => { offset = Math.max(0, offset - PAGE_SIZE); }}>Anterior</button><button class="button button-secondary" type="button" disabled={offset + PAGE_SIZE >= page.total || loading} onclick={() => { offset += PAGE_SIZE; }}>Siguiente</button></div></nav>
		{:else}<div class="empty-state"><span class="empty-mark" aria-hidden="true"><Glyph name="settings" size={20} /></span><div><h3>{page.total ? 'No hay planes en esta página' : 'Empieza por una tarea conocida'}</h3><p>{page.total ? 'La lista ha cambiado. Vuelve a consultar los planes.' : 'Añade una revisión por fecha o kilómetros usando datos que conozcas.'}</p></div>{#if !page.total}<button class="button button-primary" type="button" onclick={() => openPlan()} disabled={busy || saving || !online}>Crear primer plan</button>{/if}</div>{/if}
	{:else if !loading && !readError}<p class="maintenance-state">Conéctate para consultar los planes guardados en tu cuenta.</p>{/if}
	{#if page?.total === MAX_PLANS}<p class="quota-note">Has alcanzado el límite de {formatNumber.format(MAX_PLANS)} planes. El historial eliminado se conserva según las reglas de la cuenta.</p>{/if}
</section>

<style>
	.maintenance-panel { display: grid; gap: 18px; min-width: 0; }
	.maintenance-header, .plan-top, .form-title, .form-actions, .pagination, .pagination > div { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	.maintenance-header { align-items: flex-end; padding: 4px 0 6px; }
	.maintenance-mark { display: grid; width: 42px; height: 42px; flex: 0 0 42px; place-items: center; border: 1px solid var(--line); border-radius: 12px; background: var(--forest-soft); color: var(--forest); }
	.maintenance-mark :global(svg) { display: block; }
	.maintenance-heading-copy { min-width: 0; flex: 1 1 auto; }
	.maintenance-header h2 { margin: 3px 0 4px; font-size: clamp(1.35rem, 2vw, 1.75rem); letter-spacing: -.025em; }
	.maintenance-heading-copy > p:not(.eyebrow) { max-width: 66ch; margin: 0; color: var(--muted); }
	.eyebrow { margin: 0; color: var(--forest); font-size: .75rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
	.maintenance-tools { display: flex; align-items: stretch; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 14px 0; border-block: 1px solid var(--line); border-inline: 0; border-radius: 0; background: transparent; }
	.odometer-form { display: grid; gap: 5px; min-width: min(100%, 290px); }
	.odometer-form label, .field label, .interval-field legend { color: var(--muted-strong); font-size: .875rem; font-weight: 650; }
	.odometer-form label span, .field label span { color: var(--muted); font-weight: 450; }
	.odometer-controls { display: flex; gap: 8px; }
	input, textarea { width: 100%; min-width: 0; min-height: 48px; padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface); color: var(--ink); font: inherit; }
	textarea { resize: vertical; }
	.odometer-controls input { max-width: 170px; }
	.odometer-form p, .quota-note { margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.45; }
	.quota { display: grid; min-width: 118px; align-content: center; gap: 2px; padding: 8px 14px; border-left: 1px solid var(--line-strong); color: var(--muted); font-size: .8125rem; }
	.quota strong { color: var(--ink); font-size: 1rem; font-variant-numeric: tabular-nums; }
	.quota-details { max-width: 30ch; }
	.quota-details summary { display: flex; min-height: 48px; align-items: center; color: var(--forest); font-size: .75rem; font-weight: 650; cursor: pointer; }
	.quota-details small { display: block; padding-bottom: 5px; color: var(--muted); font-size: .75rem; line-height: 1.4; }
	.maintenance-form { display: grid; gap: 16px; min-width: 0; padding: clamp(14px, 2.4vw, 22px); border: 1px solid var(--line-strong); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow); }
	.form-title h3 { margin: 2px 0 0; font-size: 1.2rem; }
	.icon-button { min-width: 48px; min-height: 48px; border: 1px solid var(--line); border-radius: 50%; background: var(--surface-soft); font-size: 1.5rem; cursor: pointer; }
	.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
	.field { display: grid; align-content: start; gap: 6px; min-width: 0; }
	.field.full { grid-column: 1 / -1; }
	.field small, .interval-field small { color: var(--danger); font-size: .8125rem; }
	.field input[aria-invalid="true"], .field textarea[aria-invalid="true"], .interval-field input[aria-invalid="true"] { border-color: var(--danger); }
	.interval-field { display: grid; align-content: start; gap: 7px; min-width: 0; margin: 0; padding: 10px 12px 12px; border: 1px solid var(--line); border-radius: 12px; }
	.interval-field legend { padding: 0 5px; }
	.input-with-unit { display: flex; align-items: center; gap: 8px; min-width: 0; }
	.input-with-unit span { flex: 0 0 auto; color: var(--muted); font-size: .875rem; }
	.form-hint { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.form-actions { justify-content: flex-end; flex-wrap: wrap; }
	.button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 48px; padding: 9px 15px; border: 1px solid transparent; border-radius: 11px; font: inherit; font-weight: 680; cursor: pointer; transition: background-color 150ms ease, border-color 150ms ease, transform 150ms ease; }
	.button:hover:not(:disabled) { transform: translateY(-1px); }
	.button-primary { background: var(--forest); color: var(--on-primary); }
	.button-primary:hover:not(:disabled) { background: var(--forest-hover); }
	.button-secondary { border-color: var(--line-strong); background: var(--surface); color: var(--ink); }
	.button-secondary:hover:not(:disabled), .button-quiet:hover:not(:disabled) { background: var(--surface-soft); }
	.button-quiet { border-color: var(--line); background: transparent; color: var(--muted-strong); }
	.button:disabled { opacity: .58; cursor: not-allowed; }
	.danger-text { color: var(--danger); }
	.text-button { display: block; min-height: 48px; padding: 4px 0; border: 0; background: transparent; color: inherit; text-align: left; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
	.form-errors, .maintenance-error { display: grid; gap: 5px; margin: 0; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--danger) 32%, var(--line)); border-radius: 12px; background: var(--danger-soft); color: var(--danger); line-height: 1.45; overflow-wrap: anywhere; }
	.form-errors ul { margin: 0; padding-inline-start: 20px; }
	.form-errors a { text-decoration: underline; text-underline-offset: 2px; }
	.maintenance-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
	.maintenance-state { margin: 0; padding: 13px; border-radius: 12px; background: var(--surface-soft); color: var(--muted); }
	.plan-list.evolution-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
	.plan-list > li.evolution-item { position: relative; display: block; min-width: 0; overflow: visible; border: 0; border-top: 1px solid var(--line); background: transparent; }
	.plan-list > li.plan-due { border-color: color-mix(in srgb, var(--accent) 48%, var(--line)); }
	.plan-list > li.plan-unknown { border-style: dashed; }
	.plan-card { display: grid; grid-template-columns: 5px minmax(0, 1fr); min-width: 0; }
	.plan-accent { background: var(--forest); }
	.plan-due .plan-accent { background: var(--accent); }
	.plan-unknown .plan-accent { background: repeating-linear-gradient(180deg, var(--line-strong) 0 4px, transparent 4px 8px); }
	.plan-body { display: grid; gap: 13px; min-width: 0; padding: clamp(17px, 2.4vw, 23px) 0; }
	.plan-top { align-items: flex-start; }
	.plan-label { margin: 0; color: var(--muted); font-size: .75rem; font-weight: 680; text-transform: uppercase; letter-spacing: .06em; }
	.plan-top h3 { margin: 2px 0 0; font-size: 1.15rem; letter-spacing: -.015em; }
	.status-chip { flex: 0 0 auto; max-width: 15ch; padding: 5px 9px; border: 1px solid var(--line); border-radius: 999px; background: var(--forest-soft); color: var(--forest); font-size: .75rem; font-weight: 720; text-align: center; }
	.chip-due { background: var(--accent-soft); color: var(--accent); }
	.chip-unknown { border-style: dashed; background: var(--surface-soft); color: var(--muted-strong); }
	.plan-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
	.plan-detail-grid > div { display: grid; gap: 3px; min-width: 0; padding: 10px 0; border-top: 1px solid var(--line); }
	.plan-detail-grid span, .coverage { color: var(--muted); font-size: .8rem; }
	.plan-detail-grid strong { font-size: .95rem; font-weight: 680; }
	.plan-detail-grid small { color: var(--muted); line-height: 1.4; }
	.coverage { display: flex; flex-wrap: wrap; gap: 7px; }
	.coverage span { padding: 5px 8px; border: 1px solid var(--line); border-radius: 8px; }
	.due-reasons { display: grid; gap: 3px; margin: -4px 0 0; padding-inline-start: 20px; color: var(--muted-strong); font-size: .875rem; }
	.plan-notes { margin: 0; padding-left: 11px; border-left: 2px solid var(--line-strong); color: var(--muted-strong); white-space: pre-wrap; overflow-wrap: anywhere; }
	.plan-actions { display: flex; flex-wrap: wrap; gap: 8px; }
	.history-details { padding-top: 9px; border-top: 1px solid var(--line); }
	.history-details summary { display: flex; align-items: center; min-height: 48px; color: var(--muted-strong); font-size: .875rem; font-weight: 650; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
	.history-list { display: grid; gap: 0; margin: 8px 0 0; padding: 0; list-style: none; }
	.history-list li { display: flex; flex-wrap: wrap; gap: 6px 15px; padding: 10px 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; color: var(--muted); font-size: .8125rem; }
	.history-list strong { color: var(--ink); }
	.history-note, .history-empty { margin: 6px 0 0; color: var(--muted); font-size: .8125rem; }
	.pagination { padding-top: 4px; color: var(--muted); font-size: .875rem; }
	.pagination p { margin: 0; }
	.pagination > div { flex-wrap: wrap; }
	.empty-state { display: grid; grid-template-columns: 36px minmax(0, 1fr); justify-items: start; align-items: start; gap: 6px 11px; padding: clamp(20px, 4vw, 36px); border: 1px dashed var(--line-strong); border-radius: 18px; background: var(--surface); }
	.empty-state h3, .empty-state p { margin: 0; }
	.empty-state p { max-width: 54ch; color: var(--muted); }
	.empty-state > .button { grid-column: 2; }
	.empty-mark { display: grid; width: 36px; height: 36px; grid-row: span 2; place-items: center; border-radius: 11px; background: var(--forest-soft); color: var(--forest); }
	.empty-mark :global(svg) { display: block; }
	@media (max-width: 620px) { .maintenance-header { display: grid; grid-template-columns: 42px minmax(0, 1fr); align-items: start; }.maintenance-heading-copy { grid-column: 2; }.maintenance-header > button { grid-column: 1 / -1; width: 100%; }.form-grid, .plan-detail-grid { grid-template-columns: 1fr; }.field.full { grid-column: auto; }.quota { border-left: 0; border-top: 1px solid var(--line-strong); padding: 10px 0 0; }.plan-top { flex-direction: column; }.status-chip { max-width: none; }.pagination { align-items: flex-start; flex-direction: column; }.pagination > div { width: 100%; }.pagination .button { flex: 1; }.empty-state > .button { grid-column: 1 / -1; width: 100%; } }
	@media (prefers-reduced-motion: reduce) { .button { transition: none; }.button:hover:not(:disabled) { transform: none; } }
	.maintenance-panel.workspace-panel { gap: 15px; padding: clamp(15px, 2vw, 23px); border: 1px solid var(--line); border-radius: 20px; background: var(--surface); box-shadow: var(--shadow); }
	.maintenance-header { align-items: center; margin: 0; padding: 0 0 12px; border-bottom: 1px solid var(--line); }
	.maintenance-mark { width: 36px; height: 36px; flex-basis: 36px; border-radius: 10px; }
	.maintenance-header h2 { font-size: 1.1rem; }
	.maintenance-heading-copy > p:not(.eyebrow) { max-width: 58ch; font-size: .8125rem; }
	.maintenance-tools { padding-block: 10px; }
	.maintenance-form { gap: 14px; padding: 14px 0 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.interval-field { border: 0; border-top: 1px solid var(--line); border-radius: 0; padding: 12px 0 0; }
	.form-title h3 { font-size: 1.05rem; }
	.plan-body { gap: 11px; }
	.plan-actions { padding-top: 3px; }
	.coverage span { border: 0; border-bottom: 1px solid var(--line); border-radius: 0; padding: 4px 0; }
	.empty-state { padding: 18px 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; }
	@media (max-width: 620px) {
		.maintenance-panel.workspace-panel { padding: 14px; border-radius: 17px; }
		.maintenance-header { grid-template-columns: minmax(0, 1fr); }
		.maintenance-mark { display: none; }
		.maintenance-heading-copy { grid-column: auto; }
		.maintenance-header > button { grid-column: auto; }
		.plan-actions { display: grid; grid-template-columns: minmax(0, 1fr); }
	}
</style>
