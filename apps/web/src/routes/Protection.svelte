<script lang="ts">
	import Glyph from '../lib/Glyph.svelte';
	import ObservedEventsPanel from '../lib/ObservedEventsPanel.svelte';
	import SecuritySignalsPanel from '../lib/SecuritySignalsPanel.svelte';
	import TelemetryHistoryPanel from '../lib/TelemetryHistoryPanel.svelte';
	import { count, dateTime } from '../lib/format';
	import type { PageProps } from '../lib/types';

	let { pulseState, busy, online, revision, mutate, confirmAction, onSessionExpired }: PageProps = $props();
	let protection = $derived(pulseState.protection);
	let events = $derived(protection.events);

	async function toggleProtection() {
		if (pulseState.mode !== 'demo' || busy) return;
		const enabled = !protection.enabled;
		if (enabled) {
			const accepted = await confirmAction(
				'Activar protección simulada',
				'La protección de esta versión solo permite probar eventos ficticios dentro de la demostración. No usa cámaras ni vigila el coche.',
				'Activar simulación'
			);
			if (!accepted) return;
		}
		await mutate('/api/protection', 'PUT', { enabled }, enabled ? 'La protección simulada está activa.' : 'La protección simulada está desactivada.');
	}

	async function addExampleEvent() {
		if (pulseState.mode !== 'demo' || !protection.enabled || busy) return;
		await mutate('/api/protection/event', 'POST', {}, 'El evento ficticio se ha añadido.');
	}

	async function enableDemo() {
		const accepted = await confirmAction(
			'Explorar la demostración',
			'La demostración utiliza datos y eventos ficticios. No activa una conexión física ni envía órdenes.',
			'Activar demo'
		);
		if (accepted) await mutate('/api/demo', 'POST', { enabled: true }, 'La demostración está activa.');
	}
</script>

<section class="page-intro">
	<div>
		<p class="eyebrow">CADA CAMBIO, EN SU MOMENTO</p>
		<h2>Historial de estados</h2>
		<p>Compara lo que cambió entre lecturas de cierre o carga, con su fuente y hora de recepción.</p>
	</div>
	<span class="status-pill" class:status-demo={pulseState.mode === 'demo'}><span class="status-dot"></span>{pulseState.mode === 'demo' ? 'Simulación' : 'Lecturas del servidor'}</span>
</section>

