<script lang="ts">
	import type { Mode, TelemetrySnapshot, TemporalQuality } from './types';
	import { currentSecuritySignals } from './security-signals';

	let { snapshot, quality, mode, online }: {
		snapshot: TelemetrySnapshot | null;
		quality: TemporalQuality | null;
		mode: Mode;
		online: boolean;
	} = $props();

	let expanded = $state(false);
	let readings = $derived(currentSecuritySignals(snapshot, quality, mode, online));
	let hasRecentSnapshot = $derived(online && mode !== 'demo' && snapshot !== null && quality?.temporal_status === 'fresh');

	function fullDate(value: string | null): string {
		if (!value) return 'Sin fecha indicada';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return 'Fecha no disponible';
		return new Intl.DateTimeFormat('es-ES', {
			year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
		}).format(date);
	}
</script>

<section class="security-signals" aria-labelledby="security-signals-title">
	<details bind:open={expanded}>
		<summary>
			<span><strong id="security-signals-title">Diagnóstico de seguridad</strong><small>Lectura técnica para cualificación · sin interpretación</small></span>
			<span class="security-signals-toggle">{expanded ? 'Ocultar' : 'Consultar'} <span aria-hidden="true">⌄</span></span>
		</summary>
		<div class="security-signals-content">
			<p class="security-signals-note">Los códigos se presentan en decimal tal como se recibieron. No describen un estado físico del vehículo ni generan acciones.</p>
			{#if !online}
				<p class="security-signals-state" role="status">Sin conexión. No se muestran datos privados del vehículo.</p>
			{:else if mode === 'demo'}
				<p class="security-signals-state">La demostración no incluye códigos de diagnóstico.</p>
			{:else if !hasRecentSnapshot}
				<p class="security-signals-state">No hay una lectura temporalmente reciente válida para mostrar.</p>
			{:else if readings.length === 0}
				<p class="security-signals-state">Esta lectura no contiene códigos válidos.</p>
			{:else}
				<dl class="security-signals-list">
					{#each readings as reading (reading.key)}
						<div><dt>{reading.label}</dt><dd>{reading.code}<small>Campo: {reading.key} · Fuente: {reading.source}</small></dd></div>
					{/each}
				</dl>
				<dl class="security-signals-times">
					<div><dt>Observada por la fuente</dt><dd>{fullDate(snapshot?.observed_at ?? null)}</dd></div>
					<div><dt>Recibida por el proveedor</dt><dd>{fullDate(snapshot?.provider_received_at ?? null)}</dd></div>
					<div><dt>Recibida por Pulse</dt><dd>{fullDate(snapshot?.server_received_at ?? null)}</dd></div>
					<div><dt>Vigencia física</dt><dd>Pendiente de cualificación</dd></div>
				</dl>
			{/if}
		</div>
	</details>
</section>

<style>
	.security-signals { grid-column: 1 / -1; min-width: 0; margin-top: 4px; border-top: 1px solid var(--line); }
	.security-signals > details > summary { display: flex; min-height: 70px; justify-content: space-between; gap: 16px; padding: 10px 2px; }
	.security-signals > details > summary > span:first-child { display: grid; gap: 4px; }
	.security-signals strong { color: var(--ink); font-size: 1rem; font-weight: 650; }
	.security-signals summary small, .security-signals-toggle { color: var(--muted); font-size: .75rem; }
	.security-signals-toggle { display: inline-flex; align-items: center; gap: 9px; white-space: nowrap; flex-shrink: 0; }
	.security-signals-content { min-width: 0; padding: 0 0 18px; }
	.security-signals-note { margin: 6px 0 14px; color: var(--muted); font-size: .8125rem; line-height: 1.55; }
	.security-signals-list, .security-signals-times { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: 10px; margin: 0; }
	.security-signals-list > div, .security-signals-times > div { min-width: 0; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
	.security-signals-list dt, .security-signals-times dt { color: var(--muted); font-size: .75rem; }
	.security-signals-list dd, .security-signals-times dd { overflow-wrap: anywhere; margin: 5px 0 0; color: var(--ink); font-size: .875rem; font-weight: 620; }
	.security-signals-list dd small { display: block; margin-top: 5px; color: var(--muted); font-size: .75rem; font-weight: 400; }
	.security-signals-times { margin-top: 10px; }
	.security-signals-state { margin: 0; padding: 14px 0; color: var(--muted); font-size: .875rem; }
	@media (max-width: 520px) { .security-signals > details > summary { align-items: flex-start; } }
</style>
