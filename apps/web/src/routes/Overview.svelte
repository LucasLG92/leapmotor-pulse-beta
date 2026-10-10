<script lang="ts">
	import Glyph from '../lib/Glyph.svelte';
	import VehicleArtwork from '../lib/VehicleArtwork.svelte';
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

<section class="overview-hero card" aria-labelledby="vehicle-status-title">
	<header class="vehicle-heading">
		<div><h2 id="vehicle-status-title">{pulseState.settings.vehicle_name}</h2><p>{pulseState.settings.model || 'Modelo sin indicar'}</p></div>
		<span class="status-pill" class:status-demo={pulseState.mode === 'demo'}><span class="status-dot"></span>{pulseState.mode === 'demo' ? 'Datos simulados' : hasLatestTelemetry ? 'Lectura sin controles' : 'Preparación'}</span>
	</header>
	<div class="vehicle-overview">
		<VehicleArtwork color={pulseState.settings.vehicle_color} />
		<div class="vehicle-reading">
			{#if displayedVehicle}
				<div class="vehicle-charge-status"><Glyph name="charge" size={19} /><strong>{displayedVehicle.charging === null ? 'Estado de carga sin dato' : displayedVehicle.charging ? 'Carga en curso' : 'Carga detenida'}</strong></div>
				<dl class="vehicle-energy">
					<div><dt>Batería</dt><dd>{batteryPercent === null ? '—' : number(batteryPercent)}<span>{batteryPercent === null ? '' : '%'}</span></dd></div>
					<div><dt>Autonomía estimada</dt><dd>{number(displayedVehicle.range_km)}<span>{displayedVehicle.range_km === null ? '' : 'km'}</span></dd></div>
				</dl>
				{#if batteryPercent !== null}<div class="battery-track" role="meter" aria-label="Batería" aria-valuemin="0" aria-valuemax="100" aria-valuenow={batteryPercent}><span style:transform={`scaleX(${Math.max(0, Math.min(100, batteryPercent)) / 100})`}></span></div>{/if}
				<dl class="vehicle-observation"><div><dt>Última observación</dt><dd>{observationAgeLabel()}</dd></div><div><dt>Odómetro</dt><dd>{number(displayedVehicle.odometer_km)}{displayedVehicle.odometer_km === null ? '' : ' km'}</dd></div></dl>
			{:else}
				<h3>{hasLatestTelemetry ? 'Lectura sin valores disponibles' : pulseState.mode === 'telemetry_read_only' ? 'Esperando una lectura' : 'Tu B10, cuando esté conectado'}</h3>
				<p class="preparation-copy">{hasLatestTelemetry ? `La fuente requiere validación. ${pulseState.connection.reason || 'Esta integración solo permite lecturas.'}` : pulseState.mode === 'telemetry_read_only' ? 'La lectura del proveedor está pendiente. La batería y la autonomía aparecerán cuando haya una observación disponible.' : 'Mientras llega tu coche, puedes personalizar su perfil, registrar cargas y preparar Pulse.'}</p>
				<a class="button button-primary" href="/settings">Personalizar mi B10 <Glyph name="arrow" size={17} /></a>
				{#if pulseState.mode === 'unconfigured'}<button class="text-button" type="button" disabled={busy || !online} onclick={enableDemo}>Explorar la demo</button>{/if}
			{/if}
		</div>
	</div>
	<div class="vehicle-summary-footer">
		<p>{pulseState.mode === 'demo' ? 'Batería, autonomía y acciones son ficticias. No hay conexión física con el coche.' : hasLatestTelemetry ? `Solo lectura. Frescura física pendiente de validación.${observedAt ? ` Observado: ${dateTime(observedAt)}.` : ''}` : pulseState.mode === 'telemetry_read_only' ? 'Lectura pendiente. Los controles físicos siguen bloqueados.' : 'Aún sin conexión al vehículo. No se muestran lecturas estimadas.'}</p>
		<a class="text-button" href="/energy">Energía y cargas <Glyph name="arrow" size={16} /></a>
	</div>
</section>

{#if displayedVehicle}
	<section class="overview-grid" aria-label="Estado y acciones">
		<article class="card overview-status-card">
			<div class="section-heading compact-heading"><div><h2>Lo que sabemos</h2></div></div>
			<div class="state-rows">
				<div><span><i class="state-icon"><Glyph name={displayedVehicle.locked === true ? 'lock' : 'unlock'} size={16} /></i> Cierre</span><strong>{displayedVehicle.locked === null ? 'Sin dato' : displayedVehicle.locked ? 'Cerrado' : 'Abierto'}</strong></div>
				<div><span><i class="state-icon"><Glyph name="climate" size={16} /></i> Climatización</span><strong>{displayedVehicle.climate_on === null ? 'Sin dato' : displayedVehicle.climate_on ? 'Activa' : 'Inactiva'}</strong></div>
				<div><span><i class="state-icon"><Glyph name="charge" size={16} /></i> Carga</span><strong>{displayedVehicle.charging === null ? 'Sin dato' : displayedVehicle.charging ? 'En curso' : 'Detenida'}</strong></div>
			</div>
			<p class="fine-print">{hasLatestTelemetry ? 'La observación temporal no acredita la frescura física del vehículo.' : 'Este panel presenta el último valor recibido, no garantiza que siga vigente.'}</p>
		</article>

		<article class="card overview-controls-card">
			{#if hasLatestTelemetry}
				<div class="section-heading compact-heading"><div><h2>Lectura sin controles</h2></div><span class="badge">Solo lectura</span></div>
				<div class="empty-state compact-empty"><h3>No se envían comandos</h3><p>Esta integración presenta datos del vehículo en modo de solo lectura.</p></div>
			{:else}
				<div class="section-heading compact-heading"><div><h2>Controles disponibles</h2></div><span class="badge badge-demo">Solo simulación</span></div>
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
			<span class="prep-step"><Glyph name="settings" size={17} /></span><div><h3>Perfil del vehículo</h3><p>Define un nombre y un modelo para organizar la información que añadas.</p><a class="text-button" href="/settings">Revisar ajustes <Glyph name="arrow" size={16} /></a></div>
		</article>
		<article class="card prep-card">
			<span class="prep-step"><Glyph name="charge" size={17} /></span><div><h3>Registro personal</h3><p>Guarda cargas manuales y mantenimientos; aparecerán aquí sin inventar lecturas del coche.</p><a class="text-button" href="/energy">Abrir energía <Glyph name="arrow" size={16} /></a></div>
		</article>
	</section>
{/if}

<section class="overview-bottom-grid">
	<article class="card overview-summary-card">
		<div class="section-heading compact-heading"><div><h2>Resumen de cargas</h2></div><a class="text-button" href="/energy">Ver detalle <Glyph name="arrow" size={16} /></a></div>
		<div class="summary-metrics">
			<div><span>Energía registrada</span><strong>{number(pulseState.energy.totals.kwh)} <small>kWh</small></strong></div>
			<div><span>Coste registrado</span><strong>{euros(pulseState.energy.totals.cost_eur)}</strong></div>
		</div>
		<p class="fine-print">Incluye registros que el servidor entrega actualmente, sin estimar cargas ausentes.</p>
	</article>
	<article class="card overview-summary-card">
		<div class="section-heading compact-heading"><div><h2>Historial de estados</h2></div><a class="text-button" href="/protection">Ver detalle <Glyph name="arrow" size={16} /></a></div>
		<div class="protection-summary"><span class="protection-mark"><Glyph name="protection" size={21} /></span><div><strong>{count(protectionCount)} {protectionKind}</strong><span>{pulseState.mode === 'demo' ? 'Eventos de demostración' : observedCount ? 'Cambios entre lecturas recibidas' : 'Aún no hay cambios recibidos'}</span></div></div>
		<p class="fine-print">Pulse no graba cámaras ni ofrece modo centinela.</p>
	</article>
</section>

<footer class="overview-footer">Servidor actualizado: {dateTime(pulseState.updated_at)} <span aria-hidden="true">·</span> Conexión: {hasLatestTelemetry ? 'Lectura sin controles' : pulseState.mode === 'telemetry_read_only' ? 'Preparación' : connectionLabel(pulseState.connection.status)}</footer>

<style>
	.overview-hero { overflow: hidden; border-radius: 20px; }
	.vehicle-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 26px 28px 0; }
	.vehicle-heading h2 { margin: 0; font-size: 1.5rem; font-weight: 650; letter-spacing: -.025em; overflow-wrap: anywhere; }
	.vehicle-heading p { margin: 5px 0 0; color: var(--muted); font-size: .875rem; }
	.vehicle-overview { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); align-items: center; gap: 32px; padding: 26px 28px; }
	.vehicle-reading { min-width: 0; }
	.vehicle-reading h3 { margin: 0; font-size: 1.25rem; line-height: 1.3; letter-spacing: -.02em; }
	.preparation-copy { margin: 12px 0 22px; max-width: 42ch; color: var(--muted); font-size: 1rem; line-height: 1.65; }
	.vehicle-reading > .text-button { display: flex; margin-top: 6px; }
	.vehicle-charge-status { display: flex; align-items: center; gap: 8px; color: var(--forest); font-size: .875rem; }
	.vehicle-energy { display: grid; grid-template-columns: 1fr 1.25fr; gap: 18px; margin: 24px 0 16px; }
	dt { color: var(--muted); font-size: .875rem; }
	dd { margin: 0; }
	.vehicle-energy dd { margin-top: 7px; font-size: 2rem; font-weight: 620; line-height: 1.1; letter-spacing: -.035em; }
	.vehicle-energy dd span { margin-left: 4px; color: var(--muted-strong); font-size: .875rem; font-weight: 500; letter-spacing: 0; }
	.battery-track { overflow: hidden; height: 6px; border-radius: 4px; background: var(--line); }
	.battery-track span { display: block; width: 100%; height: 100%; background: var(--forest); transform-origin: left center; transition: transform var(--motion-state) ease-out; }
	.vehicle-observation { display: grid; gap: 12px; margin: 20px 0 0; }
	.vehicle-observation > div { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px; }
	.vehicle-observation dd { font-size: .875rem; font-weight: 600; }
	.vehicle-summary-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 14px 28px; border-top: 1px solid var(--line); }
	.vehicle-summary-footer p { max-width: 75ch; margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.55; }
	.vehicle-summary-footer .text-button { flex: 0 0 auto; }
	.overview-grid, .overview-bottom-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
	.overview-status-card, .overview-controls-card, .overview-summary-card { min-width: 0; padding: 22px; }
	.compact-heading { margin: 0 0 15px; }
	.compact-heading h2 { margin: 0; font-size: 1rem; }
	.compact-heading .text-button { min-height: 48px; }
	.state-rows { display: grid; }
	.state-rows > div { display: flex; min-height: 48px; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--line); font-size: .875rem; }
	.state-rows > div:last-child { border-bottom: 0; }
	.state-rows > div > span { display: flex; align-items: center; gap: 8px; color: var(--muted-strong); }
	.state-rows strong { font-size: .875rem; font-weight: 650; }
	.state-icon { display: inline-grid; width: 28px; height: 28px; place-items: center; color: var(--muted-strong); font-style: normal; }
	.overview-status-card .fine-print, .overview-controls-card .fine-print, .overview-summary-card .fine-print { margin: 12px 0 0; }
	.overview-controls-card .section-heading { flex-direction: row; align-items: center; }
	.command-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
	.command-button { display: flex; min-height: 52px; align-items: center; gap: 9px; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface-raised); color: var(--ink); cursor: pointer; font-size: .875rem; font-weight: 620; text-align: left; }
	.command-button:hover:not(:disabled) { background: var(--surface-soft); }
	.command-icon { display: grid; width: 24px; flex: 0 0 24px; place-items: center; color: var(--forest); }
	.compact-empty { padding: 14px; }
	.compact-empty h3 { font-size: .875rem; }
	.prep-card { display: flex; gap: 14px; padding: 22px; }
	.prep-step { display: grid; width: 24px; height: 30px; flex: 0 0 24px; place-items: center; color: var(--forest); }
	.prep-card h3 { margin: 2px 0 0; font-size: 1rem; }
	.prep-card p { margin: 8px 0 4px; color: var(--muted); font-size: 1rem; line-height: 1.6; }
	.prep-card .text-button { min-height: 48px; }
	.summary-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
	.summary-metrics > div { display: grid; gap: 7px; padding: 12px 0; }
	.summary-metrics span { color: var(--muted); font-size: .875rem; }
	.summary-metrics strong { font-size: 1.125rem; }
	.summary-metrics small { color: var(--muted); font-size: .75rem; font-weight: 520; }
	.protection-summary { display: flex; align-items: center; gap: 11px; min-height: 58px; }
	.protection-mark { display: grid; width: 28px; height: 38px; place-items: center; color: var(--forest); }
	.protection-summary div { display: grid; gap: 4px; }
	.protection-summary strong { font-size: .875rem; }
	.protection-summary span:last-child { color: var(--muted); font-size: .8125rem; }
	.overview-footer { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 22px; color: var(--muted); font-size: .75rem; }
	@media (max-width: 1050px) {
		.vehicle-overview { gap: 22px; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
		.overview-grid, .overview-bottom-grid { grid-template-columns: 1fr; }
	}
	@media (max-width: 700px) {
		.vehicle-heading { padding: 20px 18px 0; gap: 12px; flex-wrap: wrap; }
		.vehicle-heading h2 { font-size: 1.25rem; }
		.vehicle-overview { grid-template-columns: 1fr; padding: 18px; gap: 24px; }
		.vehicle-summary-footer { padding: 14px 18px; flex-direction: column; align-items: flex-start; gap: 5px; }
		.vehicle-reading h3 { font-size: 1.125rem; }
		.preparation-copy { margin-bottom: 16px; }
		.vehicle-energy { margin-top: 20px; }
		.vehicle-energy dd { font-size: 1.875rem; }
		.overview-status-card, .overview-controls-card, .overview-summary-card, .prep-card { padding: 18px; }
	}
	@media (max-width: 370px) { .vehicle-energy, .command-grid, .summary-metrics { grid-template-columns: 1fr; } }
</style>
