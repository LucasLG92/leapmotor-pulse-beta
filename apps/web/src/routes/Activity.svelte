<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { ApiError, getCommandHistory } from '../lib/api';
	import Glyph from '../lib/Glyph.svelte';
	import ManualJourneysPanel from '../lib/ManualJourneysPanel.svelte';
	import MaintenancePlansPanel from '../lib/MaintenancePlansPanel.svelte';
	import { count, dateOnly, dateTime, number } from '../lib/format';
	import type { CommandHistoryEntry, MaintenanceItem, PageProps } from '../lib/types';

	let { pulseState, busy, online, revision, mutate, reserveJourneyCreation, confirmAction, drafts, onSessionExpired }: PageProps = $props();
	let commandHistory = $state<CommandHistoryEntry[]>([]);
	let loadingCommands = $state(true);
	let commandError = $state('');
	let editing = $state<MaintenanceItem | null>(null);
	let title = $state('');
	let dueDate = $state('');
	let formError = $state('');
	let formErrorField = $state<'title' | 'date' | null>(null);
	let maintenanceSaving = $state(false);
	let maintenanceBaseline = { title: '', dueDate: '' };
	let disposed = false;
	let commandHistoryRequest = 0;
	let maintenanceEditRequest = 0;
	let observedRevision: number | undefined;
	let observedOnline: boolean | undefined;
	let journeys = $derived(pulseState.journeys);
	let maintenance = $derived(pulseState.maintenance);

	function isMaintenanceDirty(): boolean {
		return title !== maintenanceBaseline.title || dueDate !== maintenanceBaseline.dueDate;
	}

	async function loadCommandHistory() {
		if (disposed || !online) return;
		const request = ++commandHistoryRequest;
		loadingCommands = true;
		commandError = '';
		try {
			const result = await getCommandHistory();
			if (disposed || request !== commandHistoryRequest) return;
			commandHistory = result;
		} catch (error) {
			if (disposed || request !== commandHistoryRequest) return;
			if (error instanceof ApiError && error.status === 401) {
				onSessionExpired();
				return;
			}
			commandError = error instanceof ApiError ? error.message : !navigator.onLine
				? 'La conexión no está disponible. El historial no se conserva sin red en este dispositivo.'
				: 'No se pudo cargar el historial de acciones.';
		} finally {
			if (!disposed && request === commandHistoryRequest) loadingCommands = false;
		}
	}

	onMount(() => {
		const unregisterDraft = drafts.register('maintenance', isMaintenanceDirty);
		return unregisterDraft;
	});

	onDestroy(() => {
		disposed = true;
		commandHistoryRequest += 1;
	});

	$effect(() => {
		const currentRevision = revision;
		const connected = online;
		const shouldRefresh = connected && (observedOnline !== true || currentRevision !== observedRevision);
		observedRevision = currentRevision;
		observedOnline = connected;
		if (!connected) {
			commandHistoryRequest += 1;
			commandHistory = [];
			loadingCommands = false;
			commandError = 'La conexión no está disponible. El historial no se conserva sin red en este dispositivo.';
			return;
		}
		if (shouldRefresh) untrack(() => { void loadCommandHistory(); });
	});

	async function beginEdit(item: MaintenanceItem) {
		const request = ++maintenanceEditRequest;
		if (disposed || busy || (editing?.id === item.id && isMaintenanceDirty())) return;
		if (isMaintenanceDirty()) {
			const accepted = await confirmAction(
				'Descartar cambios de mantenimiento',
				'Hay cambios sin guardar en el formulario. Se descartarán para editar otra tarea.',
				'Descartar cambios'
			);
			if (!accepted || disposed || busy || request !== maintenanceEditRequest) return;
		}
		const currentItem = maintenance.find((current) => current.id === item.id);
		if (!currentItem) return;
		editing = currentItem;
		title = currentItem.title;
		dueDate = currentItem.due_date;
		maintenanceBaseline = { title: currentItem.title, dueDate: currentItem.due_date };
		formError = '';
		formErrorField = null;
		window.setTimeout(() => { if (!disposed) document.getElementById('maintenance-title')?.focus(); }, 0);
	}

	function cancelEdit() {
		maintenanceEditRequest += 1;
		editing = null;
		title = '';
		dueDate = '';
		formError = '';
		formErrorField = null;
		maintenanceBaseline = { title: '', dueDate: '' };
	}

	async function showMaintenanceError(field: 'title' | 'date', message: string) {
		formError = message;
		formErrorField = field;
		await tick();
		if (!disposed) document.getElementById(field === 'title' ? 'maintenance-title' : 'maintenance-date')?.focus();
	}

	function clearMaintenanceError(field: 'title' | 'date') {
		if (formErrorField !== field) return;
		formError = '';
		formErrorField = null;
	}

	async function saveMaintenance(event: SubmitEvent) {
		event.preventDefault();
		if (disposed || busy || maintenanceSaving) return;
		const form = event.currentTarget as HTMLFormElement;
		const previousFocus = document.activeElement instanceof HTMLElement && form.contains(document.activeElement)
			? document.activeElement : null;
		formError = '';
		formErrorField = null;
		const cleanTitle = title.trim();
		if (!cleanTitle || cleanTitle.length > 120) {
			await showMaintenanceError('title', 'Escribe un título de hasta 120 caracteres.');
			return;
		}
		const parsedDate = /^\d{4}-\d{2}-\d{2}$/.test(dueDate) ? new Date(`${dueDate}T12:00:00`) : null;
		if (!parsedDate || Number.isNaN(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== dueDate) {
			await showMaintenanceError('date', 'Indica una fecha válida.');
			return;
		}
		const path = editing ? `/api/maintenance/${encodeURIComponent(editing.id)}` : '/api/maintenance';
		const request = maintenanceEditRequest;
		const submittedTitle = title;
		const submittedDate = dueDate;
		maintenanceSaving = true;
		try {
			const saved = await mutate(path, editing ? 'PUT' : 'POST', { title: cleanTitle, due_date: submittedDate }, editing ? 'El mantenimiento se ha actualizado.' : 'El mantenimiento se ha añadido.');
			if (disposed || request !== maintenanceEditRequest) return;
			if (saved) {
				if (title === submittedTitle && dueDate === submittedDate) cancelEdit();
				else maintenanceBaseline = { title: submittedTitle, dueDate: submittedDate };
			} else formError = 'No se ha podido confirmar el guardado del recordatorio. Revisa las tareas guardadas antes de reintentarlo.';
		} catch {
			if (!disposed && request === maintenanceEditRequest) formError = 'No se ha podido confirmar el guardado del recordatorio. Revisa las tareas guardadas antes de reintentarlo.';
		} finally {
			if (!disposed) {
				maintenanceSaving = false;
				await tick();
				if (!disposed && request === maintenanceEditRequest && formError && document.hasFocus() && document.activeElement === document.body && previousFocus?.isConnected) {
					previousFocus.focus({ preventScroll: true });
				}
			}
		}
	}

	async function toggleMaintenance(item: MaintenanceItem) {
		await mutate(`/api/maintenance/${encodeURIComponent(item.id)}`, 'PUT', { completed: !item.completed }, item.completed ? 'El mantenimiento se ha reabierto.' : 'El mantenimiento se ha marcado como hecho.');
	}

	async function deleteMaintenance(item: MaintenanceItem) {
		const accepted = await confirmAction(
			'Eliminar mantenimiento',
			`Se eliminará «${item.title}» de tu registro. Esta operación no se puede deshacer.`,
			'Eliminar registro'
		);
		if (!accepted || disposed) return;
		const removed = await mutate(`/api/maintenance/${encodeURIComponent(item.id)}`, 'DELETE', undefined, 'El mantenimiento se ha eliminado.');
		if (!disposed && removed && editing?.id === item.id) cancelEdit();
	}

	function commandLabel(command: string): string {
		const labels: Record<string, string> = {
			lock: 'Bloquear vehículo', unlock: 'Desbloquear vehículo', climate_on: 'Activar climatización',
			climate_off: 'Desactivar climatización', charge_start: 'Iniciar carga', charge_stop: 'Detener carga'
		};
		return labels[command] ?? command;
	}
</script>

<section class="page-intro">
	<div>
		<h2>Tu diario</h2>
		<p>Tu registro manual de trayectos, demostración y mantenimiento guardado en Pulse, cada uno con su fecha.</p>
	</div>
	<span class="status-pill" class:status-demo={pulseState.mode === 'demo'}><span class="status-dot"></span>{pulseState.mode === 'demo' ? 'Incluye datos simulados' : 'Solo datos guardados'}</span>
</section>

<section class="activity-grid">
	<div class="activity-main-column">
		<ManualJourneysPanel {busy} {online} {revision} {drafts} {mutate} {reserveJourneyCreation} {confirmAction} {onSessionExpired} />
		<section aria-labelledby="journeys-heading">
			<div class="section-heading"><div><h2 id="journeys-heading">{pulseState.mode === 'demo' ? 'Trayectos de demostración' : 'Trayectos automáticos'}</h2><p class="activity-section-copy">{pulseState.mode === 'demo' ? 'Registros ficticios, separados de tu diario manual.' : 'La recepción del vehículo requiere una integración cualificada.'}</p></div><span class="badge">{count(journeys.length)} disponibles</span></div>
			{#if journeys.length === 0}
				<div class="empty-state"><span class="empty-mark"><Glyph name="activity" /></span><h3>No hay trayectos recibidos</h3><p>{pulseState.mode === 'demo' ? 'El servidor no entrega recorridos en este momento.' : 'Pulse aún no tiene una integración que reciba recorridos del vehículo.'}</p>{#if pulseState.mode !== 'demo'}<a class="button button-secondary" href="/settings#preparation">Revisar preparación</a>{/if}</div>
			{:else}
				<div class="journey-list card evolution-list">
					{#each journeys as journey (journey.id)}
						<article class="journey-row evolution-item">
							<div class="journey-route-mark" aria-hidden="true"><span></span><i></i><span></span></div>
							<div class="journey-main"><strong>{journey.from_label} <Glyph name="arrow" size={14} /> {journey.to_label}</strong><span>{dateTime(journey.started_at)}</span><small>{number(journey.distance_km)} km <i aria-hidden="true">·</i> {number(journey.duration_min)} min</small></div>
							<div class="journey-energy"><strong>{number(journey.energy_kwh)} <small>kWh</small></strong><span>Energía</span></div>
						</article>
					{/each}
				</div>
			{/if}
		</section>

		<section aria-labelledby="command-history-heading">
			<div class="section-heading"><div><h2 id="command-history-heading">Historial de acciones</h2><p class="activity-section-copy">Resultados guardados por Pulse; no son confirmaciones del coche.</p></div><span class="badge badge-demo">Simulaciones</span></div>
			{#if loadingCommands}
				<div class="card loading-card" role="status">Cargando historial…</div>
			{:else if commandError}
				<div class="empty-state"><h3>{online ? 'No se pudo abrir el historial' : 'Historial sin conexión'}</h3><p role="alert" aria-atomic="true">{commandError}</p><button class="button button-secondary" type="button" disabled={busy || !online || loadingCommands} onclick={loadCommandHistory}>Reintentar</button></div>
			{:else if commandHistory.length === 0}
				<div class="empty-state"><span class="empty-mark"><Glyph name="settings" /></span><h3>No hay acciones registradas</h3><p>Cuando se active una acción de demostración, aparecerá aquí con su resultado.</p></div>
			{:else}
				<div class="command-history card evolution-list">
					{#each commandHistory as item (item.id)}
						<article class="history-row evolution-item">
							<span class="history-icon"><Glyph name="check" size={16} /></span>
							<div><strong>{commandLabel(item.command)}</strong><p>{item.message}</p></div>
							<div class="history-meta"><span class="badge badge-demo">{item.status === 'simulated' ? 'Simulada' : item.status}</span><time datetime={item.created_at}>{dateTime(item.created_at)}</time></div>
						</article>
					{/each}
				</div>
			{/if}
			<p class="fine-print history-footnote">Las acciones son simulaciones registradas por el servidor. No se ha enviado ninguna orden al vehículo.</p>
		</section>
	</div>

	<aside class="activity-side-column">
		<MaintenancePlansPanel {busy} {online} {revision} {drafts} {mutate} {confirmAction} {onSessionExpired} />
		<details class="maintenance-legacy" aria-labelledby="maintenance-heading">
			<summary><span class="legacy-summary-copy"><strong id="maintenance-heading">Recordatorios por fecha</strong><small>Registro anterior de mantenimiento personal</small></span><span class="badge">{count(maintenance.length)}</span></summary>
			<div class="legacy-maintenance-content">
				<p class="activity-section-copy">Conservamos aquí los recordatorios guardados con fecha prevista. Tus planes de mantenimiento actuales aparecen en la sección principal.</p>
				{#if maintenance.length === 0}
					<div class="empty-state maintenance-empty"><span class="empty-mark"><Glyph name="check" /></span><h3>Sin recordatorios por fecha</h3><p>Los nuevos planes se añaden desde la sección principal.</p></div>
				{:else}
					<div class="maintenance-list">
						{#each maintenance as item (item.id)}
							<article class="maintenance-card card" class:maintenance-complete={item.completed}>
								<div class="maintenance-card-top"><span class="maintenance-date">{dateOnly(item.due_date)}</span><span class="badge" class:badge-success={item.completed}>{item.completed ? 'Hecho' : 'Pendiente'}</span></div>
								<h3>{item.title}</h3>
								<div class="maintenance-actions">
									<button class="text-button" type="button" disabled={busy} onclick={() => toggleMaintenance(item)}>{item.completed ? 'Reabrir' : 'Marcar hecho'}</button>
									<button class="text-button" type="button" disabled={busy} onclick={() => beginEdit(item)}>Editar</button>
									<button class="text-button danger-text" type="button" disabled={busy} onclick={() => deleteMaintenance(item)}>Eliminar</button>
								</div>
							</article>
						{/each}
					</div>
				{/if}
				<section class="maintenance-form-card card" aria-labelledby="maintenance-form-heading">
					<div class="section-heading"><div><h2 id="maintenance-form-heading">{editing ? 'Editar tarea anterior' : 'Añadir recordatorio por fecha'}</h2><p class="activity-section-copy">El formulario modifica únicamente tu registro en Pulse.</p></div><span class="form-icon"><Glyph name={editing ? 'settings' : 'plus'} size={18} /></span></div>
					<form class="maintenance-form" onsubmit={saveMaintenance}>
						<div class="field"><label for="maintenance-title">Tarea</label><input id="maintenance-title" maxlength="120" bind:value={title} oninput={() => clearMaintenanceError('title')} placeholder="Por ejemplo, revisar neumáticos" required aria-invalid={formErrorField === 'title' ? 'true' : undefined} aria-describedby={formErrorField === 'title' ? 'maintenance-title-error' : undefined} />{#if formError && formErrorField === 'title'}<p id="maintenance-title-error" class="form-error" role="alert">{formError}</p>{/if}</div>
						<div class="field"><label for="maintenance-date">Fecha prevista</label><input id="maintenance-date" type="date" bind:value={dueDate} oninput={() => clearMaintenanceError('date')} required aria-invalid={formErrorField === 'date' ? 'true' : undefined} aria-describedby={formErrorField === 'date' ? 'maintenance-date-error' : undefined} />{#if formError && formErrorField === 'date'}<p id="maintenance-date-error" class="form-error" role="alert">{formError}</p>{/if}</div>
						{#if formError && formErrorField === null}<p id="maintenance-general-error" class="form-error" role="alert">{formError}</p>{/if}
						<div class="form-actions"><button class="button button-secondary button-block" type="submit" disabled={busy || maintenanceSaving}>{busy || maintenanceSaving ? 'Guardando…' : editing ? 'Guardar cambios' : 'Añadir recordatorio'}</button>{#if editing}<button class="button button-quiet button-block" type="button" onclick={cancelEdit}>Cancelar edición</button>{/if}</div>
					</form>
					<p class="fine-print">Los recordatorios son datos de Pulse. No se envían al fabricante ni activan una revisión.</p>
				</section>
			</div>
		</details>

		<div class="notice"><Glyph name="protection" size={17} /><span>Actividad muestra únicamente los registros recibidos o añadidos en Pulse. Los datos ficticios se marcan como simulaciones.</span></div>
	</aside>
</section>

<style>
	.page-intro h2 { margin: 0; font-size: clamp(1.8rem, 3vw, 2.45rem); letter-spacing: -.035em; }
	.activity-section-copy { max-width: 58ch; margin: 6px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.activity-grid { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: clamp(28px, 4vw, 48px); }
	.activity-main-column, .activity-side-column { display: grid; min-width: 0; gap: clamp(28px, 4vw, 48px); }
	.activity-grid .section-heading { margin-top: 0; }
	.journey-list.card, .command-history.card { overflow: visible; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.journey-list, .command-history { overflow: hidden; }
	.journey-row.evolution-item { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 79px; padding: 15px 4px; border-bottom: 1px solid var(--line); }
	.journey-row:first-child, .history-row:first-child { border-top: 1px solid var(--line); }
	.journey-row:last-child, .history-row:last-child { border-bottom: 0; }
	.journey-route-mark { display: grid; justify-items: center; gap: 3px; }
	.journey-route-mark span { width: 7px; height: 7px; border: 2px solid var(--forest); border-radius: 50%; }
	.journey-route-mark i { width: 1px; height: 17px; background: var(--line-strong); }
	.journey-main { display: grid; min-width: 0; gap: 4px; }
	.journey-main > strong { display: flex; align-items: center; gap: 5px; overflow-wrap: anywhere; font-size: .875rem; }
	.journey-main > strong :global(svg) { flex: 0 0 14px; color: var(--muted); }
	.journey-main > span { color: var(--muted); font-size: .75rem; }
	.journey-main > small { color: var(--muted); font-size: .875rem; }
	.journey-main > small i { font-style: normal; }
	.journey-energy { display: grid; gap: 4px; text-align: right; }
	.journey-energy strong { font-size: 0.875rem; font-variant-numeric: tabular-nums; }
	.journey-energy strong small { color: var(--muted); font-size: .75rem; font-weight: 500; }
	.journey-energy span { color: var(--muted); font-size: .75rem; }
	.command-history .history-row.evolution-item { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: start; gap: 10px; padding: 15px 4px; border-bottom: 1px solid var(--line); }
	.history-icon { display: grid; width: 30px; height: 30px; place-items: center; border-radius: 9px; background: var(--accent-soft); color: var(--accent); }
	.history-row > div:nth-child(2) { min-width: 0; }
	.history-row > div:nth-child(2) strong { font-size: .875rem; }
	.history-row p { margin: 4px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.history-meta { display: grid; justify-items: end; gap: 6px; }
	.history-meta time { color: var(--muted); font-size: .75rem; white-space: nowrap; }
	.history-footnote { margin: 8px 0 0; }
	.maintenance-list { display: grid; gap: 0; border-top: 1px solid var(--line); }
	.maintenance-card.card { padding: 14px 2px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.maintenance-complete { opacity: .82; }
	.maintenance-card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
	.maintenance-date { color: var(--muted); font-size: .75rem; }
	.maintenance-card h3 { margin: 13px 0; font-size: .875rem; font-weight: 640; }
	.maintenance-actions { display: flex; flex-wrap: wrap; gap: 5px 10px; border-top: 1px solid var(--line); }
	.maintenance-actions .text-button { min-height: 48px; font-size: .875rem; }
	.maintenance-empty { margin-bottom: 10px; }
	.maintenance-form-card { padding: 19px; }
	.maintenance-form-card .section-heading { align-items: center; }
	.maintenance-form-card .section-heading h2 { font-size: 1.0625rem; }
	.maintenance-form { display: grid; gap: 13px; margin-top: 16px; }
	.maintenance-form-card .fine-print { margin: 12px 0 0; }
	.maintenance-legacy { min-width: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
	.maintenance-legacy > summary { display: flex; min-height: 56px; justify-content: space-between; align-items: center; gap: 12px; list-style: none; color: var(--ink); }
	.maintenance-legacy > summary::-webkit-details-marker { display: none; }
	.legacy-summary-copy { display: grid; gap: 3px; }
	.legacy-summary-copy strong { font-size: .9375rem; }
	.legacy-summary-copy small { color: var(--muted); font-size: .75rem; font-weight: 450; }
	.legacy-maintenance-content { display: grid; gap: 14px; padding: 10px 0 18px; }
	.maintenance-legacy[open] > summary { border-bottom: 1px solid var(--line); }
	.activity-side-column > .notice { font-size: .875rem; }
	.activity-side-column > .notice :global(svg) { flex: 0 0 17px; color: var(--forest); }
	@media (max-width: 940px) {
		.activity-grid { grid-template-columns: minmax(0, 1fr); }
		.activity-side-column { grid-template-columns: minmax(0, 1fr); align-items: start; }
		.activity-side-column > :first-child { grid-row: auto; }
	}
	@media (max-width: 700px) {
		.activity-side-column { grid-template-columns: 1fr; }
		.activity-side-column > :first-child { grid-row: auto; }
		.journey-row.evolution-item { padding-inline: 11px; }
		.command-history .history-row.evolution-item { grid-template-columns: 29px minmax(0, 1fr); }
		.history-meta { grid-column: 2; display: flex; align-items: center; justify-items: start; }
	}


	@media (max-width: 360px) {
		.journey-row.evolution-item { grid-template-columns: 16px minmax(0, 1fr); }
		.journey-energy { grid-column: 2; grid-template-columns: auto 1fr; align-items: baseline; text-align: left; }
		.journey-energy span { text-align: right; }
		.history-meta { flex-wrap: wrap; }
		.maintenance-actions { gap: 2px 6px; }
	}
</style>
