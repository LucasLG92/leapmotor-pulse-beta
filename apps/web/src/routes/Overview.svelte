<script lang="ts">
	import Glyph from '../lib/Glyph.svelte';
	import { count, dateTime, euros, number, relativeAge } from '../lib/format';
	import type { Command, PageProps } from '../lib/types';

	let { pulseState, busy, online, mutate, confirmAction, toast }: PageProps = $props();

	const commandIcons: Record<string, string> = {
		lock: 'lock', unlock: 'unlock', climate_on: 'climate', climate_off: 'climate', charge_start: 'charge', charge_stop: 'charge'
	};
	const commandNames: Record<string, string> = {
		lock: 'Bloquear', unlock: 'Desbloquear', climate_on: 'Activar clima', climate_off: 'Apagar clima',
		charge_start: 'Iniciar carga', charge_stop: 'Detener carga'
	};
	const connectionLabels: Record<string, string> = {
		missing_material: 'Material de conexión pendiente',
		not_connected: 'Proveedor sin conexión',
		connected: 'Proveedor conectado',
		disconnected: 'Sin conexión al proveedor',
		demo: 'Modo demostración'
	};

	let hasLatestTelemetry = $derived(pulseState.mode === 'telemetry_read_only' && pulseState.telemetry?.latest !== null && pulseState.telemetry?.latest !== undefined);
	let telemetryValuesAvailable = $derived(hasLatestTelemetry && pulseState.telemetry?.quality.temporal_status !== 'unavailable');
	let displayedVehicle = $derived(pulseState.mode === 'telemetry_read_only' ? telemetryValuesAvailable ? pulseState.vehicle : null : pulseState.vehicle);
	let batteryPercent = $derived(displayedVehicle?.battery_pct ?? null);
	let batteryOffset = $derived(batteryPercent === null ? 301.6 : 301.6 * (1 - Math.max(0, Math.min(100, batteryPercent)) / 100));
	let observedAt = $derived(pulseState.mode === 'telemetry_read_only' ? hasLatestTelemetry ? pulseState.telemetry?.quality.observed_at ?? null : null : displayedVehicle?.observed_at ?? null);
	let availableCommands = $derived(pulseState.mode === 'demo' ? pulseState.commands.filter((command) => command.enabled) : []);
	let observedCount = $derived(pulseState.protection.observed_count);
	let protectionCount = $derived(pulseState.mode === 'demo' ? pulseState.protection.events.length : observedCount);
	let protectionKind = $derived(pulseState.mode === 'demo' ? protectionCount === 1 ? 'evento' : 'eventos' : protectionCount === 1 ? 'cambio' : 'cambios');

	async function runCommand(command: Command) {
		if (pulseState.mode !== 'demo' || !command.enabled || busy || !online) return;
		const accepted = await confirmAction(
			'Confirmar acción simulada',
			`«${command.label}» solo se registrará como una simulación. Pulse aún no tiene una conexión física con el vehículo.`,
			'Confirmar simulación'
		);
		if (accepted) await mutate('/api/commands', 'POST', { command: command.id, confirmed: true }, 'La simulación se ha registrado.');
	}

	async function enableDemo() {
		const accepted = await confirmAction(
			'Explorar la demostración',
			'La demostración añade datos ficticios a Pulse y simula las acciones del vehículo. Tus cargas y mantenimientos manuales se conservan por separado.',
			'Activar demo'
		);
		if (accepted) await mutate('/api/demo', 'POST', { enabled: true }, 'La demostración está activa.');
	}

	function commandHeading(command: Command): string {
		return commandNames[command.id] ?? command.label;
	}

	function connectionLabel(status: string): string {
		return connectionLabels[status] ?? 'Estado de conexión no disponible';
	}

	function observationAgeLabel(): string {
		if (!observedAt) return 'Sin lectura disponible';
		const observed = new Date(observedAt);
		if (Number.isNaN(observed.getTime())) return 'Hora de observación no válida';
		if (observed.getTime() - Date.now() > 60_000) return 'Hora de observación futura';
		return relativeAge(observedAt);
	}
</script>

<section class="page-intro">
	<div>
		<p class="eyebrow">TU MOVILIDAD, A SIMPLE VISTA</p>
		<h2>Un estado claro, sin ruido.</h2>
		<p>Pulse muestra lo que el servidor conoce y deja claro cuándo un dato no está disponible.</p>
	</div>
	<span class="status-pill" class:status-demo={pulseState.mode === 'demo'}><span class="status-dot"></span>{pulseState.mode === 'demo' ? 'Datos simulados' : hasLatestTelemetry ? 'Lectura sin controles' : 'Preparación'}</span>