<section class="protection-stage" class:protection-stage-demo={pulseState.mode === 'demo'}>
{#if pulseState.mode !== 'demo'}
	<div class="state-history">
	<ObservedEventsPanel {online} {revision} mode={pulseState.mode} {onSessionExpired} />
	</div>
{/if}

<section class="protection-layout">
	<article class="protection-feature card">
		<div class="protection-feature-heading">
			<span class="protection-large-icon"><Glyph name="protection" size={26} /></span>
			<div><h2>{pulseState.mode === 'demo' ? (protection.enabled ? 'Eventos simulados activos' : 'Eventos simulados pausados') : pulseState.mode === 'telemetry_read_only' ? 'Historial preparado' : 'Lecturas de cierre y carga'}</h2></div>
		</div>
		{#if pulseState.mode === 'demo'}
			<p class="body-copy">Puedes activar o pausar el registro ficticio. El cambio se guarda en Pulse y afecta únicamente a los eventos de demostración.</p>
			<div class="protection-control-row">
				<div><strong>Registro de eventos de demo</strong><span>{protection.enabled ? 'La demo permite añadir un evento ficticio.' : 'No se añadirán eventos mientras esté pausada.'}</span></div>
				<button class="switch-control" type="button" role="switch" aria-checked={protection.enabled} aria-label="Registro de eventos de demostración" disabled={busy} onclick={toggleProtection}><span></span></button>
			</div>
			<button class="button button-secondary" type="button" disabled={busy || !protection.enabled} onclick={addExampleEvent}>Añadir evento ficticio</button>
		{:else}
			<p class="body-copy">El historial depende de lecturas válidas de cierre o carga. Pulse registra un cambio cuando dos lecturas recibidas indican valores distintos; la primera lectura solo fija una referencia. Los eventos describen estados comunicados y no confirman una acción física.</p>
			<button class="button button-primary" type="button" disabled={busy} onclick={enableDemo}>Explorar la demostración <Glyph name="arrow" size={16} /></button>
		{/if}
	</article>

	<aside class="protection-boundaries card">
		<h2>Límites del historial</h2>
		<p class="protection-boundaries-intro">Pulse conserva cambios comunicados; no certifica lo que ocurre físicamente en el coche.</p>
		<ul>
			<li><span class="boundary-check"><Glyph name="check" size={15} /></span><span><strong>Sin cámaras</strong><small>Pulse no graba ni transmite vídeo.</small></span></li>
			<li><span class="boundary-check"><Glyph name="check" size={15} /></span><span><strong>Sin vigilancia nativa</strong><small>No hay centinela ni detección de intrusos.</small></span></li>
			<li><span class="boundary-check"><Glyph name="check" size={15} /></span><span><strong>Sin órdenes físicas</strong><small>Los controles de demo no llegan al vehículo.</small></span></li>
		</ul>
		<p class="fine-print">Las horas observada y recibida se muestran por separado para conservar la procedencia de cada cambio.</p>
	</aside>

{#if pulseState.mode === 'demo'}
	<section class="protection-events-section" aria-labelledby="demo-events-title">
		<div class="section-heading"><div><h2 id="demo-events-title">Eventos de demostración</h2><p class="protection-events-copy">Registros ficticios guardados por Pulse dentro de esta simulación.</p></div><span class="badge badge-demo">{count(events.length)} {events.length === 1 ? 'evento' : 'eventos'}</span></div>
		{#if events.length === 0}
			<div class="empty-state"><span class="empty-mark"><Glyph name="protection" /></span><h3>No hay eventos de demostración</h3><p>Al añadir un evento ficticio aparecerá aquí con la hora recibida del servidor.</p></div>
		{:else}
			<div class="event-list card evolution-list">
				{#each events as event (event.id)}
					<article class="event-row evolution-item">
						<span class="event-mark"><Glyph name="protection" size={17} /></span>
						<div><strong>{event.message}</strong><span>{event.kind}</span></div>
						<time datetime={event.created_at}>{dateTime(event.created_at)}</time>
						<span class="badge badge-demo">Simulado</span>
					</article>
				{/each}
			</div>
		{/if}
	</section>
{/if}
</section>
</section>
{#if pulseState.mode !== 'demo'}
	<SecuritySignalsPanel
		snapshot={pulseState.telemetry?.latest ?? null}
		quality={pulseState.telemetry?.quality ?? null}
		mode={pulseState.mode}
		{online}
	/>
	<TelemetryHistoryPanel
		{online}
		{revision}
		quality={pulseState.telemetry?.quality ?? null}
		collector={pulseState.telemetry?.collector ?? null}
		{onSessionExpired}
	/>
{/if}

<style>
	.page-intro h2 { margin: 0; font-size: clamp(1.8rem, 3vw, 2.45rem); letter-spacing: -.035em; }
	.protection-stage { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: clamp(22px, 3vw, 34px); }
	:global(.protection-stage > .state-history) { grid-column: 1; grid-row: 1; min-width: 0; }
	:global(.state-history .observed-section) { margin-top: 0; }
	:global(.state-history .section-label) { display: none; }
	.protection-stage:has(:global(.observed-section[data-initial-loading="true"])) .protection-layout { visibility: hidden; }
	.protection-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(270px, .85fr); align-content: start; grid-column: 1; grid-row: 2; gap: 22px; min-width: 0; }
	.protection-stage-demo { grid-template-columns: minmax(0, 1fr); }
	.protection-stage-demo .protection-layout { grid-column: 1; grid-row: 1; grid-template-columns: minmax(0, 1.15fr) minmax(270px, .85fr); }
	.protection-feature, .protection-boundaries { padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.protection-boundaries { padding-top: 18px; border-top: 1px solid var(--line); }
	.protection-feature-heading { display: flex; align-items: center; gap: 14px; }
	.protection-large-icon { display: grid; width: 50px; height: 50px; flex: 0 0 50px; place-items: center; border-radius: 15px; background: var(--forest-soft); color: var(--forest); }
	.protection-feature-heading h2, .protection-boundaries h2 { margin: 6px 0 0; font-size: 1.1875rem; font-weight: 620; letter-spacing: -.035em; }
	.protection-feature > .body-copy { max-width: 630px; margin: 18px 0; font-size: 1rem; }
	.protection-control-row { display: flex; min-height: 66px; align-items: center; justify-content: space-between; gap: 15px; margin: 18px 0 16px; padding: 10px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
	.protection-control-row > div { display: grid; gap: 4px; }
	.protection-control-row strong { font-size: .875rem; }
	.protection-control-row span:not(.switch-control span) { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.switch-control { position: relative; display: flex; width: 58px; min-width: 58px; min-height: 48px; align-items: center; justify-content: flex-start; padding: 0 5px; border: 0; border-radius: 99px; background: transparent; cursor: pointer; }
	.switch-control::before { position: absolute; inset: 7px 4px; border: 1px solid var(--line-strong); border-radius: 99px; background: var(--surface-soft); content: ''; transition: background-color 140ms ease, border-color 140ms ease; }
	.switch-control[aria-checked="true"] > span { transform: translateX(26px); }
	.switch-control[aria-checked="true"]::before { border-color: var(--forest); background: var(--forest); }
	.switch-control:disabled { opacity: .55; }
	.switch-control > span { position: relative; width: 22px; height: 22px; border-radius: 50%; background: var(--surface-raised); box-shadow: 0 1px 3px rgb(0 0 0 / 18%); transition: transform var(--motion-state) var(--ease-out); }
	.protection-boundaries h2 { margin: 0 0 10px; }
	.protection-boundaries-intro { margin: 0 0 17px; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.protection-boundaries ul { display: grid; gap: 15px; margin: 0; padding: 0; list-style: none; }
	.protection-boundaries li { display: flex; align-items: flex-start; gap: 9px; }
	.boundary-check { display: grid; width: 24px; height: 24px; flex: 0 0 24px; place-items: center; border-radius: 8px; background: var(--forest-soft); color: var(--forest); }
	.protection-boundaries li strong, .protection-boundaries li small { display: block; }
	.protection-boundaries li strong { font-size: .875rem; }
	.protection-boundaries li small { margin-top: 4px; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.protection-boundaries .fine-print { margin: 18px 0 0; }
	.protection-stage-demo .protection-boundaries { padding: 0 0 0 22px; border-top: 0; border-left: 1px solid var(--line); }
	.protection-events-section { grid-column: 1 / -1; margin-top: 8px; }
	.protection-events-section .section-heading { margin-top: 0; }
	.protection-events-copy { margin: 6px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.event-list.evolution-list { display: grid; gap: 0; overflow: visible; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.event-row.evolution-item { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto auto; align-items: center; gap: 11px; min-height: 68px; padding: 10px 0; border-bottom: 1px solid var(--line); }
	.event-row:first-child { border-top: 1px solid var(--line); }
	.event-row:last-child { border-bottom: 0; }
	.event-mark { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 9px; background: var(--surface-soft); color: var(--forest); }
	.event-row > div { display: grid; min-width: 0; gap: 4px; }
	.event-row strong { font-size: .875rem; font-weight: 630; }
	.event-row > div span { color: var(--muted); font-size: .75rem; text-transform: capitalize; }
	.event-row time { color: var(--muted); font-size: .75rem; white-space: nowrap; }
	:global(.state-history .observed-list.card) { overflow: visible; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	:global(.state-history .observed-row) { position: relative; grid-template-columns: 16px minmax(0, 1fr); gap: 14px; padding: 18px 0; }
	:global(.state-history .observed-row:not(:last-child)::before) { position: absolute; top: 42px; bottom: -18px; left: 7px; width: 1px; background: var(--line-strong); content: ''; }
	:global(.state-history .observed-mark) { width: 15px; height: 15px; margin-top: 5px; border: 2px solid var(--forest); border-radius: 50%; background: var(--canvas); }
	:global(.state-history .observed-mark span) { display: none; }
	@media (max-width: 850px) { .protection-layout, .protection-stage-demo .protection-layout { grid-column: 1; grid-row: auto; grid-template-columns: minmax(0, 1fr); } .protection-stage-demo .protection-boundaries { padding: 18px 0 0; border-top: 1px solid var(--line); border-left: 0; } }
	@media (max-width: 700px) { .protection-feature, .protection-boundaries { padding: 0; } .protection-boundaries { padding-top: 17px; } .event-row.evolution-item { grid-template-columns: 32px minmax(0, 1fr) auto; gap: 8px; padding-inline: 0; } .event-row time { grid-column: 2; grid-row: 2; } .event-row > .badge { grid-column: 3; grid-row: 1 / span 2; } }
	@media (max-width: 360px) { .protection-feature-heading { align-items: flex-start; } .protection-control-row { align-items: flex-start; flex-wrap: wrap; } .event-row.evolution-item { grid-template-columns: 28px minmax(0, 1fr); } .event-row time, .event-row > .badge { grid-column: 2; grid-row: auto; justify-self: start; } }
</style>
