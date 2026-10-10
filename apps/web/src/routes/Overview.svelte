<script lang="ts">
	import { onDestroy } from 'svelte';
	import { ApiError, getChargerStatus } from '../lib/api';
	import Glyph from '../lib/Glyph.svelte';
	import VehicleArtwork from '../lib/VehicleArtwork.svelte';
	import { count, euros, number } from '../lib/format';
	import { vehicleColors } from '../lib/vehicle-colors';
	import type { ChargerStatus, Command, PageProps } from '../lib/types';

	let { pulseState, busy, online, revision, mutate, confirmAction, toast, onSessionExpired }: PageProps = $props();

	const telemetryFields = [
		{ key: 'battery_pct', label: 'Batería' },
		{ key: 'range_km', label: 'Autonomía' },
		{ key: 'odometer_km', label: 'Odómetro' },
		{ key: 'locked', label: 'Cierre' },
		{ key: 'climate_on', label: 'Climatización' },
		{ key: 'charging', label: 'Carga' }
	] as const;

	type ControlAction = { id: string; label: string; icon: string; command?: Command };
	const controlDefinitions: Omit<ControlAction, 'command'>[] = [
		{ id: 'lock', label: 'Bloquear', icon: 'lock' },
		{ id: 'unlock', label: 'Desbloquear', icon: 'unlock' },
		{ id: 'climate_on', label: 'Activar climatización', icon: 'climate' },
		{ id: 'climate_off', label: 'Apagar climatización', icon: 'climate' },
		{ id: 'charge_start', label: 'Iniciar carga', icon: 'charge' },
		{ id: 'charge_stop', label: 'Detener carga', icon: 'charge' }
	];

	let latestTelemetry = $derived(pulseState.telemetry?.latest ?? null);
	let vehicleValues = $derived.by(() => {
		if (pulseState.mode === 'demo') {
			const vehicle = pulseState.vehicle;
			return vehicle ? {
				battery_pct: vehicle.battery_pct,
				range_km: vehicle.range_km,
				odometer_km: vehicle.odometer_km,
				locked: vehicle.locked,
				climate_on: vehicle.climate_on,
				charging: vehicle.charging
			} : null;
		}
		return pulseState.mode === 'telemetry_read_only' ? latestTelemetry?.vehicle ?? null : null;
	});
	let profileName = $derived(pulseState.settings.vehicle_name || 'Vehículo sin nombre');
	let profileModel = $derived(pulseState.settings.model || 'Modelo sin indicar');
	let currentColorLabel = $derived(
		vehicleColors.find((option) => option.id === pulseState.settings.vehicle_color)?.label ?? 'Color sin elegir'
	);
	let displayMode = $derived(
		pulseState.mode === 'demo' ? 'Datos simulados' :
		pulseState.mode === 'telemetry_read_only' ? 'Solo lectura' : 'Preparación'
	);
	let profileUpdatedAt = $derived(pulseState.updated_at);
	let observedAt = $derived(
		pulseState.mode === 'demo' ? pulseState.vehicle?.observed_at ?? null : latestTelemetry?.observed_at ?? null
	);
	let sourceLabel = $derived(
		pulseState.mode === 'demo' ? 'Demostración de Pulse' : latestTelemetry?.source || 'Sin fuente disponible'
	);
	let sourceRows = $derived.by(() => latestTelemetry ? telemetryFields.map((field) => ({
		label: field.label,
		source: latestTelemetry.field_sources[field.key] || 'No indicada'
	})) : []);
	let latestCharge = $derived(pulseState.energy.sessions[0] ?? null);
	let protectionCount = $derived(
		pulseState.mode === 'demo' ? pulseState.protection.events.length : pulseState.protection.observed_count
	);
	let protectionDescription = $derived(
		pulseState.mode === 'demo'
			? protectionCount === 1 ? 'evento de demostración' : 'eventos de demostración'
			: protectionCount === 1 ? 'cambio observado' : 'cambios observados'
	);
	let connectionLabel = $derived(
		pulseState.mode === 'demo' ? 'Sin conexión física' :
		pulseState.mode === 'telemetry_read_only' ? 'Proveedor de solo lectura' :
		pulseState.connection.status === 'missing_material' ? 'Material pendiente' :
		pulseState.connection.status === 'connected' ? 'Proveedor conectado' :
		'Proveedor sin conexión'
	);
	let connectionReason = $derived(
		pulseState.connection.reason || 'Pulse todavía no dispone de una conexión validada con el vehículo.'
	);
	let controlActions = $derived.by(() => {
		const locked = vehicleValues?.locked === true;
		const climateOn = vehicleValues?.climate_on === true;
		const charging = vehicleValues?.charging === true;
		const definitions = [
			{ id: locked ? 'unlock' : 'lock', label: locked ? 'Desbloquear' : 'Bloquear', icon: locked ? 'unlock' : 'lock' },
			{ id: climateOn ? 'climate_off' : 'climate_on', label: climateOn ? 'Apagar clima' : 'Activar clima', icon: 'climate' },
			{ id: charging ? 'charge_stop' : 'charge_start', label: charging ? 'Detener carga' : 'Iniciar carga', icon: 'charge' }
		];
		return definitions.map((definition) => ({
			...definition,
			command: pulseState.commands.find((command) => command.id === definition.id)
		}));
	});
	let alternateControlActions = $derived.by(() => {
		const primaryIds = new Set(controlActions.map((action) => action.id));
		return controlDefinitions
			.filter((definition) => !primaryIds.has(definition.id))
			.map((definition) => ({
				...definition,
				command: pulseState.commands.find((command) => command.id === definition.id)
			}));
	});

	let chargerStatus = $state<ChargerStatus | null>(null);
	let chargerError = $state('');
	let chargerLoading = $state(false);
	let chargerRequest = 0;
	let destroyed = false;
	let lastRequestedRevision: number | null = null;
	let lastRequestedOnline: boolean | null = null;

	$effect(() => {
		const currentRevision = revision;
		const currentlyOnline = online;
		if (currentRevision === lastRequestedRevision && currentlyOnline === lastRequestedOnline) return;
		lastRequestedRevision = currentRevision;
		lastRequestedOnline = currentlyOnline;
		if (!currentlyOnline) {
			chargerRequest += 1;
			chargerStatus = null;
			chargerLoading = false;
			chargerError = 'Sin conexión. El estado del cargador no se puede actualizar.';
			return;
		}
		void refreshCharger(currentRevision);
	});

	onDestroy(() => {
		destroyed = true;
		chargerRequest += 1;
	});

	async function refreshCharger(expectedRevision = revision) {
		if (!online) return;
		const requestId = ++chargerRequest;
		chargerStatus = null;
		chargerError = '';
		chargerLoading = true;
		try {
			const result = await getChargerStatus();
			if (destroyed || requestId !== chargerRequest || expectedRevision !== revision || !online) return;
			chargerStatus = result;
		} catch (error) {
			if (destroyed || requestId !== chargerRequest || expectedRevision !== revision || !online) return;
			if (error instanceof ApiError && error.status === 401) {
				onSessionExpired();
				return;
			}
			chargerError = 'No se pudo consultar el cargador. Puedes revisar el estado en Energía.';
		} finally {
			if (!destroyed && requestId === chargerRequest) chargerLoading = false;
		}
	}

	function absoluteDateTime(value: string | null | undefined): string {
		if (!value) return 'Sin fecha indicada';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return 'Fecha no disponible';
		return new Intl.DateTimeFormat('es-ES', {
			year: 'numeric', month: 'long', day: 'numeric',
			hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
		}).format(date);
	}

	function booleanState(value: boolean | null | undefined, yes: string, no: string): string {
		if (value === null || value === undefined) return 'Sin dato';
		return value ? yes : no;
	}

	function freshnessLabel(value: string | null | undefined): string {
		return value === 'unknown' ? 'Desconocida' : value || 'Sin dato';
	}

	function chargerStateLabel(status: ChargerStatus | null): string {
		if (!status) return 'Estado pendiente';
		if (status.status === 'disabled') return 'Sin configurar';
		if (status.status === 'error') return 'Error de consulta';
		if (status.status === 'waiting') return 'Esperando observación';
		if (status.observation?.charging) return 'Carga en curso';
		if (status.observation?.connected) return 'Conectado · sin carga';
		return status.observation ? 'Sin conexión al punto' : 'Sin observación actual';
	}

	function activeDuration(seconds: number | null): string {
		if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Sin dato';
		const totalMinutes = Math.floor(seconds / 60);
		const hours = Math.floor(totalMinutes / 60);
		const minutes = totalMinutes % 60;
		return hours ? `${hours} h ${minutes} min` : `${minutes} min`;
	}

	async function runCommand(action: ControlAction) {
		if (pulseState.mode !== 'demo') {
			toast(connectionReason, 'info');
			return;
		}
		if (!action.command?.enabled) {
			toast(action.command?.reason || 'Esta acción no está disponible en la demostración.', 'info');
			return;
		}
		if (busy || !online) {
			toast('Sin conexión con Pulse. La acción simulada no se ha enviado.', 'info');
			return;
		}
		const accepted = await confirmAction(
			'Confirmar acción simulada',
			`«${action.label}» solo se registrará como una simulación. Pulse no enviará órdenes físicas al vehículo.`,
			'Confirmar simulación'
		);
		if (accepted) {
			await mutate('/api/commands', 'POST', { command: action.command.id, confirmed: true }, 'La simulación se ha registrado.');
		}
	}

	async function chooseColor(color: (typeof vehicleColors)[number]['id']) {
		if (busy || !online) {
			toast('Conéctate con Pulse para guardar el color del perfil.', 'info');
			return;
		}
		if (pulseState.settings.vehicle_color === color) return;
		await mutate('/api/settings', 'PUT', { vehicle_color: color }, 'Color del perfil actualizado.');
	}