</section>

<section class="overview-hero card" aria-labelledby="vehicle-status-title">
	<div class="overview-hero-copy">
		<div class="hero-topline">
			<div>
				<p class="section-label">ESTADO DEL VEHÍCULO</p>
				<h3 id="vehicle-status-title">{displayedVehicle?.name ?? pulseState.settings.vehicle_name}</h3>
			</div>
			{#if pulseState.mode === 'demo'}<span class="badge badge-demo">Simulación</span>{:else if hasLatestTelemetry}<span class="badge">Lectura sin controles</span>{:else}<span class="badge">Preparación</span>{/if}
		</div>

		{#if displayedVehicle}
			<div class="hero-battery-row">
				<div class="battery-gauge" aria-label={batteryPercent === null ? 'Nivel de batería no disponible' : `Batería ${number(batteryPercent)} por ciento`}>
					<svg viewBox="0 0 120 120" role="img" aria-hidden="true" focusable="false">
						<circle class="gauge-track" cx="60" cy="60" r="48" />
						<circle class="gauge-value" cx="60" cy="60" r="48" stroke-dasharray="301.6" stroke-dashoffset={batteryOffset} />
					</svg>
					<div class="gauge-center">
						<strong>{batteryPercent === null ? '—' : number(batteryPercent)}<small>{batteryPercent === null ? '' : '%'}</small></strong>
						<span>Batería</span>
					</div>
				</div>
				<div class="range-summary">
					<p class="section-label">AUTONOMÍA ESTIMADA</p>
					<strong>{number(displayedVehicle.range_km)}<small>{displayedVehicle.range_km === null ? '' : 'km'}</small></strong>
					<p>Valor recibido del estado del servidor.</p>
				</div>
			</div>
			<div class="hero-data-row">
				<div><span>Odómetro</span><strong>{number(displayedVehicle.odometer_km)}{displayedVehicle.odometer_km === null ? '' : ' km'}</strong></div>
				<div><span>Vehículo</span><strong>{displayedVehicle.model || 'Modelo no indicado'}</strong></div>
				<div><span>Última observación</span><strong>{observationAgeLabel()}</strong></div>
			</div>
			<p class="hero-footnote">{pulseState.mode === 'demo' ? 'Los valores de este vehículo son ficticios y no representan una lectura real.' : hasLatestTelemetry ? 'Lectura sin controles. La frescura física no está cualificada y esta fuente aún requiere validación.' : 'La lectura puede quedar desactualizada. Comprueba la hora antes de actuar.'} {observedAt ? `Observado: ${dateTime(observedAt)}.` : ''}</p>
		{:else}
			<div class="vehicle-unconfigured">
				<div class="vehicle-line-art" aria-hidden="true">
					<svg viewBox="0 0 240 100" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
						<path d="M25 67h190l-8-21c-4-10-12-15-24-17l-24-4H92L65 31c-10 3-17 8-22 17l-7 12H25v7Z" />
						<path d="m67 31 13-2 19-1v21H57l10-18Zm38-3h42l23 5 13 16h-78V28Z" />
						<path d="M41 66v13m158-13v13" /><circle cx="69" cy="69" r="11" fill="var(--surface)" /><circle cx="170" cy="69" r="11" fill="var(--surface)" />
					</svg>
				</div>
				<div>
					<h4>{hasLatestTelemetry ? 'Lectura recibida sin valores disponibles' : pulseState.mode === 'telemetry_read_only' ? 'Aún no hay una lectura disponible' : 'Aún no hay un coche conectado'}</h4>
					<p>{hasLatestTelemetry ? `Lectura sin controles. La frescura física no está cualificada y esta fuente aún requiere validación. ${pulseState.connection.reason || 'Esta integración no habilita controles del vehículo.'}` : pulseState.mode === 'telemetry_read_only' ? 'La lectura del proveedor está pendiente. No se mostrarán valores hasta recibir una observación.' : `${pulseState.connection.reason || 'La integración con el vehículo está pendiente.'} No introduzcas aquí contraseñas del fabricante.`}</p>
					{#if pulseState.mode === 'unconfigured'}
						<button class="button button-primary" type="button" disabled={busy} onclick={enableDemo}>Explorar una demo <Glyph name="arrow" size={17} /></button>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</section>

{#if displayedVehicle}
	<section class="overview-grid" aria-label="Estado y acciones">
		<article class="card overview-status-card">
			<div class="section-heading compact-heading"><div><p class="section-label">ESTADO</p><h2>Lo que sabemos</h2></div></div>
			<div class="state-rows">
				<div><span><i class="state-icon"><Glyph name={displayedVehicle.locked === true ? 'lock' : 'unlock'} size={16} /></i> Cierre</span><strong>{displayedVehicle.locked === null ? 'Sin dato' : displayedVehicle.locked ? 'Cerrado' : 'Abierto'}</strong></div>
				<div><span><i class="state-icon"><Glyph name="climate" size={16} /></i> Climatización</span><strong>{displayedVehicle.climate_on === null ? 'Sin dato' : displayedVehicle.climate_on ? 'Activa' : 'Inactiva'}</strong></div>
				<div><span><i class="state-icon"><Glyph name="charge" size={16} /></i> Carga</span><strong>{displayedVehicle.charging === null ? 'Sin dato' : displayedVehicle.charging ? 'En curso' : 'Detenida'}</strong></div>
			</div>
			<p class="fine-print">{hasLatestTelemetry ? 'La observación temporal no acredita la frescura física del vehículo.' : 'Este panel presenta el último valor recibido, no garantiza que siga vigente.'}</p>
		</article>

		<article class="card overview-controls-card">
			{#if hasLatestTelemetry}
				<div class="section-heading compact-heading"><div><p class="section-label">ACCIONES</p><h2>Lectura sin controles</h2></div><span class="badge">Solo lectura</span></div>
				<div class="empty-state compact-empty"><h3>No se envían comandos</h3><p>Esta integración presenta datos del vehículo en modo de solo lectura.</p></div>
			{:else}
				<div class="section-heading compact-heading"><div><p class="section-label">ACCIONES</p><h2>Controles disponibles</h2></div><span class="badge badge-demo">Solo simulación</span></div>
			{/if}
			{#if !hasLatestTelemetry && availableCommands.length}
				<div class="command-grid">
					{#each availableCommands as command (command.id)}
						<button class="command-button" type="button" disabled={busy || !online} onclick={() => runCommand(command)}>
							<span class="command-icon"><Glyph name={commandIcons[command.id] ?? 'arrow'} size={18} /></span>
							<span>{commandHeading(command)}</span>
						</button>
					{/each}
				</div>
			{:else if !hasLatestTelemetry}
				<div class="empty-state compact-empty"><h3>No hay controles habilitados</h3><p>{pulseState.commands.find((command) => command.reason)?.reason || 'El servidor no ofrece acciones para este estado.'}</p></div>
			{/if}
			<p class="fine-print">{hasLatestTelemetry ? 'Esta integración solo entrega lecturas; Pulse no ejecuta controles del vehículo.' : online ? 'Cada acción solicita confirmación y, en esta versión, solo puede generar una simulación.' : 'Sin conexión: las acciones simuladas quedan deshabilitadas y no se ponen en cola.'}</p>
		</article>
	</section>
	{:else if hasLatestTelemetry}
	<section class="overview-grid overview-prep-grid" aria-label="Lectura del vehículo">
		<article class="card prep-card">
			<span class="prep-step"><Glyph name="activity" size={17} /></span><div><h3>Lectura sin controles</h3><p>El servidor informa una observación, pero no presenta valores disponibles. La frescura física no está cualificada y esta fuente aún requiere validación.</p></div>
		</article>
	</section>
{:else}
	<section class="overview-grid overview-prep-grid" aria-label="Preparación de Pulse">
		<article class="card prep-card">
			<span class="prep-step">01</span><div><h3>Perfil del vehículo</h3><p>Define un nombre y un modelo para organizar la información que añadas.</p><a class="text-button" href="/settings">Revisar ajustes <Glyph name="arrow" size={16} /></a></div>
		</article>
		<article class="card prep-card">
			<span class="prep-step">02</span><div><h3>Registro personal</h3><p>Guarda cargas manuales y mantenimientos; aparecerán aquí sin inventar lecturas del coche.</p><a class="text-button" href="/energy">Abrir energía <Glyph name="arrow" size={16} /></a></div>
		</article>
	</section>
{/if}

<section class="overview-bottom-grid">
	<article class="card overview-summary-card">
		<div class="section-heading compact-heading"><div><p class="section-label">ENERGÍA</p><h2>Resumen de cargas</h2></div><a class="text-button" href="/energy">Ver detalle <Glyph name="arrow" size={16} /></a></div>
		<div class="summary-metrics">
			<div><span>Energía registrada</span><strong>{number(pulseState.energy.totals.kwh)} <small>kWh</small></strong></div>
			<div><span>Coste registrado</span><strong>{euros(pulseState.energy.totals.cost_eur)}</strong></div>
		</div>
		<p class="fine-print">Incluye registros que el servidor entrega actualmente, sin estimar cargas ausentes.</p>
	</article>
	<article class="card overview-summary-card">
		<div class="section-heading compact-heading"><div><p class="section-label">PROTECCIÓN</p><h2>Historial de estados</h2></div><a class="text-button" href="/protection">Ver detalle <Glyph name="arrow" size={16} /></a></div>
		<div class="protection-summary"><span class="protection-mark"><Glyph name="protection" size={21} /></span><div><strong>{count(protectionCount)} {protectionKind}</strong><span>{pulseState.mode === 'demo' ? 'Eventos de demostración' : observedCount ? 'Cambios entre lecturas recibidas' : 'Aún no hay cambios recibidos'}</span></div></div>
		<p class="fine-print">Pulse no graba cámaras ni ofrece modo centinela.</p>
	</article>
</section>

<footer class="overview-footer">Servidor actualizado: {dateTime(pulseState.updated_at)} <span aria-hidden="true">·</span> Conexión: {hasLatestTelemetry ? 'Lectura sin controles' : pulseState.mode === 'telemetry_read_only' ? 'Preparación' : connectionLabel(pulseState.connection.status)}</footer>

<style>
	.overview-hero { overflow: hidden; border-radius: 20px; }
	.overview-hero-copy { padding: clamp(21px, 4vw, 36px); }
	.hero-topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
	.hero-topline h3 { margin: 7px 0 0; font-size: 1.1875rem; font-weight: 610; letter-spacing: -.025em; }
	.hero-battery-row { display: flex; align-items: center; gap: clamp(22px, 5vw, 48px); padding: 23px 0 17px; }
	.battery-gauge { position: relative; display: grid; width: 145px; height: 145px; flex: 0 0 145px; place-items: center; }
	.battery-gauge svg { position: absolute; inset: 0; width: 100%; height: 100%; transform: rotate(-90deg); overflow: visible; }
	.battery-gauge circle { fill: none; stroke-width: 8; }
	.gauge-track { stroke: var(--surface-soft); }
	.gauge-value { stroke: var(--forest); stroke-linecap: round; transition: stroke-dashoffset 280ms ease; }
	.gauge-center { display: grid; justify-items: center; gap: 1px; }
	.gauge-center strong { font-variant-numeric: tabular-nums; font-size: 2.0625rem; font-weight: 570; letter-spacing: -.065em; }
	.gauge-center strong small { margin-left: 2px; font-size: 0.875rem; font-weight: 520; }
	.gauge-center span { color: var(--muted); font-size: .75rem; }
	.range-summary { display: grid; gap: 5px; }
	.range-summary strong { margin-top: 2px; font-size: 2.75rem; font-weight: 560; letter-spacing: -.07em; line-height: 1; }
	.range-summary strong small { margin-left: 5px; color: var(--muted); font-size: 1.0625rem; font-weight: 470; letter-spacing: -.02em; }
	.range-summary p:last-child { margin: 0; color: var(--muted); font-size: .875rem; }
	.hero-data-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; padding: 16px 0; border-top: 1px solid var(--line); }
	.hero-data-row > div { display: grid; gap: 5px; }
	.hero-data-row span { color: var(--muted); font-size: .875rem; }
	.hero-data-row strong { overflow-wrap: anywhere; font-size: .875rem; font-weight: 630; }
	.hero-footnote { margin: 0; color: var(--muted); font-size: .75rem; line-height: 1.6; }
	.vehicle-unconfigured { display: grid; grid-template-columns: minmax(180px, 1fr) 1.15fr; align-items: center; gap: 22px; padding: 22px 0 4px; }
	.vehicle-line-art { display: grid; min-height: 125px; place-items: center; border-radius: 14px; background: var(--surface-soft); color: var(--forest); }
	.vehicle-line-art svg { width: min(88%, 250px); }
	.vehicle-unconfigured h4 { margin: 0; font-size: 1rem; font-weight: 620; letter-spacing: -.025em; }
	.vehicle-unconfigured p { max-width: 480px; margin: 8px 0 15px; color: var(--muted); font-size: 1rem; line-height: 1.65; }
	.overview-grid, .overview-bottom-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
	.overview-status-card, .overview-controls-card, .overview-summary-card { min-width: 0; padding: 19px; }
	.compact-heading { margin: 0 0 15px; }
	.compact-heading h2 { margin-top: 6px; font-size: 1rem; }
	.compact-heading .text-button { min-height: 44px; }
	.state-rows { display: grid; }
	.state-rows > div { display: flex; min-height: 47px; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--line); font-size: .875rem; }
	.state-rows > div:last-child { border-bottom: 0; }
	.state-rows > div > span { display: flex; align-items: center; gap: 8px; color: var(--muted-strong); }
	.state-rows strong { font-size: .875rem; font-weight: 650; }
	.state-icon { display: inline-grid; width: 28px; height: 28px; place-items: center; border-radius: 8px; background: var(--surface-soft); color: var(--forest); font-style: normal; }
	.overview-status-card .fine-print, .overview-controls-card .fine-print, .overview-summary-card .fine-print { margin: 12px 0 0; }
	.overview-controls-card .section-heading { flex-direction: row; align-items: center; }
	.command-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
	.command-button { display: flex; min-height: 52px; align-items: center; gap: 9px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-raised); color: var(--ink); cursor: pointer; font-size: .875rem; font-weight: 620; text-align: left; }
	.command-button:hover:not(:disabled) { border-color: var(--line-strong); background: var(--surface-soft); }
	.command-icon { display: grid; width: 31px; height: 31px; flex: 0 0 31px; place-items: center; border-radius: 9px; background: var(--forest-soft); color: var(--forest); }
	.compact-empty { padding: 14px; }
	.compact-empty h3 { font-size: .875rem; }
	.prep-card { display: flex; gap: 13px; padding: 20px; }
	.prep-step { display: grid; width: 34px; height: 34px; flex: 0 0 34px; place-items: center; border-radius: 10px; background: var(--forest-soft); color: var(--forest); font-size: .75rem; font-weight: 750; }
	.prep-card h3 { margin: 2px 0 0; font-size: 0.875rem; }
	.prep-card p { margin: 7px 0 3px; color: var(--muted); font-size: 1rem; line-height: 1.6; }
	.prep-card .text-button { min-height: 44px; }
	.summary-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
	.summary-metrics > div { display: grid; gap: 6px; padding: 12px; border-radius: 10px; background: var(--surface-soft); }
	.summary-metrics span { color: var(--muted); font-size: .875rem; }
	.summary-metrics strong { font-size: 1rem; font-variant-numeric: tabular-nums; }
	.summary-metrics small { color: var(--muted); font-size: .75rem; font-weight: 520; }
	.protection-summary { display: flex; align-items: center; gap: 11px; min-height: 58px; }
	.protection-mark { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 11px; background: var(--forest-soft); color: var(--forest); }
	.protection-summary div { display: grid; gap: 4px; }
	.protection-summary strong { font-size: .875rem; }
	.protection-summary span:last-child { color: var(--muted); font-size: .75rem; }
	.overview-footer { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 22px; color: var(--muted); font-size: .75rem; }
	@media (max-width: 980px) {
		.overview-grid, .overview-bottom-grid { grid-template-columns: 1fr; }
	}
	@media (max-width: 700px) {
		.overview-hero-copy { padding: 19px; }
		.hero-battery-row { gap: 20px; }
		.battery-gauge { width: 126px; height: 126px; flex-basis: 126px; }
		.range-summary strong { font-size: 2.25rem; }
		.range-summary strong small { font-size: 0.875rem; }
		.hero-data-row { gap: 8px; }
		.vehicle-unconfigured { grid-template-columns: 1fr; gap: 15px; }
		.vehicle-line-art { min-height: 105px; }
	}
	@media (max-width: 370px) {
		.battery-gauge { width: 108px; height: 108px; flex-basis: 108px; }
		.gauge-center strong { font-size: 1.6875rem; }
		.range-summary strong { font-size: 1.9375rem; }
		.hero-data-row { grid-template-columns: 1fr 1fr; }
	}


	@media (max-width: 360px) {
		.overview-hero-copy { padding: 15px; }
		.hero-battery-row { flex-wrap: wrap; gap: 14px; }
		.range-summary { min-width: 0; }
		.range-summary strong { font-size: 1.75rem; }
	}
</style>
