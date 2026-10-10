<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError, getObservedEvents } from './api';
	import { count } from './format';
	import type { Mode, ObservedEvent, ObservedEventPage } from './types';

	let { online, revision, mode, onSessionExpired }: {
		online: boolean;
		revision: number;
		mode: Mode;
		onSessionExpired: () => void;
	} = $props();
	const pageSize = 20;
	const retentionLimit = 1_000;
	let events = $state<ObservedEvent[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let requestedOffset = $state(0);
	let loading = $state(true);
	let initialLoadComplete = $state(false);
	let initialLoadTimer: ReturnType<typeof setTimeout> | undefined;
	let error = $state('');
	let retry = $state(0);
	let mounted = $state(false);
	let destroyed = false;
	let requestGeneration = 0;
	let controller: AbortController | null = null;
	let lastRequestKey = '';
	let lastOnline: boolean | undefined;
	let lastMode: Mode | undefined;
	let currentPage = $derived(Math.floor(offset / pageSize) + 1);
	let maxOffset = $derived(Math.min(Math.floor(Math.max(0, total - 1) / pageSize) * pageSize, retentionLimit - pageSize));

	$effect(() => {
		const ready = mounted;
		const connected = online;
		const currentMode = mode;
		const currentRevision = revision;
		const pageToLoad = requestedOffset;
		const retryNumber = retry;
		if (!ready) return;
		if (currentMode === 'demo' || !connected) {
			cancelRequest();
			events = [];
			total = 0;
			offset = 0;
			requestedOffset = 0;
			loading = false;
			lastRequestKey = '';
			lastMode = currentMode;
			lastOnline = connected;
			error = !connected ? 'Sin conexión. El historial se carga desde Pulse y no se guarda en este dispositivo.' : '';
			return;
		}
		if (lastMode !== currentMode || lastOnline !== connected) lastRequestKey = '';
		lastMode = currentMode;
		lastOnline = connected;
		const key = `${currentRevision}:${currentMode}:${pageToLoad}:${retryNumber}`;
		if (key === lastRequestKey) return;
		lastRequestKey = key;
		void loadPage(pageToLoad, currentRevision, currentMode);
	});

	onMount(() => {
		mounted = true;
		// A slow first request must not keep the rest of the page unavailable.
		initialLoadTimer = setTimeout(() => {
			finishInitialLoad();
			if (!loading) return;
			cancelRequest();
			loading = false;
			error = 'El historial tardó demasiado. Vuelve a intentarlo.';
		}, 8_000);
	});

	onDestroy(() => {
		destroyed = true;
		mounted = false;
		if (initialLoadTimer) clearTimeout(initialLoadTimer);
		cancelRequest();
	});

	function finishInitialLoad() {
		initialLoadComplete = true;
		if (initialLoadTimer) clearTimeout(initialLoadTimer);
		initialLoadTimer = undefined;
	}

	function cancelRequest() {
		requestGeneration += 1;
		controller?.abort();
		controller = null;
	}

	function isCurrent(generation: number, expectedRevision: number, expectedMode: Mode): boolean {
		return mounted && !destroyed && generation === requestGeneration && expectedRevision === revision && expectedMode === mode && online;
	}

	async function loadPage(targetOffset: number, expectedRevision: number, expectedMode: Mode) {
		if (!mounted || destroyed || !online || expectedMode === 'demo') return;
		controller?.abort();
		const abortController = new AbortController();
		controller = abortController;
		const generation = ++requestGeneration;
		let pageOffset = Math.max(0, Math.min(Math.floor(targetOffset / pageSize) * pageSize, retentionLimit - pageSize));
		loading = true;
		error = '';
		try {
			let result: ObservedEventPage | null = null;
			for (let attempt = 0; attempt < 3; attempt += 1) {
				result = await getObservedEvents(pageSize, pageOffset, abortController.signal);
				if (!isCurrent(generation, expectedRevision, expectedMode)) return;
				const safeTotal = Math.min(result.total, retentionLimit);
				const lastOffset = Math.min(Math.floor(Math.max(0, safeTotal - 1) / pageSize) * pageSize, retentionLimit - pageSize);
				if (pageOffset > lastOffset && attempt < 2) {
					pageOffset = lastOffset;
					continue;
				}
				if (pageOffset > lastOffset || result.offset !== pageOffset) {
					throw new Error('El historial cambió durante la carga. Vuelve a intentarlo.');
				}
				break;
			}
			if (!result || !isCurrent(generation, expectedRevision, expectedMode)) return;
			const safeTotal = Math.min(result.total, retentionLimit);
			const safeOffset = pageOffset;
			lastRequestKey = `${expectedRevision}:${expectedMode}:${safeOffset}:${retry}`;
			events = result.items;
			total = safeTotal;
			offset = safeOffset;
			requestedOffset = safeOffset;
		} catch (cause) {
			if (!isCurrent(generation, expectedRevision, expectedMode)) return;
			if (cause instanceof DOMException && cause.name === 'AbortError') return;
			if (cause instanceof ApiError && cause.status === 401) {
				onSessionExpired();
				return;
			}
			error = cause instanceof ApiError ? cause.message : 'No se pudo cargar el historial de cambios.';
		} finally {
			if (isCurrent(generation, expectedRevision, expectedMode)) {
				loading = false;
				finishInitialLoad();
			}
			if (controller === abortController) controller = null;
		}
	}

	function loadOffset(nextOffset: number) {
		if (!online || loading) return;
		const safeOffset = Math.max(0, Math.min(Math.floor(nextOffset / pageSize) * pageSize, maxOffset));
		if (safeOffset === requestedOffset) {
			if (error && safeOffset !== offset) retry += 1;
			return;
		}
		requestedOffset = safeOffset;
	}

	function retryPage() {
		if (!online || loading) return;
		retry += 1;
	}

	function stateText(event: ObservedEvent, value = event.after): string {
		if (event.field === 'locked') return value ? 'bloqueado' : 'desbloqueado';
		return value ? 'activa' : 'no activa';
	}

	function fieldText(event: ObservedEvent): string {
		return event.field === 'locked' ? 'Cierre indicado' : 'Carga indicada';
	}

	function fullDate(value: string, includeSeconds = false): string {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return 'Fecha no disponible';
		return new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: includeSeconds ? 'medium' : 'short' }).format(date);
	}
