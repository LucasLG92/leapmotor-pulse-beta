<script lang="ts">
	import { onDestroy } from 'svelte';
	import { ApiError, getChargerPricingHistory, getChargerSessions, getChargerStatus } from './api';
	import { dateTime, euros, number } from './format';
	import SessionPricingDetails from './SessionPricingDetails.svelte';
	import type { ChargeQuote, ChargeQuotePayload, ChargerPricingEvent, ChargerPricingHistoryPage, ChargerSession, ChargerStatus, PageProps, PricingMethod } from './types';

	let { online, busy, revision, quoteCharge, pricingMethod = 'manual', onSessionExpired }: Pick<PageProps, 'online' | 'busy' | 'revision' | 'quoteCharge' | 'onSessionExpired'> & { pricingMethod?: PricingMethod } = $props();
	const pageSize = 20;
	const retentionLimit = 1000;
	let status = $state<ChargerStatus | null>(null);
	let sessions = $state<ChargerSession[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loading = $state(false);
	let historyLoading = $state(false);
	let error = $state('');
	let historyError = $state('');
	let pricingHistorySessionId = $state('');
	let pricingHistoryItems = $state<ChargerPricingEvent[]>([]);
	let pricingHistoryTotal = $state(0);
	let pricingHistoryOffset = $state(0);
	let pricingHistoryLoading = $state(false);
	let pricingHistoryError = $state('');
	let statusRequest = 0;
	let historyRequest = 0;
	let pricingHistoryRequest = 0;
	let pricingHistoryRetryOffset = 0;
	let quoteRequest = 0;
	let quoteBusyId = $state('');
	let quoteError = $state('');
	let quoteErrorId = $state('');
	let quotes = $state<Record<string, ChargeQuote>>({});
	let observedRevision: number | undefined;
	let observedOnline: boolean | undefined;
	let destroyed = false;
	let currentPage = $derived(Math.floor(offset / pageSize) + 1);
	let maxOffset = $derived(Math.min(Math.floor(Math.max(0, total - 1) / pageSize) * pageSize, retentionLimit - pageSize));

	$effect(() => {
		const currentRevision = revision;
		const currentlyOnline = online;
		if (currentRevision === observedRevision && currentlyOnline === observedOnline) return;
		const revisionChanged = observedRevision !== undefined && currentRevision !== observedRevision;
		observedRevision = currentRevision;
		observedOnline = currentlyOnline;
		if (revisionChanged) {
			clearQuotes();
			invalidatePricingHistory();
		}
		if (!currentlyOnline) {
			statusRequest += 1;
			historyRequest += 1;
			quoteRequest += 1;
			clearPricingHistory();
			status = null;
			sessions = [];
			total = 0;
			offset = 0;
			quotes = {};
			quoteBusyId = '';
			quoteError = '';
			loading = false;
			historyLoading = false;
			pricingHistoryLoading = false;
			error = 'El cargador solo se consulta en Pulse cuando hay conexión.';
			historyError = '';
			return;
		}
		void refresh(currentRevision);
	});

	onDestroy(() => {
		destroyed = true;
		statusRequest += 1;
		historyRequest += 1;
		clearPricingHistory();
		quoteRequest += 1;
	});

	async function refresh(expectedRevision = revision) {
		if (!online) return;
		const statusId = ++statusRequest;
		const pageId = ++historyRequest;
		loading = true;
		historyLoading = true;
		historyError = '';
		try {
			const result = await getChargerStatus();
			if (destroyed || statusId !== statusRequest || expectedRevision !== revision || !online) return;
			status = result;
			error = '';
		} catch (cause) {
			if (destroyed || statusId !== statusRequest || expectedRevision !== revision || !online) return;
			if (cause instanceof ApiError && cause.status === 401) {
				expireSession();
				return;
			}
			error = apiMessage(cause, 'No se pudo consultar el estado del cargador.');
		} finally {
			if (!destroyed && statusId === statusRequest) loading = false;
		}
		try {
			await fetchHistoryPage(offset, expectedRevision, pageId);
			if (destroyed || pageId !== historyRequest || expectedRevision !== revision || !online) return;
			if (pricingHistorySessionId) {
				if (sessions.some((session) => session.id === pricingHistorySessionId)) {
					await loadPricingHistory(pricingHistorySessionId, pricingHistoryOffset, expectedRevision);
				} else {
					clearPricingHistory();
				}
			}
		} catch (cause) {
			if (destroyed || pageId !== historyRequest || expectedRevision !== revision || !online) return;
			if (cause instanceof ApiError && cause.status === 401) {
				expireSession();
				return;
			}
			if (pricingHistorySessionId) clearPricingHistory();
			historyError = apiMessage(cause, 'No se pudo cargar el historial de evcc.');
		} finally {
			if (!destroyed && pageId === historyRequest) historyLoading = false;
		}
	}

	async function fetchHistoryPage(requestedOffset: number, expectedRevision: number, requestId: number) {
		let pageOffset = requestedOffset;
		for (let attempt = 0; attempt < 2; attempt += 1) {
			const result = await getChargerSessions(pageSize, pageOffset);
			if (destroyed || requestId !== historyRequest || expectedRevision !== revision || !online) return;
			const pageTotal = Math.min(result.total, retentionLimit);
			const lastPageOffset = Math.min(Math.floor(Math.max(0, pageTotal - 1) / pageSize) * pageSize, retentionLimit - pageSize);
			if (result.offset > lastPageOffset && attempt === 0) {
				pageOffset = lastPageOffset;
				continue;
			}
			sessions = result.items;
			total = pageTotal;
			offset = result.offset;
			return;
		}
	}

	async function loadPage(nextOffset: number, force = false) {
		if (!online || historyLoading) return;
		const safeOffset = Math.max(0, Math.min(Math.floor(nextOffset / pageSize) * pageSize, maxOffset));
		if (safeOffset === offset && !force) return;
		clearQuotes();
		clearPricingHistory();
		const requestId = ++historyRequest;
		const expectedRevision = revision;
		historyLoading = true;
		historyError = '';
		try {
			await fetchHistoryPage(safeOffset, expectedRevision, requestId);
		} catch (cause) {
			if (destroyed || requestId !== historyRequest || expectedRevision !== revision || !online) return;
			if (cause instanceof ApiError && cause.status === 401) {
				expireSession();
				return;
			}
			historyError = apiMessage(cause, 'No se pudo cargar esta página del historial.');
		} finally {
			if (!destroyed && requestId === historyRequest) historyLoading = false;
		}
	}

	function clearPricingHistory() {
		pricingHistoryRequest += 1;
		pricingHistorySessionId = '';
		pricingHistoryItems = [];
		pricingHistoryTotal = 0;
		pricingHistoryOffset = 0;
		pricingHistoryRetryOffset = 0;
		pricingHistoryLoading = false;
		pricingHistoryError = '';
	}

	function invalidatePricingHistory() {
		pricingHistoryRequest += 1;
		pricingHistoryItems = [];
		pricingHistoryTotal = 0;
		pricingHistoryLoading = false;
		pricingHistoryError = '';
		pricingHistoryRetryOffset = pricingHistoryOffset;
	}

	function expireSession() {
		clearPricingHistory();
		onSessionExpired();
	}

	function canLoadPricingHistory(): boolean {
		return online && !busy && !quoteBusyId && !pricingHistoryLoading;
	}

	function togglePricingHistory(session: ChargerSession) {
		if (pricingHistorySessionId === session.id) {
			clearPricingHistory();
			return;
		}
		if (!online || busy || quoteBusyId) return;
		pricingHistoryRequest += 1;
		pricingHistorySessionId = session.id;
		pricingHistoryItems = [];
		pricingHistoryTotal = 0;
		pricingHistoryOffset = 0;
		pricingHistoryRetryOffset = 0;
		pricingHistoryError = '';
		void loadPricingHistory(session.id, 0, revision);
	}

	async function loadPricingHistory(sessionId: string, requestedOffset: number, expectedRevision: number) {
		if (!online || pricingHistorySessionId !== sessionId || !sessions.some((session) => session.id === sessionId)) return;
		const requestId = ++pricingHistoryRequest;
		pricingHistoryRetryOffset = requestedOffset;
		pricingHistoryLoading = true;
		pricingHistoryError = '';
		try {
			let pageOffset = requestedOffset;
			for (let attempt = 0; attempt < 2; attempt += 1) {
				const result: ChargerPricingHistoryPage = await getChargerPricingHistory(sessionId, 10, pageOffset);
				if (destroyed || requestId !== pricingHistoryRequest || pricingHistorySessionId !== sessionId || expectedRevision !== revision || !online || !sessions.some((session) => session.id === sessionId)) return;
				const lastPageOffset = Math.floor(Math.max(0, result.total - 1) / 10) * 10;
				if (result.offset > lastPageOffset && attempt === 0) {
					pageOffset = lastPageOffset;
					continue;
				}
				pricingHistoryItems = result.items;
				pricingHistoryTotal = result.total;
				pricingHistoryOffset = result.offset;
				break;
			}
		} catch (cause) {
			if (destroyed || requestId !== pricingHistoryRequest || pricingHistorySessionId !== sessionId || expectedRevision !== revision || !online) return;
			if (cause instanceof ApiError && cause.status === 401) {
				expireSession();
				return;
			}
			pricingHistoryError = apiMessage(cause, 'No se pudo cargar el historial del cálculo.');
		} finally {
			if (!destroyed && requestId === pricingHistoryRequest) pricingHistoryLoading = false;
		}
	}

	function movePricingHistory(nextOffset: number) {
		if (!canLoadPricingHistory() || !pricingHistorySessionId) return;
		void loadPricingHistory(pricingHistorySessionId, nextOffset, revision);
	}

	function pricingEventLabel(kind: ChargerPricingEvent['kind']): string {
		if (kind === 'priced') return 'Coste calculado';
		if (kind === 'baseline') return 'Coste previo conservado';
		return 'Datos corregidos';
	}

	function pricingAccuracy(quote: ChargeQuote): string {
		return (quote.pricing as unknown as { accuracy?: string }).accuracy ?? 'estimated_uniform';
	}

	function durationSeconds(session: ChargerSession): number {
		const duration = (Date.parse(session.ended_at) - Date.parse(session.started_at)) / 1000;
		return Number.isFinite(duration) ? duration : -1;
	}

	function canQuote(session: ChargerSession): boolean {
		return online && !busy && !quoteBusyId && isQuoteEligible(session);
	}

	function isQuoteEligible(session: ChargerSession): boolean {
		const duration = durationSeconds(session);
		return Number.isFinite(session.kwh) && session.kwh > 0 && duration > 0 && duration <= 72 * 60 * 60;
	}

	function ineligibleReason(session: ChargerSession): string {
		if (!Number.isFinite(session.kwh) || session.kwh <= 0) return 'No se estima el coste porque la sesión comunica 0 kWh.';
		const duration = durationSeconds(session);
		if (duration > 72 * 60 * 60) return 'No se estima el coste porque el periodo supera las 72 horas.';
		return 'No se estima el coste porque el periodo comunicado no es válido.';
	}

	function clearQuotes() {
		quoteRequest += 1;
		quotes = {};
		quoteBusyId = '';
		quoteError = '';
		quoteErrorId = '';
	}

	async function quote(session: ChargerSession) {
		if (session.pricing_status !== 'disabled' || !canQuote(session)) return;
		const requestId = ++quoteRequest;
		const expectedRevision = revision;
		quoteBusyId = session.id;
		quoteError = '';
		quoteErrorId = session.id;
		quotes = {};
		const payload: ChargeQuotePayload = {
			started_at: session.started_at,
			ended_at: session.ended_at,
			kwh: session.kwh,
			pricing_method: 'pvpc'
		};
		try {
			const result = await quoteCharge(payload);
			if (destroyed || requestId !== quoteRequest || expectedRevision !== revision || !online) return;
			quotes = { [session.id]: result };
			quoteErrorId = '';
		} catch (cause) {
			if (destroyed || requestId !== quoteRequest || expectedRevision !== revision || !online) return;
			quoteError = apiMessage(cause, 'No se pudo calcular el coste estimado.');
		} finally {
			if (!destroyed && requestId === quoteRequest) quoteBusyId = '';
		}
	}

	function apiMessage(cause: unknown, fallback: string): string {
		return cause instanceof ApiError ? cause.message : fallback;
	}

	function statusLabel(value: ChargerStatus['status']): string {
		if (value === 'ready') return 'Datos recibidos';
		if (value === 'waiting') return 'Esperando datos';
		if (value === 'stale') return 'Datos antiguos';
		if (value === 'error') return 'Error de lectura';
		return 'Desactivado';
	}

	function visibleStatusLabel(value: ChargerStatus['status']): string {
		if (value === 'ready' && !error && status?.observation) return observationStateLabel(status.observation);
		return error && value === 'ready' ? 'Última lectura' : statusLabel(value);
	}

	function observationStateLabel(observation: NonNullable<ChargerStatus['observation']>): string {
		if (observation.charging && observation.connected) return 'Cargando';
		if (observation.connected) return 'Conectado, sin carga';
		return 'Desconectado';
	}

	function isCurrentObservation(): boolean {
		return status?.status === 'ready' && !error;
	}

	function historyLabel(value: ChargerStatus['history_status']): string {
		if (value === 'ready') return 'disponible';
		if (value === 'waiting') return 'esperando';
		if (value === 'error') return 'con error';
		return 'desactivado';
	}

	function durationLabel(seconds: number | null): string {
		if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return '—';
		const minutes = Math.floor(seconds / 60);
		const hours = Math.floor(minutes / 60);
		const remainingMinutes = minutes % 60;
		return hours > 0 ? `${hours} h ${remainingMinutes} min` : `${minutes} min`;
	}
</script>

<section class="charger-panel card" aria-labelledby="charger-title">
	<div class="charger-heading">
		<div>
			<h2 id="charger-title">Tu cargador</h2>
			<p>Al confirmar evcc el cierre, Pulse guarda inicio, fin y kWh. {pricingMethod === 'pvpc' ? 'Con PVPC estima el coste automáticamente, sin formulario.' : 'La comparación con PVPC se solicita manualmente.'}</p>
		</div>
		{#if status}
			<span class="charger-state" class:state-ready={status.status === 'ready' && !error} class:state-muted={status.status === 'disabled' || status.status === 'waiting'} class:state-warning={status.status === 'stale' || status.status === 'error' || !!error} aria-label={`Estado del cargador: ${visibleStatusLabel(status.status)}`}>
				<span aria-hidden="true"></span>{visibleStatusLabel(status.status)}
			</span>
		{/if}
	</div>

	{#if !online}
		<div class="charger-notice" role="status">Sin conexión. Se han borrado de esta vista las observaciones y respuestas privadas recibidas del servidor.</div>
	{:else if !status && loading}
		<div class="charger-loading" role="status">Consultando el estado guardado por Pulse…</div>
	{:else if !status && error}
		<div class="charger-error" role="alert"><div><strong>Estado no disponible</strong><p>{error}</p></div><button class="button button-secondary" type="button" disabled={loading} onclick={() => void refresh(revision)}>{loading ? 'Consultando…' : 'Reintentar'}</button></div>
	{:else if status}
		{#if status.status === 'disabled'}
			<div class="charger-disabled"><strong>Cargador no configurado</strong><span>evcc no está habilitado en Pulse. No hay lecturas actuales que mostrar.</span></div>
		{:else}
			{#if error}<div class="charger-error" role="alert"><span>{error} Se conserva la última respuesta recibida.</span><button class="text-button" type="button" disabled={loading} onclick={() => void refresh(revision)}>Reintentar</button></div>{/if}
			{#if status.status === 'waiting'}<p class="charger-message" role="status">{status.message}</p>{/if}
			{#if status.status === 'stale' || status.status === 'error'}<p class="charger-message charger-message-warning" role="status">{status.message} {status.observation ? 'La observación conservada puede ser antigua.' : ''}</p>{/if}
			{#if status.observation}
				{@const observation = status.observation}
				{@const currentObservation = isCurrentObservation()}
				<div class="charger-observation" aria-label={currentObservation ? 'Estado actual comunicado por evcc' : 'Última lectura conservada del cargador'}>
					{#if !currentObservation}<p class="observation-age">Última lectura · estado de entonces</p>{/if}
					<div class="charger-live-heading" class:observation-past={!currentObservation}>
						<div><span class="charger-live-indicator" class:indicator-active={currentObservation && observation.charging && observation.connected} aria-hidden="true"></span><strong>{observation.loadpoint}</strong></div>
						<strong class="observation-state">{observationStateLabel(observation)}{currentObservation ? '' : ' · dato anterior'}</strong>
					</div>
					<div class="charger-metrics">
						<div><span>Potencia comunicada</span><strong>{observation.power_w === null ? '—' : `${number(observation.power_w / 1000)} kW`}</strong></div>
						<div><span>Energía comunicada</span><strong>{observation.charged_kwh === null ? '—' : `${number(observation.charged_kwh)} kWh`}</strong></div>
					</div>
					<details class="charger-details observation-details">
						<summary>Hora y detalles de la lectura</summary>
						<div class="charger-metrics observation-extra">
							<div><span>Tiempo activo comunicado</span><strong>{durationLabel(observation.active_seconds)}</strong></div>
						</div>
						<p>{currentObservation ? 'Recibida por Pulse' : 'Última lectura guardada'}: {dateTime(observation.received_at)}. Es la hora de recepción del servidor; no acredita cuándo se midieron físicamente los valores.</p>
					</details>
				</div>
			{:else if status.status !== 'waiting'}
				<div class="charger-empty">No hay lecturas disponibles ahora.</div>
			{/if}
		{/if}
	{/if}

	{#if status?.status !== 'disabled' || sessions.length > 0 || historyLoading || historyError}
		<div class="charger-history">
			<div class="charger-history-heading">
				<div><h3>Cargas detectadas</h3><p>Sesiones comunicadas por evcc</p></div>
				<span class="badge">{number(total)} {total === 1 ? 'carga' : 'cargas'}</span>
			</div>
			<details class="charger-details history-details">
				<summary>Fuente y cobertura</summary>
				<p>Fuente: evcc. Historial {status ? historyLabel(status.history_status) : 'guardado por Pulse'}; {status ? `${status.history_months} ${status.history_months === 1 ? 'mes configurado' : 'meses configurados'} y hasta ${number(Math.min(status.retention_limit, retentionLimit))} sesiones retenidas` : 'cobertura todavía no disponible'}.</p>
				{#if pricingMethod === 'pvpc'}
				<p>Con PVPC, Pulse estima el coste cuando evcc confirma el cierre y hay datos suficientes, sin formulario. Las estimaciones indican si el reparto es uniforme o por intervalos. Los intervalos de evcc siguen siendo candidatos hasta que Pulse los vincula a una sesión y valida la cobertura completa; las estimaciones anteriores conservan su cálculo uniforme.</p>
				{:else}
				<p>Con precio fijo manual, Pulse guarda inicio, fin y kWh al confirmar evcc el cierre. Puedes pedir una comparación con PVPC para cada sesión elegible; no se calcula automáticamente. Las estimaciones anteriores conservan su cálculo uniforme y los intervalos de evcc siguen siendo candidatos hasta que Pulse los vincula a una sesión y valida la cobertura completa.</p>
				{/if}
				<p>La lista de evcc se mantiene separada de los registros manuales y de demostración. Pulse no modifica ni elimina las sesiones importadas.</p>
				{#if status?.energy_history?.enabled}
					<p><strong>Energía por tramos.</strong> {status.energy_history.message}</p>
					{#if status.energy_history.retained_intervals > 0}
						<p>{number(status.energy_history.retained_intervals)} tramos de 15 minutos guardados del punto de carga. Se conservan como candidatos y no se suman a tus cargas ni a sus costes. Última recepción guardada: {dateTime(status.energy_history.last_received_at)}.</p>
					{/if}
				{/if}
			</details>
			{#if historyError}
				<div class="charger-error" role="alert"><span>{historyError}{sessions.length > 0 ? ' Se muestran las últimas sesiones guardadas.' : ''}</span><button class="text-button" type="button" disabled={historyLoading || !online} onclick={() => void loadPage(offset, true)}>Reintentar</button></div>
			{:else if status?.history_status === 'error' && sessions.length > 0}
				<div class="charger-message charger-message-warning" role="status">{status.history_message ?? 'No se pudo actualizar el historial.'} Se muestran las últimas sesiones guardadas.</div>
			{/if}
			{#if historyLoading && sessions.length === 0}
				<div class="charger-loading" role="status">Cargando historial de evcc…</div>
			{:else if sessions.length === 0}
				<div class="charger-empty">No hay sesiones de evcc retenidas.</div>
			{:else}
				<ul class="charger-session-list">
					{#each sessions as session (session.id)}
						{@const displayedQuote = session.pvpc ?? quotes[session.id]}
						<li class="charger-session">
							<div class="session-topline"><strong>{dateTime(session.started_at)}</strong><span>{session.loadpoint}</span></div>
							<div class="session-primary">
								<div class="session-energy"><strong>{number(session.kwh)} <small>kWh</small></strong><span>Energía comunicada</span></div>
								<div class="session-cost">
									{#if displayedQuote}
										<div class="session-cost-total"><strong>{euros(displayedQuote.cost_eur)}</strong><span>{pricingAccuracy(displayedQuote) === 'estimated_intervals' ? 'Estimación por intervalos · sin impuestos' : 'Coste PVPC estimado · sin impuestos'}</span></div>
										<SessionPricingDetails quote={displayedQuote} sessionStartedAt={session.started_at} sessionEndedAt={session.ended_at} sessionKwh={session.kwh} />
									{:else if session.pricing_status === 'ready'}
										<div class="session-cost-state" role="status"><strong>Coste PVPC calculado</strong><span>Guardado en Pulse; el importe no viene en esta respuesta.</span></div>
									{:else if session.pricing_status === 'pending'}
										<div class="session-cost-state" role="status"><strong>Coste PVPC pendiente</strong><span>Aún no hay un importe disponible.</span></div>
									{:else if session.pricing_status === 'ineligible'}
										<p class="session-pricing-message" role="status">{ineligibleReason(session)}</p>
									{:else if isQuoteEligible(session)}
										<button class="button button-quiet quote-session" type="button" disabled={!online || busy || !!quoteBusyId} onclick={() => void quote(session)}>{quoteBusyId === session.id ? 'Calculando…' : 'Comparar con PVPC'}</button>
									{:else}
										<p class="session-pricing-message" role="status">{ineligibleReason(session)}</p>
									{/if}
								</div>
							</div>
							<div class="session-secondary-metrics"><span>{durationLabel(session.active_seconds)} activos</span><span>Fin {dateTime(session.ended_at)}</span></div>
							<button class="button button-quiet pricing-history-toggle" type="button" aria-expanded={pricingHistorySessionId === session.id} aria-controls={`pricing-history-${session.id}`} disabled={pricingHistorySessionId !== session.id && (!online || busy || !!quoteBusyId)} onclick={() => togglePricingHistory(session)}>{pricingHistorySessionId === session.id ? 'Cerrar historial del cálculo' : 'Historial del cálculo'}</button>
							<section id={`pricing-history-${session.id}`} class="pricing-history-panel" hidden={pricingHistorySessionId !== session.id} aria-label="Historial del cálculo">
							{#if pricingHistorySessionId === session.id}
								<div class="pricing-history-heading">
									<div><h4>Historial del cálculo</h4><p>{number(pricingHistoryTotal)} {pricingHistoryTotal === 1 ? 'evento conservado' : 'eventos conservados'} · retención global de hasta 2048 eventos</p></div>
								</div>
				{#if pricingHistoryLoading}<p class="pricing-history-status" role="status">Consultando el historial del cálculo…</p>{/if}
				{#if pricingHistoryError}
					<div class="charger-error" role="alert"><span>{pricingHistoryError}</span><button class="button button-secondary" type="button" disabled={!online || busy || !!quoteBusyId || pricingHistoryLoading} onclick={() => movePricingHistory(pricingHistoryRetryOffset)}>{pricingHistoryLoading ? 'Consultando…' : 'Reintentar'}</button></div>
				{/if}
				{#if pricingHistoryItems.length === 0}
					{#if !pricingHistoryError && !pricingHistoryLoading}<p class="pricing-history-status">No hay cálculos conservados para esta sesión.</p>{/if}
				{:else}
									<ul class="pricing-event-list">
										{#each pricingHistoryItems as event (event.id)}
											<li class="pricing-event">
												<div class="pricing-event-heading"><strong>{pricingEventLabel(event.kind)}</strong><span>Guardado {dateTime(event.recorded_at)}</span></div>
												<div class="session-primary pricing-event-primary">
								<div class="session-energy"><strong>{number(event.session.kwh)} <small>kWh</small></strong><span>Energía de la sesión</span></div>
								<div class="session-cost">
									<div class="session-cost-total"><strong>{euros(event.quote.cost_eur)}</strong><span>{pricingAccuracy(event.quote) === 'estimated_intervals' ? 'Estimación por intervalos · sin impuestos' : 'Coste PVPC estimado · sin impuestos'}</span></div>
									<SessionPricingDetails quote={event.quote} sessionStartedAt={event.session.started_at} sessionEndedAt={event.session.ended_at} sessionKwh={event.session.kwh} summary="Desglose del cálculo" uniformVariant="history" />
								</div>
												</div>
												<div class="pricing-event-session"><span>Periodo previo: {dateTime(event.session.started_at)} → {dateTime(event.session.ended_at)}</span></div>
												{#if event.kind === 'invalidated' && event.corrected_session}
													<div class="pricing-correction"><strong>Datos corregidos</strong><span>Antes: {number(event.session.kwh)} kWh · fin {dateTime(event.session.ended_at)}</span><span>Ahora: {number(event.corrected_session.kwh)} kWh · fin {dateTime(event.corrected_session.ended_at)}</span></div>
												{/if}
												{#if event.kind === 'baseline'}<p class="pricing-history-status">Este cálculo previo se conserva desde que comenzó el registro; los cálculos anteriores no se reconstruyen.</p>{/if}
											</li>
										{/each}
					</ul>
				{/if}
								{#if pricingHistoryTotal > 10}
									<nav class="charger-pagination pricing-history-pagination" aria-label="Paginación del historial del cálculo">
										<span>Página {Math.floor(pricingHistoryOffset / 10) + 1} · {number(pricingHistoryOffset + 1)}–{number(Math.min(pricingHistoryOffset + pricingHistoryItems.length, pricingHistoryTotal))} de {number(pricingHistoryTotal)} eventos conservados</span>
										<div><button class="button button-secondary" type="button" disabled={!online || busy || !!quoteBusyId || pricingHistoryLoading || pricingHistoryOffset === 0} onclick={() => movePricingHistory(Math.max(0, pricingHistoryOffset - 10))}>Anterior</button><button class="button button-secondary" type="button" disabled={!online || busy || !!quoteBusyId || pricingHistoryLoading || pricingHistoryOffset + pricingHistoryItems.length >= pricingHistoryTotal} onclick={() => movePricingHistory(pricingHistoryOffset + 10)}>Siguiente</button></div>
									</nav>
								{/if}
							{/if}
							</section>
						</li>
					{/each}
				</ul>
				{#if quoteError && sessions.some((session) => session.id === quoteErrorId && session.pricing_status === 'disabled')}<div class="charger-error" role="alert"><span>{quoteError}</span><button class="text-button" type="button" disabled={!online || busy || !!quoteBusyId} onclick={() => { const retry = sessions.find((item) => item.id === quoteErrorId); if (retry) void quote(retry); }}>Reintentar</button></div>{/if}
			{/if}
			{#if total > pageSize}
				<nav class="charger-pagination" aria-label="Paginación de las cargas detectadas">
					<span>Página {currentPage} · {number(offset + 1)}–{number(Math.min(offset + sessions.length, total))} de {number(total)}</span>
					<div><button class="button button-secondary" type="button" disabled={!online || historyLoading || offset === 0} onclick={() => void loadPage(offset - pageSize)}>Anterior</button><button class="button button-secondary" type="button" disabled={!online || historyLoading || offset >= maxOffset} onclick={() => void loadPage(offset + pageSize)}>Siguiente</button></div>
				</nav>
			{/if}
		</div>
	{/if}
</section>

<style>
	.charger-panel { display: grid; min-width: 0; max-width: 100%; gap: 16px; margin-top: 17px; padding: clamp(16px, 2.5vw, 22px); }
	.charger-heading, .charger-history-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
	.charger-heading h2 { margin: 5px 0 0; font-size: 1.5rem; font-weight: 650; letter-spacing: -.03em; overflow-wrap: anywhere; }
	.charger-heading p:not(.section-label) { max-width: 600px; margin: 7px 0 0; color: var(--muted); font-size: 1rem; line-height: 1.55; overflow-wrap: anywhere; }
	.charger-state { display: inline-flex; flex: 0 1 auto; align-items: center; gap: 7px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-soft); color: var(--muted-strong); font-size: .875rem; font-weight: 650; }
	.charger-state > span, .charger-live-indicator { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
	.charger-state.state-ready > span { background: var(--forest); }
	.charger-state.state-warning > span { background: var(--accent); }
	.charger-live-indicator.indicator-active { background: var(--forest); }
	.charger-loading, .charger-empty, .charger-disabled, .charger-notice { display: grid; gap: 5px; min-width: 0; padding: 13px 15px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); color: var(--muted-strong); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-disabled { background: var(--surface); }
	.charger-disabled strong { color: var(--ink); font-size: 1rem; }
	.charger-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; min-width: 0; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--danger) 28%, var(--line)); border-radius: 11px; background: var(--danger-soft); color: var(--muted-strong); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-error p { margin: 3px 0 0; }
	.charger-message { margin: 0; color: var(--muted-strong); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-message-warning { color: var(--accent); }
	.charger-observation { display: grid; gap: 9px; min-width: 0; padding: 13px; border: 1px solid var(--line); border-radius: 13px; background: var(--surface-raised); }
	.charger-live-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 14px; color: var(--muted); font-size: .875rem; overflow-wrap: anywhere; }
	.charger-live-heading > div { display: flex; align-items: center; gap: 8px; color: var(--ink); }
	.charger-live-heading strong { font-size: 1rem; overflow-wrap: anywhere; }
	.charger-live-heading .observation-state { color: var(--forest); text-align: end; }
	.charger-live-heading.observation-past .observation-state { color: var(--muted-strong); }
	.observation-age { margin: 0; color: var(--muted-strong); font-size: .875rem; font-weight: 680; }
	.charger-details { min-width: 0; padding: 0 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
	.charger-details summary { display: flex; min-height: 44px; align-items: center; color: var(--forest); cursor: pointer; font-size: .875rem; font-weight: 650; overflow-wrap: anywhere; }
	.charger-details summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 6px; }
	.charger-details > p { margin: 0 0 10px; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
	.charger-metrics > div { display: grid; gap: 5px; min-width: 0; }
	.charger-metrics span { color: var(--muted); font-size: .875rem; }
	.charger-observation > .charger-metrics { padding: 4px 2px 8px; }
	.charger-observation > .charger-metrics strong { overflow-wrap: anywhere; color: var(--ink); font-size: clamp(1.25rem, 4vw, 1.65rem); font-weight: 720; font-variant-numeric: tabular-nums; }
	.observation-details .charger-metrics { margin: 0 0 10px; }
	.charger-history { display: grid; gap: 11px; padding-top: 16px; border-top: 1px solid var(--line); }
	.charger-history-heading { align-items: center; }
	.charger-history-heading h3 { margin: 0; font-size: 1.25rem; font-weight: 670; overflow-wrap: anywhere; }
	.charger-history-heading p { margin: 5px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.45; overflow-wrap: anywhere; }
	.charger-session-list { display: grid; min-width: 0; gap: 8px; margin: 0; padding: 0; list-style: none; }
	.charger-session { display: grid; gap: 9px; min-width: 0; padding: 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-raised); }
	.session-topline, .session-secondary-metrics { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 5px 12px; }
	.session-topline strong { font-size: .75rem; font-weight: 670; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.session-topline span, .session-secondary-metrics { color: var(--muted); font-size: .875rem; overflow-wrap: anywhere; }
	.session-primary { display: grid; grid-template-columns: minmax(82px, .65fr) minmax(0, 1.35fr); align-items: start; gap: 12px; }
	.session-energy, .session-cost { display: grid; align-content: start; gap: 5px; min-width: 0; }
	.session-energy > strong { color: var(--ink); font-size: 1.25rem; font-weight: 680; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.session-energy small { color: var(--muted); font-size: .875rem; font-weight: 520; }
	.session-energy > span, .session-cost-total > span { color: var(--muted); font-size: .875rem; line-height: 1.35; overflow-wrap: anywhere; }
	.session-cost-total { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 2px 8px; min-width: 0; }
	.session-cost-total > strong { color: var(--ink); font-size: 1.125rem; font-weight: 680; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.session-cost-state { display: grid; gap: 3px; color: var(--muted); font-size: .875rem; line-height: 1.45; overflow-wrap: anywhere; }
	.session-cost-state strong { color: var(--ink); font-size: .875rem; }
	.session-pricing-message { margin: 0; min-width: 0; padding: 9px 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-soft); color: var(--muted); font-size: .875rem; line-height: 1.45; overflow-wrap: anywhere; }
	.pricing-history-toggle { min-height: 48px; width: 100%; padding: 9px 12px; border: 1px solid var(--line); border-radius: 9px; color: var(--forest); font-size: .875rem; font-weight: 660; text-align: start; }
	.pricing-history-toggle:disabled { color: var(--muted); }
	.pricing-history-panel { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; gap: 10px; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
	.pricing-history-panel[hidden] { display: none; }
	.pricing-history-heading { display: flex; min-width: 0; align-items: flex-start; justify-content: space-between; gap: 10px; }
	.pricing-history-heading > div { min-width: 0; }
	.pricing-history-heading h4 { margin: 0; color: var(--ink); font-size: 1rem; font-weight: 670; overflow-wrap: anywhere; }
	.pricing-history-heading p, .pricing-history-status { margin: 4px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	.pricing-event-list { display: grid; min-width: 0; gap: 8px; margin: 0; padding: 0; list-style: none; }
	.pricing-event { display: grid; min-width: 0; gap: 8px; padding: 11px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-raised); }
	.pricing-event-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 10px; }
	.pricing-event-heading strong { color: var(--ink); font-size: .875rem; font-weight: 680; overflow-wrap: anywhere; }
	.pricing-event-heading span, .pricing-event-session { color: var(--muted); font-size: .75rem; line-height: 1.45; overflow-wrap: anywhere; }
	.pricing-event-primary { grid-template-columns: minmax(82px, .65fr) minmax(0, 1.35fr); }
	.pricing-correction { display: grid; gap: 3px; padding: 9px 10px; border-inline-start: 1px solid var(--accent); background: var(--surface-soft); color: var(--muted-strong); font-size: .875rem; line-height: 1.45; overflow-wrap: anywhere; }
	.pricing-correction strong { color: var(--ink); }
	.pricing-history-pagination { margin-top: 2px; }
	.pricing-history-panel .pricing-history-pagination .button { min-height: 48px; }
	.pricing-history-pagination > div { flex-wrap: wrap; }
	.pricing-history-panel .pricing-history-pagination .button { min-width: 0; white-space: normal; overflow-wrap: anywhere; }
	.pricing-history-panel .charger-error :global(.button) { min-height: 48px; }
	.quote-session { min-height: 44px; width: 100%; padding: 8px 12px; border: 1px solid var(--line); border-radius: 9px; color: var(--forest); font-size: .875rem; font-weight: 660; }
	.quote-session:disabled { color: var(--muted); }
	.session-pricing-message { margin: 0; min-width: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; color: var(--muted); font-size: .75rem; }
	.charger-pagination > div { display: flex; gap: 8px; }
	.charger-pagination .button, .charger-error :global(.button) { min-height: 44px; font-size: .875rem; }
	@media (max-width: 520px) {
		.charger-heading, .charger-history-heading { align-items: flex-start; }
		.charger-heading { flex-direction: column; }
		.charger-state { max-width: 100%; }
		.session-primary { grid-template-columns: minmax(0, 1fr); gap: 10px; }
		.pricing-event-primary { grid-template-columns: minmax(0, 1fr); }
		.charger-observation > .charger-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
		.charger-metrics > div { min-width: 0; }
		.charger-history-heading { flex-wrap: wrap; }
		.charger-pagination { align-items: stretch; flex-direction: column; }
		.charger-pagination > div .button { flex: 1; }
	}
	@media (max-width: 360px) { .charger-panel { padding: 14px; } .charger-observation { padding: 12px; } .charger-error { align-items: stretch; } .charger-observation > .charger-metrics { grid-template-columns: 1fr; } .charger-live-heading .observation-state { text-align: start; } }
</style>
