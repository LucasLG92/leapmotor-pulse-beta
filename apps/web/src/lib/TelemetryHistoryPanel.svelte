<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError, getTelemetryHistory } from './api';
	import { count, number } from './format';
	import type { TemporalQuality, TelemetrySnapshot } from './types';

	let { online, revision, quality, collector, onSessionExpired }: {
		online: boolean;
		revision: number;
		quality: TemporalQuality | null;
		collector: Record<string, unknown> | null;
		onSessionExpired: () => void;
	} = $props();

	const pageSize = 20;
	const retentionLimit = 1_000;
	const signalFields = [
		{ key: 'battery_pct', label: 'Batería' },
		{ key: 'range_km', label: 'Autonomía' },
		{ key: 'odometer_km', label: 'Odómetro' },
		{ key: 'locked', label: 'Cierre' },
		{ key: 'climate_on', label: 'Climatización' },
		{ key: 'charging', label: 'Carga' }
	] as const;

	let expanded = $state(false);
	let mounted = $state(false);
	let snapshots = $state<TelemetrySnapshot[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let requestedOffset = $state(0);
	let loading = $state(false);
	let error = $state('');
	let retry = $state(0);
	let controller: AbortController | null = null;
	let requestGeneration = 0;
	let lastRequestKey = '';
	let displayedRevision: number | null = null;
	let destroyed = false;
	const lastOffset = $derived(Math.min(
		Math.floor(Math.max(0, Math.min(total, retentionLimit) - 1) / pageSize) * pageSize,
		retentionLimit - pageSize
	));
	const currentPage = $derived(Math.floor(offset / pageSize) + 1);
	const pageCount = $derived(Math.max(1, Math.ceil(Math.min(total, retentionLimit) / pageSize)));
	const collectorStatus = $derived(safeCollectorStatus(collector));

	$effect(() => {
		const open = expanded;
		const connected = online;
		const currentRevision = revision;
		let targetOffset = requestedOffset;
		const retryNumber = retry;
		if (!mounted) return;

		if (!open || !connected) {
			cancelRequest();
			snapshots = [];
			total = 0;
			offset = 0;
			requestedOffset = 0;
			loading = false;
			error = !connected && open ? 'Sin conexión. Las lecturas no se guardan en este dispositivo.' : '';
			lastRequestKey = '';
			displayedRevision = currentRevision;
			return;
		}

		if (displayedRevision !== currentRevision) {
			snapshots = [];
			total = 0;
			offset = 0;
			requestedOffset = 0;
			targetOffset = 0;
			loading = false;
			error = '';
			lastRequestKey = '';
			displayedRevision = currentRevision;
		}

		const key = `${currentRevision}:${targetOffset}:${retryNumber}`;
		if (key === lastRequestKey) return;
		lastRequestKey = key;
		void loadPage(targetOffset, currentRevision);
	});

	onMount(() => { mounted = true; });

	onDestroy(() => {
		destroyed = true;
		mounted = false;
		cancelRequest();
		snapshots = [];
	});

	function cancelRequest() {
		requestGeneration += 1;
		controller?.abort();
		controller = null;
	}

	function isCurrent(generation: number, expectedRevision: number): boolean {
		return mounted && !destroyed && generation === requestGeneration && expectedRevision === revision && expanded && online;
	}

	async function loadPage(targetOffset: number, expectedRevision: number) {
		if (!mounted || destroyed || !expanded || !online) return;
		controller?.abort();
		const abortController = new AbortController();
		controller = abortController;
		const generation = ++requestGeneration;
		let pageOffset = Math.max(0, Math.min(Math.floor(targetOffset / pageSize) * pageSize, retentionLimit - pageSize));
		loading = true;
		error = '';
		try {
			let result: Awaited<ReturnType<typeof getTelemetryHistory>> | null = null;
			for (let attempt = 0; attempt < 3; attempt += 1) {
				result = await getTelemetryHistory(pageSize, pageOffset, abortController.signal);
				if (!isCurrent(generation, expectedRevision)) return;
				if (!Number.isSafeInteger(result.total) || result.total < 0 || result.limit !== pageSize ||
					!Number.isSafeInteger(result.offset) || !Array.isArray(result.items) || result.items.length > pageSize) {
					throw new Error('El servidor devolvió una página de lecturas no válida.');
				}
				const safeTotal = Math.min(result.total, retentionLimit);
				const finalOffset = Math.min(
					Math.floor(Math.max(0, safeTotal - 1) / pageSize) * pageSize,
					retentionLimit - pageSize
				);
				if (pageOffset > finalOffset && attempt < 2) {
					pageOffset = finalOffset;
					continue;
				}
				if (pageOffset > finalOffset || result.offset !== pageOffset) {
					throw new Error('El historial cambió mientras se cargaba. Vuelve a intentarlo.');
				}
				break;
			}
			if (!result || !isCurrent(generation, expectedRevision)) return;
			const safeTotal = Math.min(result.total, retentionLimit);
			lastRequestKey = `${expectedRevision}:${pageOffset}:${retry}`;
			snapshots = result.items;
			total = safeTotal;
			offset = pageOffset;
			requestedOffset = pageOffset;
		} catch (cause) {
			if (!isCurrent(generation, expectedRevision)) return;
			if (cause instanceof DOMException && cause.name === 'AbortError') return;
			if (cause instanceof ApiError && cause.status === 401) {
				onSessionExpired();
				return;
			}
			error = cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : 'No se pudo cargar el historial de lecturas.';
		} finally {
			if (isCurrent(generation, expectedRevision)) loading = false;
			if (controller === abortController) controller = null;
		}
	}

	function movePage(nextOffset: number) {
		if (!online || loading) return;
		const safeOffset = Math.max(0, Math.min(Math.floor(nextOffset / pageSize) * pageSize, lastOffset));
		if (safeOffset === requestedOffset) return;
		requestedOffset = safeOffset;
	}

	function retryPage() {
		if (!online || loading) return;
		retry += 1;
	}

	function signalValue(snapshot: TelemetrySnapshot, key: typeof signalFields[number]['key']): string {
		switch (key) {
			case 'battery_pct': return snapshot.vehicle.battery_pct === null ? 'Sin dato' : `${number(snapshot.vehicle.battery_pct)} %`;
			case 'range_km': return snapshot.vehicle.range_km === null ? 'Sin dato' : `${number(snapshot.vehicle.range_km)} km`;
			case 'odometer_km': return snapshot.vehicle.odometer_km === null ? 'Sin dato' : `${number(snapshot.vehicle.odometer_km)} km`;
			case 'locked': return snapshot.vehicle.locked === null ? 'Sin dato' : snapshot.vehicle.locked ? 'Cerrado' : 'Abierto';
			case 'climate_on': return snapshot.vehicle.climate_on === null ? 'Sin dato' : snapshot.vehicle.climate_on ? 'Activo' : 'Inactiva';
			case 'charging': return snapshot.vehicle.charging === null ? 'Sin dato' : snapshot.vehicle.charging ? 'En curso' : 'Detenida';
		}
	}

	function fieldSource(snapshot: TelemetrySnapshot, key: typeof signalFields[number]['key']): string {
		return snapshot.field_sources[key] ?? 'Sin fuente indicada';
	}

	function fullDate(value: string | null | undefined): string {
		if (!value) return 'Sin fecha indicada';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return 'Fecha no disponible';
		return new Intl.DateTimeFormat('es-ES', {
			year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
		}).format(date);
	}

	function qualityText(status: TemporalQuality['temporal_status']): string {
		if (status === 'fresh') return 'Reciente por hora de observación';
		if (status === 'stale') return 'Antigua por hora de observación';
		if (status === 'unavailable') return 'Fuera de la ventana temporal';
		return 'Antigüedad desconocida';
	}

	function ageText(seconds: number | null): string {
		if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Sin edad calculable';
		if (seconds < 60) return `${Math.floor(seconds)} s`;
		if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
		if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
		const days = Math.floor(seconds / 86400);
		return `${days} ${days === 1 ? 'día' : 'días'} ${Math.floor((seconds % 86400) / 3600)} h`;
	}

	function reasonText(reason: string): string {
		const labels: Record<string, string> = {
			no_snapshot: 'No hay una lectura guardada',
			observed_at_missing: 'La lectura no incluye hora de observación',
			observed_at_invalid: 'La hora de observación no es válida',
			observed_at_future: 'La hora de observación está en el futuro',
			observation_expired: 'La observación supera la ventana disponible',
			observation_stale: 'La observación supera el umbral de lectura reciente',
			observation_recent: 'La hora de observación está dentro del umbral reciente'
		};
		return labels[reason] ?? 'Motivo temporal no reconocido';
	}

	function collectorErrorText(code: string): string {
		const labels: Record<string, string> = {
			clock_invalid: 'Reloj no válido',
			read_failed: 'Falló la lectura',
			read_timeout: 'La lectura superó el tiempo límite',
			provider_unavailable: 'Proveedor no disponible',
			invalid_snapshot: 'Lectura recibida con formato no válido',
			store_failed: 'No se pudo guardar la lectura'
		};
		return labels[code] ?? 'Código no reconocido';
	}

	function safeCollectorStatus(value: Record<string, unknown> | null) {
		if (!value || typeof value.running !== 'boolean' || typeof value.closed !== 'boolean' ||
			typeof value.in_flight !== 'boolean' || !isCounter(value.read_count) ||
			!isCounter(value.success_count) || !isCounter(value.error_count) ||
			!isTimestamp(value.last_attempt_at) || !isTimestamp(value.next_attempt_at)) return null;
		const allowedErrors = new Set(['clock_invalid', 'read_failed', 'read_timeout', 'provider_unavailable', 'invalid_snapshot', 'store_failed']);
		if (value.last_error_code !== null && (typeof value.last_error_code !== 'string' || !allowedErrors.has(value.last_error_code))) return null;
		return {
			running: value.running,
			inFlight: value.in_flight,
			readCount: value.read_count,
			successCount: value.success_count,
			errorCount: value.error_count,
			lastAttemptAt: value.last_attempt_at,
			lastErrorCode: value.last_error_code as string | null
		};
	}

	function isCounter(value: unknown): value is number {
		return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
	}

	function isTimestamp(value: unknown): value is string | null {
		return value === null || typeof value === 'string';
	}
</script>

<section class="telemetry-history-section" aria-labelledby="telemetry-history-title">
	<details bind:open={expanded}>
		<summary>
			<span><strong id="telemetry-history-title">Lecturas recibidas</strong><small>Historial del vehículo · separado de los cambios observados</small></span>
			<span class="telemetry-history-summary-state">{total ? `${count(total)} lecturas` : 'Ver historial'} <span aria-hidden="true">⌄</span></span>
		</summary>
		<div class="telemetry-history-content" aria-busy={loading}>
			<p class="telemetry-history-note">Son lecturas comunicadas por el proveedor, no eventos ni una confirmación del estado físico actual. Los campos ausentes se muestran como “Sin dato”.</p>
			{#if quality || collectorStatus}
				<details class="telemetry-diagnostics">
					<summary>Diagnóstico de la lectura más reciente</summary>
					{#if quality}
						<dl class="telemetry-diagnostic-grid">
							<div><dt>Calidad temporal</dt><dd>{qualityText(quality.temporal_status)}</dd></div>
							<div><dt>Antigüedad de la observación</dt><dd>{ageText(quality.age_seconds)}</dd></div>
							<div><dt>Motivo</dt><dd>{reasonText(quality.reason_code)}</dd></div>
							<div><dt>Vigencia física</dt><dd>Pendiente de cualificación</dd></div>
						</dl>
					{/if}
					{#if collectorStatus}
						<dl class="telemetry-diagnostic-grid collector-diagnostics">
							<div><dt>Recolector</dt><dd>{collectorStatus.running ? 'En ejecución' : 'Detenido'}</dd></div>
							<div><dt>Lectura en curso</dt><dd>{collectorStatus.inFlight ? 'Sí' : 'No'}</dd></div>
							<div><dt>Lecturas correctas / intentos</dt><dd>{count(collectorStatus.successCount)} / {count(collectorStatus.readCount)}</dd></div>
							<div><dt>Errores registrados</dt><dd>{count(collectorStatus.errorCount)}</dd></div>
							<div><dt>Último intento</dt><dd>{fullDate(collectorStatus.lastAttemptAt)}</dd></div>
							{#if collectorStatus.lastErrorCode}<div><dt>Último código de error</dt><dd>{collectorErrorText(collectorStatus.lastErrorCode)}</dd></div>{/if}
						</dl>
					{/if}
					<p class="telemetry-history-note">El diagnóstico corresponde a la última respuesta de Pulse; no se atribuye a cada snapshot histórico. “Reciente” solo describe la hora de observación, no la vigencia física de la señal.</p>
				</details>
			{/if}

			{#if !online}
				<p class="telemetry-history-state" role="status">Sin conexión. El historial se consulta desde Pulse y no se guarda en este dispositivo.</p>
			{:else if loading && snapshots.length === 0}
				<p class="telemetry-history-state" role="status" aria-live="polite">Consultando lecturas…</p>
			{:else if error && snapshots.length === 0}
				<div class="telemetry-history-error" role="alert"><p>{error}</p><button class="button button-secondary button-small" type="button" disabled={!online || loading} onclick={retryPage}>Reintentar</button></div>
			{:else if total === 0}
				<p class="telemetry-history-state">No hay lecturas recibidas guardadas.</p>
			{:else}
				{#if error}<div class="telemetry-history-error" role="alert"><p>{error} Se conserva esta página mientras se muestra.</p><button class="text-button" type="button" disabled={!online || loading} onclick={retryPage}>Reintentar</button></div>{/if}
				<div class="telemetry-reading-list">
					{#each snapshots as snapshot (snapshot.id)}
						<article class="telemetry-reading">
							<header>
								<div><span class="section-label">Lectura recibida</span><h3>{snapshot.source || 'Fuente no indicada'}</h3></div>
								<span class="telemetry-physical-pending">Vigencia física pendiente</span>
							</header>
							<dl class="telemetry-signal-list">
								{#each signalFields as field (field.key)}
									<div><dt>{field.label}</dt><dd>{signalValue(snapshot, field.key)}<small>{fieldSource(snapshot, field.key)}</small></dd></div>
								{/each}
							</dl>
							<details class="telemetry-reading-times">
								<summary>Observación y recepción</summary>
								<dl class="telemetry-diagnostic-grid">
									<div><dt>Observada por la fuente</dt><dd>{fullDate(snapshot.observed_at)}</dd></div>
									<div><dt>Recibida por el proveedor</dt><dd>{fullDate(snapshot.provider_received_at)}</dd></div>
									<div><dt>Recibida por Pulse</dt><dd>{fullDate(snapshot.server_received_at)}</dd></div>
									<div><dt>Calidad temporal por registro</dt><dd>No incluida en este endpoint</dd></div>
									<div><dt>Calidad física</dt><dd>Pendiente de cualificación</dd></div>
								</dl>
							</details>
						</article>
					{/each}
				</div>
				<nav class="telemetry-history-pagination" aria-label="Páginas de lecturas recibidas">
					<p role="status" aria-live="polite">Página {currentPage} de {pageCount} · {count(total)} lecturas</p>
					<div><button class="button button-secondary button-small" type="button" disabled={!online || loading || offset === 0} onclick={() => movePage(offset - pageSize)}>Anterior</button><button class="button button-secondary button-small" type="button" disabled={!online || loading || offset >= lastOffset} onclick={() => movePage(offset + pageSize)}>Siguiente</button></div>
				</nav>
			{/if}
		</div>
	</details>
</section>

<style>
	.telemetry-history-section { grid-column: 1 / -1; min-width: 0; margin-top: 4px; border-top: 1px solid var(--line); }
	.telemetry-history-section > details > summary { display: flex; min-height: 70px; justify-content: space-between; gap: 16px; padding: 10px 2px; }
	.telemetry-history-section > details > summary > span:first-child { display: grid; gap: 4px; }
	.telemetry-history-section > details > summary strong { color: var(--ink); font-size: 1rem; font-weight: 650; }
	.telemetry-history-section > details > summary small, .telemetry-history-summary-state { color: var(--muted); font-size: .75rem; }
	.telemetry-history-summary-state { display: inline-flex; align-items: center; gap: 9px; white-space: nowrap; }
	.telemetry-history-content { min-width: 0; padding: 0 0 18px; }
	.telemetry-history-note { margin: 6px 0 14px; color: var(--muted); font-size: .8125rem; line-height: 1.55; }
	.telemetry-diagnostics { margin: 0 0 16px; padding: 0 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
	.telemetry-diagnostics > summary, .telemetry-reading-times > summary { color: var(--forest); }
	.telemetry-diagnostic-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 20px; margin: 0; padding: 0 0 14px; }
	.telemetry-diagnostic-grid > div { min-width: 0; }
	.telemetry-diagnostic-grid dt { color: var(--muted); font-size: .75rem; }
	.telemetry-diagnostic-grid dd { margin: 4px 0 0; color: var(--ink); font-size: .8125rem; font-weight: 600; overflow-wrap: anywhere; }
	.collector-diagnostics { padding-top: 12px; border-top: 1px solid var(--line); }
	.telemetry-reading-list { display: grid; gap: 10px; }
	.telemetry-reading { min-width: 0; padding: 16px 18px; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }
	.telemetry-reading > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
	.telemetry-reading > header h3 { margin: 4px 0 0; font-size: .9375rem; font-weight: 650; overflow-wrap: anywhere; }
	.telemetry-physical-pending { padding: 4px 8px; border: 1px solid var(--line); border-radius: 99px; color: var(--muted-strong); font-size: .6875rem; white-space: nowrap; }
	.telemetry-signal-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; }
	.telemetry-signal-list > div { min-width: 0; padding: 11px 10px 10px 0; border-bottom: 1px solid var(--line); }
	.telemetry-signal-list dt { color: var(--muted); font-size: .75rem; }
	.telemetry-signal-list dd { margin: 4px 0 0; color: var(--ink); font-size: .875rem; font-weight: 650; overflow-wrap: anywhere; }
	.telemetry-signal-list dd small { display: block; margin-top: 3px; color: var(--muted); font-size: .6875rem; font-weight: 400; overflow-wrap: anywhere; }
	.telemetry-reading-times { margin-top: 2px; }
	.telemetry-reading-times > summary { min-height: 42px; }
	.telemetry-history-state { margin: 0; padding: 18px 0; color: var(--muted); font-size: .875rem; }
	.telemetry-history-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; }
	.telemetry-history-error p { margin: 0; color: var(--danger); font-size: .875rem; }
	.telemetry-history-pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 14px; }
	.telemetry-history-pagination p { margin: 0; color: var(--muted); font-size: .75rem; }
	.telemetry-history-pagination > div { display: flex; gap: 8px; }
	@media (max-width: 700px) {
		.telemetry-history-section > details > summary { align-items: flex-start; }
		.telemetry-diagnostic-grid { grid-template-columns: minmax(0, 1fr); gap: 10px; }
		.telemetry-signal-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.telemetry-reading { padding: 13px; }
	}
	@media (max-width: 390px) {
		.telemetry-history-section > details > summary { flex-direction: column; justify-content: center; gap: 3px; }
		.telemetry-reading > header { flex-direction: column; }
		.telemetry-history-pagination { align-items: flex-start; flex-direction: column; }
	}
</style>