</script>

<section class="observed-section" aria-labelledby="observed-events-title" data-initial-loading={loading && events.length === 0 && !initialLoadComplete}>
	<div class="section-heading">
		<div><h2 id="observed-events-title">Cambios observados</h2><p>Un cambio compara dos lecturas recibidas; no confirma una acción física.</p></div>
		<span class="badge">{count(total)} {total === 1 ? 'cambio' : 'cambios'}</span>
	</div>

	{#if loading && events.length === 0}
		<div class="empty-state" role="status" aria-live="polite"><span class="empty-mark">…</span><h3>Cargando historial</h3><p>Pulse consulta los cambios disponibles.</p></div>
	{:else if error && events.length === 0}
		<div class="observed-error" role="alert"><p>{error}</p><button class="button button-secondary" type="button" disabled={!online || loading} onclick={retryPage}>Reintentar</button></div>
	{:else if total === 0}
		<div class="empty-state"><span class="empty-mark"><span aria-hidden="true">—</span></span><h3>No hay cambios recibidos</h3><p>Todavía no se han registrado cambios. Para registrarlos, Pulse necesita lecturas válidas de cierre o carga. La primera lectura solo fija una referencia; se mostrará un cambio cuando una lectura posterior indique un valor distinto.</p></div>
	{:else}
		{#if error}<div class="observed-error observed-error-inline" role="alert"><p>{error} Se conserva la última página recibida.</p><button class="text-button" type="button" disabled={!online || loading} onclick={retryPage}>Reintentar</button></div>{/if}
		<div class="observed-list card" aria-busy={loading}>
			{#each events as event (event.id)}
				<article class="observed-row">
					<span class="observed-mark" aria-hidden="true"><span></span></span>
					<div class="observed-main">
						<strong>{fieldText(event)}: <span class="sr-only">{'cambió de '}</span>{stateText(event, event.before)} <span aria-hidden="true">→</span><span class="sr-only">{' a '}</span> {stateText(event)}</strong>
						<p>Lectura observada: <time datetime={event.observed_at}>{fullDate(event.observed_at)}</time></p>
						<details>
							<summary>Ver recepción y fuente</summary>
							<dl>
								<div><dt>Observada anteriormente</dt><dd><time datetime={event.previous_observed_at}>{fullDate(event.previous_observed_at, true)}</time></dd></div>
									<div><dt>Recibida anteriormente</dt><dd><time datetime={event.previous_received_at}>{fullDate(event.previous_received_at, true)}</time></dd></div>
									<div><dt>Recibida</dt><dd><time datetime={event.received_at}>{fullDate(event.received_at, true)}</time></dd></div>
									<div><dt>Fuente</dt><dd>{event.source}</dd></div>
							</dl>
						</details>
					</div>
				</article>
			{/each}
		</div>
		<nav class="observed-pagination" aria-label="Páginas del historial">
			<p role="status" aria-live="polite">Página {currentPage} de {Math.max(1, Math.ceil(total / pageSize))} · {count(total)} cambios</p>
			<div><button class="button button-secondary" type="button" disabled={!online || loading || offset === 0} onclick={() => loadOffset(offset - pageSize)}>Anterior</button><button class="button button-secondary" type="button" disabled={!online || loading || offset >= maxOffset} onclick={() => loadOffset(offset + pageSize)}>Siguiente</button></div>
		</nav>
	{/if}
</section>

<style>
	.observed-section { margin-top: 29px; }
	.observed-section .section-heading { margin-top: 0; }
	.observed-section .section-heading > div { min-width: 0; }
	.observed-list { overflow: hidden; }
	.observed-row { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; padding: 17px 18px; border-bottom: 1px solid var(--line); }
	.observed-row:last-child { border-bottom: 0; }
	.observed-mark { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 10px; background: var(--forest-soft); }
	.observed-mark span { width: 10px; height: 10px; border: 2px solid var(--forest); border-radius: 50%; }
	.observed-main { min-width: 0; }
	.observed-main > strong { display: block; font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.observed-main > p { margin: 5px 0; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	details { margin-top: 3px; }
	details summary { display: inline-flex; min-height: 44px; align-items: center; color: var(--muted-strong); font-size: .875rem; text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 3px; cursor: pointer; }
	details dl { display: grid; gap: 9px; margin: 7px 0 4px; padding: 12px; border-radius: 10px; background: var(--surface-soft); }
	details dl > div { display: grid; grid-template-columns: minmax(130px, .75fr) minmax(0, 1.25fr); gap: 8px; }
	details dt, details dd { margin: 0; font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	details dt { color: var(--muted); }
	details dd { color: var(--ink); font-weight: 560; }
	.observed-pagination { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; }
	.observed-pagination p { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.observed-pagination > div { display: flex; flex-wrap: wrap; gap: 8px; }
	.observed-error { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px; border: 1px solid var(--danger); border-radius: 12px; background: var(--danger-soft); color: var(--ink); }
	.observed-error p { margin: 0; font-size: .875rem; line-height: 1.55; overflow-wrap: anywhere; }
	.observed-error-inline { margin-bottom: 10px; }
	@media (max-width: 540px) { .observed-pagination { align-items: flex-start; flex-direction: column; } details dl > div { grid-template-columns: 1fr; gap: 1px; } .observed-error { align-items: flex-start; flex-direction: column; } }
</style>