</script>

<div class="overview-page">
	<article class="overview-hero" aria-labelledby="vehicle-profile-title">
		<header class="overview-heading">
			<div class="profile-heading">
				<h2 id="vehicle-profile-title">{profileName}</h2>
				<p>{profileModel} <span>· Perfil de Pulse</span></p>
			</div>
			<span class="status-pill" class:status-demo={pulseState.mode === 'demo'} class:status-neutral={pulseState.mode !== 'demo'}>
				<span class="status-dot"></span>{displayMode}
			</span>
		</header>

		<div class="overview-layout">
			<section class="vehicle-scene" aria-label="Vehículo y lecturas">
				<dl class="vehicle-readings" aria-label="Autonomía y batería">
					<div class="range-reading">
						<dt>Autonomía estimada</dt>
						<dd>{number(vehicleValues?.range_km)}{vehicleValues?.range_km == null ? '' : ' km'}</dd>
					</div>
					<div class="battery-reading">
						<dt>Batería</dt>
						<dd><Glyph name="battery" size={19} /><span>{number(vehicleValues?.battery_pct)}{vehicleValues?.battery_pct == null ? '' : '%'}</span></dd>
					</div>
				</dl>

				<div class="observation-summary" aria-label="Origen y fecha de las lecturas">
					{#if latestTelemetry && pulseState.mode === 'telemetry_read_only'}
						<p><span>Observada</span><strong>{absoluteDateTime(observedAt)}</strong></p>
						<p><span>Fuente</span><strong>{sourceLabel}</strong></p>
						<p class="freshness-summary">Vigencia física: {freshnessLabel(pulseState.telemetry?.quality.freshness)}</p>
					{:else if pulseState.mode === 'demo' && pulseState.vehicle}
						<p><span>Dato demo</span><strong>{absoluteDateTime(observedAt)}</strong></p>
						<p><span>Fuente</span><strong>{sourceLabel}</strong></p>
						<p class="freshness-summary">Datos ficticios; la vigencia física no aplica.</p>
					{:else}
						<p><span>Observación</span><strong>Sin lectura disponible</strong></p>
						<p><span>Fuente</span><strong>Sin proveedor habilitado</strong></p>
						<p class="freshness-summary">El perfil de Pulse se muestra aparte de cualquier lectura.</p>
					{/if}
				</div>

				{#if latestTelemetry && pulseState.mode === 'telemetry_read_only'}
					<details class="field-source-details">
						<summary>Fuentes por campo y fechas completas</summary>
						<dl class="field-source-list">
							{#each sourceRows as row (row.label)}
								<div><dt>{row.label}</dt><dd>{row.source}</dd></div>
							{/each}
						</dl>
						<div class="received-times">
							<p><span>Observada por la fuente</span><strong>{absoluteDateTime(observedAt)}</strong></p>
							<p><span>Recibida por el proveedor</span><strong>{absoluteDateTime(latestTelemetry.provider_received_at)}</strong></p>
							<p><span>Recibida por Pulse</span><strong>{absoluteDateTime(latestTelemetry.server_received_at)}</strong></p>
							<p><span>Vigencia física</span><strong>{freshnessLabel(pulseState.telemetry?.quality.freshness)}</strong></p>
							<p><span>Estado temporal</span><strong>{pulseState.telemetry?.quality.temporal_status ?? 'Sin dato'}</strong></p>
							<p><span>Motivo temporal</span><strong>{pulseState.telemetry?.quality.reason_code ?? 'Sin dato'}</strong></p>
						</div>
					</details>
				{/if}

				<VehicleArtwork color={pulseState.settings.vehicle_color ?? null} />



			</section>

			<aside class="vehicle-task" aria-label="Contexto y estado de carga">
				<section class="vehicle-controls" aria-labelledby="vehicle-controls-title">
					<div class="controls-intro"><h3 id="vehicle-controls-title">Acciones del vehículo</h3></div>
					<div class="control-actions" aria-label="Acciones y acceso al historial">
						{#each controlActions as action (action.id)}
							<button
								class="control-action"
								type="button"
								disabled={pulseState.mode !== 'demo' || !action.command?.enabled || busy || !online}
								title={pulseState.mode === 'demo' ? action.command?.reason : connectionReason}
								onclick={() => runCommand(action)}
							>
								<span class="control-circle"><Glyph name={action.icon} size={21} /></span>
								<span class="control-label">{action.label}</span>
							</button>
						{/each}
						<a class="control-action history-action" href="/protection" aria-label="Abrir historial de estados">
							<span class="control-circle"><Glyph name="activity" size={21} /></span>
							<span class="control-label">Historial</span>
						</a>
					</div>

					<a class="charger-brief" href="/energy" aria-label="Ver el cargador y las sesiones de energía"><span>Cargador de casa</span><strong>{chargerLoading ? 'Consultando…' : chargerError ? 'Consulta pendiente' : chargerStateLabel(chargerStatus)}</strong><Glyph name="arrow" size={16} /></a>
					<dl class="vehicle-state" aria-label="Estado recibido del vehículo">
					<div><dt><Glyph name={vehicleValues?.locked === true ? 'lock' : vehicleValues?.locked === false ? 'unlock' : 'activity'} size={16} />Cierre</dt><dd>{booleanState(vehicleValues?.locked, 'Cerrado', 'Abierto')}</dd></div>
					<div><dt><Glyph name="climate" size={16} />Clima</dt><dd>{booleanState(vehicleValues?.climate_on, 'Activo', 'Inactivo')}</dd></div>
					<div><dt><Glyph name="charge" size={16} />Carga</dt><dd>{booleanState(vehicleValues?.charging, 'En curso', 'Detenida')}</dd></div>
				</dl>

					<details class="alternate-actions">
						<summary>{pulseState.mode === 'demo' ? 'Otras acciones simuladas' : 'Acciones de demostración bloqueadas'}</summary>
						<div class="alternate-action-list">
							{#each alternateControlActions as action (action.id)}
								<button
									class="alternate-action"
									type="button"
									disabled={pulseState.mode !== 'demo' || !action.command?.enabled || busy || !online}
									title={pulseState.mode === 'demo' ? action.command?.reason : connectionReason}
									onclick={() => runCommand(action)}
								>
									<span class="control-circle"><Glyph name={action.icon} size={19} /></span>
									<span>{action.label}</span>
								</button>
							{/each}
						</div>
					</details>
					<p class="control-footnote">{pulseState.mode === 'demo' ? 'Cada acción pide confirmación y solo registra una simulación.' : 'Pulse no envía órdenes físicas al vehículo.'}</p>
					<nav class="vehicle-links" aria-label="Más información del vehículo">
						<a href="/energy">Energía y sesiones <Glyph name="arrow" size={16} /></a>
						<a href="/activity">Diario <Glyph name="arrow" size={16} /></a>
					</nav>
				</section>

				<details class="color-picker"><summary>Personalizar color <span>{currentColorLabel}</span><Glyph name="chevron" size={16} /></summary>
				<div class="vehicle-colors" role="group" aria-label="Color del perfil del vehículo">
					{#each vehicleColors as option (option.id)}
						<button
							class="color-swatch"
							class:selected={pulseState.settings.vehicle_color === option.id}
							type="button"
							style={`--swatch: ${option.swatch}`}
							aria-label={`Seleccionar ${option.label}`}
							aria-pressed={pulseState.settings.vehicle_color === option.id}
							disabled={busy || !online}
							onclick={() => chooseColor(option.id)}
						><span></span></button>
					{/each}
					<span class="color-name">{currentColorLabel}</span>
				</div>

				</details>

				<section class="vehicle-context" aria-labelledby="vehicle-context-title">
					<h3 id="vehicle-context-title">Contexto del vehículo</h3>
					<dl class="vehicle-context-list">
						<div><dt>Odómetro</dt><dd>{number(vehicleValues?.odometer_km)}{vehicleValues?.odometer_km == null ? '' : ' km'}</dd></div>
					</dl>
				</section>

				<section class="charger-context" aria-labelledby="charger-context-title">
					<div class="context-heading">
						<h3 id="charger-context-title">Estado del cargador</h3>
						<span class="badge">{chargerStateLabel(chargerStatus)}</span>
					</div>
					{#if chargerLoading && !chargerStatus}
						<p class="context-copy" aria-live="polite">Consultando el estado disponible del cargador…</p>
					{:else if chargerError}
						<p class="context-copy" role="status">{chargerError}</p>
						<button class="inline-action" type="button" disabled={!online || chargerLoading} onclick={() => refreshCharger()}>Volver a consultar</button>
					{:else if chargerStatus?.observation}
						<dl class="charger-readings">
							<div><dt>Conexión</dt><dd>{chargerStatus.observation.connected ? 'Conectado' : 'Desconectado'}</dd></div>
							<div><dt>Estado recibido</dt><dd>{chargerStatus.observation.charging ? 'Carga en curso' : 'Sin carga en curso'}</dd></div>
							{#if chargerStatus.observation.charged_kwh !== null}<div><dt>Energía recibida</dt><dd>{number(chargerStatus.observation.charged_kwh)} kWh</dd></div>{/if}
							{#if chargerStatus.observation.power_w !== null}<div><dt>Potencia recibida</dt><dd>{number(chargerStatus.observation.power_w)} W</dd></div>{/if}
							{#if chargerStatus.observation.active_seconds !== null}<div><dt>Tiempo activo recibido</dt><dd>{activeDuration(chargerStatus.observation.active_seconds)}</dd></div>{/if}
						</dl>
						<p class="charger-source">Punto {chargerStatus.observation.loadpoint} · evcc</p>
						<p class="context-copy">La observación no incluye el inicio de sesión ni un coste actual. Consulta Energía para sesiones e importes disponibles.</p>
					{:else}
						<p class="context-copy">
							{#if chargerStatus?.status === 'disabled'}Pulse no tiene un cargador configurado.
							{:else if chargerStatus?.status === 'waiting'}El cargador todavía no ha enviado una observación.
							{:else if chargerStatus?.status === 'error'}No se pudo consultar el cargador.
							{:else}No hay una observación actual del cargador.{/if}
						</p>
					{/if}
					{#if chargerStatus?.observation || (chargerStatus?.reason_code && chargerStatus.reason_code !== 'none')}
						<details class="charger-details">
							<summary>Detalles de la consulta</summary>
							{#if chargerStatus.observation}<p>Recibida por Pulse {absoluteDateTime(chargerStatus.observation.received_at)}</p>{/if}
							{#if chargerStatus.reason_code && chargerStatus.reason_code !== 'none'}<p>Motivo técnico: <code>{chargerStatus.reason_code}</code></p>{/if}
						</details>
					{/if}
					<a class="context-link" href="/energy">Ver sesiones, costes y resúmenes <Glyph name="arrow" size={16} /></a>
				</section>

				{#if latestCharge}
					<section class="latest-charge" aria-label="Última carga registrada">
						<div class="context-heading"><h3>Última carga registrada</h3><span class="badge" class:badge-demo={latestCharge.source === 'demo'}>{latestCharge.source === 'demo' ? 'Demo' : 'Manual'}</span></div>
						<p class="charge-date">{absoluteDateTime(latestCharge.started_at)}</p>
						<div class="latest-charge-values"><span>{number(latestCharge.kwh)} kWh</span><strong>{euros(latestCharge.cost_eur)}</strong></div>
						<p class="context-copy">Importe guardado para esta carga; no representa una factura total.</p>
					</section>
				{/if}

				<section class="connection-context" aria-labelledby="connection-context-title">
					<h3 id="connection-context-title">Conexión del vehículo</h3>
					<p><strong>{connectionLabel}</strong></p>
					<p class="context-copy">{connectionReason}</p>
					<a class="context-link" href="/settings">Revisar integración y perfil <Glyph name="arrow" size={16} /></a>
				</section>
			</aside>
		</div>
	</article>

	<section class="overview-records" aria-label="Registros y protección">
		<article class="record-summary">
			<div class="record-heading"><h2>Registros de carga</h2><a href="/energy">Ver detalle <Glyph name="arrow" size={16} /></a></div>
			<div class="record-totals">
				<div><span>Manual</span><strong>{number(pulseState.energy.totals_by_source.manual.kwh)} kWh · {euros(pulseState.energy.totals_by_source.manual.cost_eur)}</strong></div>
				<div><span>Demostración</span><strong>{pulseState.mode === 'demo' ? `${number(pulseState.energy.totals_by_source.demo.kwh)} kWh · ${euros(pulseState.energy.totals_by_source.demo.cost_eur)}` : 'Demo desactivada'}</strong></div>
			</div>
			<p>Los registros manuales y de demostración permanecen identificados por separado.</p>
		</article>
		<article class="record-summary protection-summary">
			<div class="record-heading"><h2>Historial de estados</h2><a href="/protection">Ver detalle <Glyph name="arrow" size={16} /></a></div>
			<strong class="protection-count">{count(protectionCount)} <span>{protectionDescription}</span></strong>
			<p>{pulseState.mode === 'demo' ? 'Eventos ficticios de la demostración.' : protectionCount ? 'Cambios guardados entre observaciones recibidas.' : 'Aún no hay cambios observados en Pulse.'} No hay cámaras ni modo centinela.</p>
		</article>
	</section>

	<footer class="overview-footer">Estado de Pulse actualizado: {absoluteDateTime(profileUpdatedAt)} <span aria-hidden="true">·</span> Conexión: {connectionLabel}</footer>
</div>

<style>
	.overview-page { display: grid; gap: 20px; min-width: 0; }
	.overview-hero { min-width: 0; }
	.overview-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 0 0 13px; border-bottom: 1px solid var(--line); }
	.profile-heading { min-width: 0; }
	.overview-heading h2 { margin: 0; font-size: 1.75rem; font-weight: 600; line-height: 1.12; letter-spacing: -.025em; overflow-wrap: anywhere; }
	.overview-heading p { margin: 5px 0 0; color: var(--muted); font-size: .8125rem; line-height: 1.4; overflow-wrap: anywhere; }
	.overview-heading p span { color: var(--muted-strong); }
	.overview-heading :global(.status-pill) { flex: 0 0 auto; min-height: 30px; font-size: .75rem; }
	.overview-layout { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(270px, .95fr); align-items: start; gap: 36px; margin-top: 12px; }
	.vehicle-scene { min-width: 0; }
	.vehicle-readings { display: flex; min-height: 56px; align-items: center; justify-content: center; gap: 40px; margin: 0 0 4px; font-variant-numeric: tabular-nums; }
	.vehicle-readings > div { min-width: 0; }
	.vehicle-readings dt { color: var(--muted); font-size: .75rem; line-height: 1.35; }
	.vehicle-readings dd { margin: 3px 0 0; overflow-wrap: anywhere; }
	.range-reading { text-align: center; }
	.range-reading dd { font-size: 2.25rem; font-weight: 650; line-height: 1.08; letter-spacing: -.035em; }
	.battery-reading { display: grid; gap: 2px; }
	.battery-reading dd { display: flex; align-items: center; gap: 8px; font-size: 1.0625rem; font-weight: 620; }
	.observation-summary { display: grid; grid-template-columns: 1fr; gap: 4px 16px; padding: 2px 0 5px; }
	.observation-summary p { display: grid; grid-template-columns: max-content minmax(0, 1fr); align-items: baseline; gap: 3px 7px; min-width: 0; margin: 0; color: var(--ink); font-size: .75rem; line-height: 1.4; overflow-wrap: anywhere; }
	.observation-summary p > span { color: var(--muted); }
	.observation-summary p > strong { min-width: 0; font-size: inherit; font-weight: 620; overflow-wrap: anywhere; }
	.observation-summary .freshness-summary { grid-column: 1 / -1; display: block; color: var(--muted-strong); }
	.field-source-details { margin: 0 0 5px; border-top: 1px solid var(--line); }
	.field-source-details summary, .alternate-actions summary, .charger-details summary { display: flex; min-height: 44px; align-items: center; color: var(--forest); cursor: pointer; font-size: .75rem; font-weight: 650; text-underline-offset: 3px; }
	.field-source-details summary:focus-visible, .alternate-actions summary:focus-visible, .charger-details summary:focus-visible, .color-swatch:focus-visible, .control-action:focus-visible, .alternate-action:focus-visible, .vehicle-links a:focus-visible, .context-link:focus-visible, .record-heading a:focus-visible, .inline-action:focus-visible { outline: 3px solid var(--forest); outline-offset: 3px; }
	.field-source-list { display: grid; grid-template-columns: 1fr 1fr; gap: 5px 14px; margin: 0; padding: 0 0 8px; }
	.field-source-list > div { display: flex; justify-content: space-between; gap: 8px; min-width: 0; }
	.field-source-list dt, .received-times span { color: var(--muted); font-size: .75rem; }
	.field-source-list dd { min-width: 0; margin: 0; font-size: .75rem; font-weight: 620; text-align: end; overflow-wrap: anywhere; }
	.received-times { display: grid; gap: 4px; padding-top: 7px; border-top: 1px solid var(--line); }
	.received-times p { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; margin: 0; font-size: .75rem; }
	.received-times strong { font-size: inherit; font-weight: 620; overflow-wrap: anywhere; }
	.vehicle-colors { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 2px; margin: 0 0 7px; }
	.color-swatch { display: grid; width: 44px; height: 44px; flex: 0 0 44px; place-items: center; padding: 3px; border: 1.5px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
	.color-swatch span { width: 21px; height: 21px; border: 1px solid var(--line-strong); border-radius: 50%; background: var(--swatch); }
	.color-swatch:hover:not(:disabled) { border-color: var(--line-strong); }
	.color-swatch.selected { border-color: var(--forest); }
	.color-swatch:disabled { cursor: not-allowed; opacity: .55; }
	.color-name { margin-left: 6px; color: var(--muted); font-size: .75rem; }
	.vehicle-state { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 8px 22px; margin: 2px 0 12px; padding: 8px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
	.vehicle-state > div { display: flex; align-items: center; gap: 6px; min-width: 0; }
	.vehicle-state dt { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); font-size: .75rem; }
	.vehicle-state dd { margin: 0; font-size: .75rem; font-weight: 650; overflow-wrap: anywhere; }
	.controls-intro { display: grid; gap: 5px; margin-top: 14px; }
	.controls-intro h3 { margin: 0; font-size: 1rem; }
	.controls-intro :global(.badge), .context-heading :global(.badge) { min-height: 24px; font-size: .75rem; }
	.control-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin: 10px 0 0; }
	.control-action { display: flex; min-width: 0; min-height: 88px; flex-direction: column; align-items: center; gap: 7px; padding: 1px 3px 4px; border: 0; border-radius: 10px; background: transparent; color: var(--ink); cursor: pointer; font-size: .75rem; line-height: 1.25; text-align: center; text-decoration: none; }
	.control-circle { display: grid; width: 52px; height: 52px; flex: 0 0 52px; place-items: center; border: 1px solid var(--line); border-radius: 50%; background: var(--surface-raised); color: var(--ink); transition: background-color var(--motion-fast) ease, border-color var(--motion-fast) ease, color var(--motion-fast) ease; }
	.control-action:hover:not(:disabled) .control-circle, .alternate-action:hover:not(:disabled) .control-circle { border-color: var(--forest); background: var(--forest-soft); color: var(--forest); }
	.control-action:disabled, .alternate-action:disabled { color: var(--muted); cursor: not-allowed; }
	.control-action:disabled .control-circle, .alternate-action:disabled .control-circle { color: var(--muted); opacity: .78; }
	.control-label { font-size: .75rem; font-weight: 650; line-height: 1.25; overflow-wrap: anywhere; }
	.alternate-actions { margin-top: 1px; border-top: 1px solid var(--line); }
	.alternate-actions summary { color: var(--muted-strong); }
	.alternate-action-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; padding-bottom: 5px; }
	.alternate-action { display: flex; min-width: 0; min-height: 68px; flex-direction: column; align-items: center; justify-content: center; gap: 5px; padding: 3px; border: 0; border-radius: 8px; background: transparent; color: var(--ink); cursor: pointer; font-size: .75rem; font-weight: 620; line-height: 1.25; text-align: center; }
	.alternate-action .control-circle { width: 46px; height: 46px; flex-basis: 46px; }
	.charger-brief { display:flex; align-items:center; gap:8px; min-height:44px; border-top:1px solid var(--line); color:var(--ink); text-decoration:none; font-size:.75rem; }
	.charger-brief span { color:var(--muted); }
	.charger-brief strong { margin-left:auto; font-weight:650; }
	.charger-brief :global(svg) { color:var(--forest); flex-shrink:0; }
	.charger-brief:focus-visible { outline:3px solid var(--focus); outline-offset:3px; }
	.color-picker { margin:0 0 14px; border-bottom:1px solid var(--line); }
	.color-picker summary { display:flex; align-items:center; gap:8px; min-height:44px; font-size:.75rem; font-weight:650; color:var(--forest); cursor:pointer; }
	.color-picker summary span { margin-left:auto; max-width:50%; color:var(--muted); font-weight:500; }
	.color-picker summary:focus-visible { outline:3px solid var(--focus); outline-offset:3px; }
	.control-footnote { margin: 3px 0 5px; color: var(--muted); font-size: .75rem; line-height: 1.4; text-align: center; }
	.vehicle-links { display: flex; flex-wrap: wrap; gap: 3px 14px; border-top: 1px solid var(--line); }
	.vehicle-links a, .record-heading a { display: inline-flex; min-height: 44px; align-items: center; gap: 5px; color: var(--forest); font-size: .75rem; font-weight: 650; text-decoration: none; text-underline-offset: 3px; }
	.vehicle-links a:hover, .record-heading a:hover, .context-link:hover { text-decoration: underline; }
	.vehicle-task { min-width: 0; padding: 0 0 0 22px; border-left: 1px solid var(--line); }
	.vehicle-controls { margin: 0 0 16px; padding: 0 0 12px; border-bottom: 1px solid var(--line); }
	.vehicle-controls .controls-intro { margin-top: 0; }
	.vehicle-task h3 { margin: 0; font-size: 1rem; }
	.vehicle-context { margin-bottom: 14px; }
	.vehicle-context-list { margin: 8px 0 0; }
	.vehicle-context-list > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 0 0 10px; border-bottom: 1px solid var(--line); }
	.vehicle-context-list dt { color: var(--muted); font-size: .75rem; }
	.vehicle-context-list dd { margin: 0; font-size: .875rem; font-weight: 650; font-variant-numeric: tabular-nums; }
	.context-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
	.context-heading :global(.badge) { flex: 0 0 auto; }
	.context-copy { margin: 8px 0 0; color: var(--muted); font-size: .8125rem; line-height: 1.5; overflow-wrap: anywhere; }
	.charger-readings { display: grid; gap: 0; margin: 8px 0 0; }
	.charger-readings > div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 10px; padding: 7px 0; border-bottom: 1px solid var(--line); }
	.charger-readings dt { color: var(--muted); font-size: .75rem; }
	.charger-readings dd { margin: 0; font-size: .8125rem; font-weight: 650; text-align: end; }
	.charger-source, .charge-date { margin: 8px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.45; overflow-wrap: anywhere; }
	.charger-details { margin-top: 2px; }
	.charger-details summary { min-height: 44px; color: var(--muted-strong); }
	.charger-details p { margin: 0 0 6px; color: var(--muted); font-size: .75rem; line-height: 1.45; overflow-wrap: anywhere; }
	.charger-details code { font-size: .75rem; }
	.context-link { display: inline-flex; min-height: 44px; align-items: center; gap: 5px; margin-top: 4px; color: var(--forest); font-size: .8125rem; font-weight: 650; text-decoration: none; text-underline-offset: 3px; }
	.inline-action { min-height: 44px; margin-top: 7px; padding: 0 10px; border: 1px solid var(--line-strong); border-radius: 9px; background: transparent; color: var(--forest); font-size: .75rem; font-weight: 650; cursor: pointer; }
	.inline-action:disabled { color: var(--muted); cursor: not-allowed; }
	.latest-charge, .connection-context { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); }
	.charge-date { margin-top: 8px; }
	.latest-charge-values { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 12px; margin-top: 5px; }
	.latest-charge-values span, .latest-charge-values strong { font-size: .9375rem; font-weight: 680; font-variant-numeric: tabular-nums; }
	.latest-charge-values strong { color: var(--forest); }
	.connection-context > p:not(.context-copy) { margin: 7px 0 0; font-size: .8125rem; }
	.overview-records { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 2px 3px; }
	.record-summary { min-width: 0; padding-top: 12px; border-top: 1px solid var(--line); }
	.record-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 12px; }
	.record-heading h2 { margin: 0; font-size: 1rem; }
	.record-heading a { min-height: 44px; }
	.record-totals { display: grid; grid-template-columns: 1fr 1fr; gap: 9px 16px; margin-top: 4px; }
	.record-totals > div { display: grid; gap: 3px; min-width: 0; }
	.record-totals span { color: var(--muted); font-size: .75rem; }
	.record-totals strong { font-size: .8125rem; font-weight: 650; overflow-wrap: anywhere; }
	.record-summary > p { margin: 7px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.45; }
	.protection-count { display: block; margin-top: 7px; font-size: 1.05rem; font-weight: 680; font-variant-numeric: tabular-nums; }
	.protection-count span { font-size: .8125rem; font-weight: 560; }
	.overview-footer { display: flex; flex-wrap: wrap; gap: 6px; color: var(--muted); font-size: .75rem; }

	@media (max-width: 1050px) {
		.overview-layout { grid-template-columns: 1fr; gap: 18px; }
		.vehicle-task { padding: 16px 0 0; border-top: 1px solid var(--line); border-left: 0; }
		.vehicle-controls { margin-bottom: 14px; padding-bottom: 10px; }
		.overview-records { gap: 18px; }
	}
	@media (max-width: 700px) {
		.overview-page { gap: 16px; }
		.overview-heading { align-items: flex-start; gap: 10px; padding-bottom: 11px; }
		.overview-heading :global(.status-pill) { max-width: 42%; white-space: normal; }
		.overview-layout { gap: 8px; margin-top: 9px; }
		.vehicle-readings { gap: 26px; }
		.range-reading dd { font-size: 1.9rem; }
		.battery-reading dd { font-size: 1rem; }
		.observation-summary { gap: 5px 10px; }
		.observation-summary p { grid-template-columns: 64px minmax(0, 1fr); gap: 2px 7px; }
		.observation-summary .freshness-summary { grid-column: 1 / -1; }
		.field-source-list { grid-template-columns: 1fr; }
		.vehicle-colors { gap: 0; margin-bottom: 5px; }
		.color-name { flex: 0 1 auto; margin-left: 6px; text-align: start; }
		.vehicle-state { gap: 8px 14px; margin-bottom: 10px; }
		.vehicle-task { padding-top: 8px; }
		.vehicle-controls { margin-bottom: 10px; padding-bottom: 8px; }
		.vehicle-controls .controls-intro { margin-top: 0; }
		.control-actions { gap: 2px; margin-top: 5px; }
		.control-action { min-height: 72px; gap: 4px; padding: 1px 1px 3px; }
		.control-action .control-circle { width: 48px; height: 48px; flex-basis: 48px; }
		.alternate-action-list { gap: 2px; padding-bottom: 2px; }
		.alternate-action { min-height: 60px; gap: 3px; padding: 2px 1px; }
		.alternate-action .control-circle { width: 44px; height: 44px; flex-basis: 44px; }
		.control-footnote { margin-top: 0; margin-bottom: 3px; }
		.vehicle-links { gap: 1px 8px; }
		.vehicle-links a { justify-content: center; }
		.overview-records { grid-template-columns: 1fr; gap: 11px; }
	}
	@media (max-width: 370px) {
		.vehicle-readings { gap: 18px; }
		.range-reading dd { font-size: 1.75rem; }
		.color-swatch { width: 44px; height: 44px; flex-basis: 44px; }
		.overview-records { gap: 10px; }
	}
	@media (prefers-reduced-motion: reduce) {
		.control-circle { transition: none; }
	}
</style>
