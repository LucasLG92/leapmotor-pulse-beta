<script lang="ts">
	import { onDestroy } from 'svelte';
	import { ApiError, getChargerSessions, getChargerStatus } from '../lib/api';
	import Glyph from '../lib/Glyph.svelte';
	import VehicleArtwork from '../lib/VehicleArtwork.svelte';
	import { count, dateOnly, euros, number } from '../lib/format';
	import { vehicleColors } from '../lib/vehicle-colors';
	import type { ChargerSession, ChargerStatus, Command, PageProps } from '../lib/types';

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
		// The state response applies temporal availability; history retains expired snapshots.
		return pulseState.mode === 'telemetry_read_only' ? pulseState.vehicle ?? null : null;
	});
	let profileName = $derived(pulseState.settings.vehicle_name || 'Vehículo sin nombre');
	let profileModel = $derived(pulseState.settings.model || 'Modelo sin indicar');
	let currentColorLabel = $derived(
		vehicleColors.find((option) => option.id === pulseState.settings.vehicle_color)?.label ?? 'Color sin elegir'
	);
	let displayMode = $derived(
		pulseState.mode === 'demo' ? 'Datos simulados' :
		pulseState.mode === 'telemetry_read_only' ? pulseState.telemetry?.quality.temporal_status === 'unavailable' ? 'Lectura caducada' : 'Solo lectura' : 'Preparación'
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
	let recentCharges = $derived(pulseState.energy.sessions.filter((session) => pulseState.mode === 'demo' || session.source === 'manual').slice(0, 4));
	let largestRecentCharge = $derived(Math.max(1, ...recentCharges.map((session) => session.kwh)));
	let latestJourney = $derived(pulseState.journeys[0] ?? null);
	let nextMaintenance = $derived(pulseState.maintenance.filter((item) => !item.completed).sort((a, b) => a.due_date.localeCompare(b.due_date))[0] ?? null);
	let batteryPercentage = $derived(vehicleValues?.battery_pct != null && Number.isFinite(vehicleValues.battery_pct) && vehicleValues.battery_pct >= 0 && vehicleValues.battery_pct <= 100 ? vehicleValues.battery_pct : null);
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
	let latestAutomaticCharge = $state<ChargerSession | null>(null);
	let automaticChargeError = $state('');
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
			latestAutomaticCharge = null;
			automaticChargeError = '';
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
		latestAutomaticCharge = null;
		automaticChargeError = '';
		chargerError = '';
		chargerLoading = true;
		try {
			const result = await getChargerStatus();
			if (destroyed || requestId !== chargerRequest || expectedRevision !== revision || !online) return;
			chargerStatus = result;
			if (result.enabled && result.history_status !== 'disabled') {
				try {
					const sessions = await getChargerSessions(1, 0);
					if (destroyed || requestId !== chargerRequest || expectedRevision !== revision || !online) return;
					latestAutomaticCharge = sessions.items[0] ?? null;
				} catch (error) {
					if (destroyed || requestId !== chargerRequest || expectedRevision !== revision || !online) return;
					if (error instanceof ApiError && error.status === 401) {
						onSessionExpired();
						return;
					}
					automaticChargeError = 'No se pudo abrir la última sesión. Puedes reintentar en Energía.';
				}
			}
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
		if (status.status === 'stale') return 'Lectura atrasada';
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
				<p class="profile-eyebrow">TU GARAJE</p>
				<h2 id="vehicle-profile-title">{profileName}</h2>
				<p>{profileModel} <span>· Perfil de Pulse</span></p>
			</div>
			<span class="status-pill" class:status-demo={pulseState.mode === 'demo'} class:status-neutral={pulseState.mode !== 'demo'}>
				<span class="status-dot"></span>{displayMode}
			</span>
		</header>

		<div class="overview-layout">
			<section class="vehicle-scene" aria-label="Vehículo y lecturas">
				<div class="vehicle-display">
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


				{#if batteryPercentage !== null}
					<meter class="battery-level" aria-label="Nivel de batería recibido" min="0" max="100" value={batteryPercentage}>{number(batteryPercentage)} %</meter>
				{/if}
				<VehicleArtwork color={pulseState.settings.vehicle_color ?? null} />
				</div>


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

				<section class="vehicle-controls" aria-labelledby="vehicle-controls-title">
					<div class="controls-intro sr-only"><h3 id="vehicle-controls-title">Acciones del vehículo</h3></div>
					<dl class="vehicle-state" aria-label="Estado recibido del vehículo">
					<div><dt><Glyph name={vehicleValues?.locked === true ? 'lock' : vehicleValues?.locked === false ? 'unlock' : 'activity'} size={16} />Cierre</dt><dd>{booleanState(vehicleValues?.locked, 'Cerrado', 'Abierto')}</dd></div>
					<div><dt><Glyph name="climate" size={16} />Clima</dt><dd>{booleanState(vehicleValues?.climate_on, 'Activo', 'Inactivo')}</dd></div>
					<div><dt><Glyph name="charge" size={16} />Carga</dt><dd>{booleanState(vehicleValues?.charging, 'En curso', 'Detenida')}</dd></div>
				<div><dt><Glyph name="car" size={16} />Odómetro</dt><dd>{number(vehicleValues?.odometer_km)}{vehicleValues?.odometer_km == null ? '' : ' km'}</dd></div>
					</dl>

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
					<p class="control-footnote">{pulseState.mode === 'demo' ? 'Acciones de ejemplo · requieren confirmación.' : 'Pulse no envía órdenes físicas al vehículo.'}</p>

				</section>

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


			</section>

			<aside class="vehicle-task" aria-label="Contexto y estado de carga">



				<section class="charger-context" aria-labelledby="charger-context-title">
					<p class="context-eyebrow"><Glyph name="plug" size={17} /> ENERGÍA EN CASA</p>
					<div class="context-heading">
						<h3 id="charger-context-title">Tu carga, de un vistazo</h3>
						<span class="badge">{chargerStateLabel(chargerStatus)}</span>
					</div>
					{#if chargerLoading && !chargerStatus}
						<p class="context-copy" aria-live="polite">Consultando el estado disponible del cargador…</p>
					{:else if chargerError}
						<p class="context-copy" role="status">{chargerError}</p>
						<button class="inline-action" type="button" disabled={!online || chargerLoading} onclick={() => refreshCharger()}>Volver a consultar</button>
					{:else if chargerStatus?.observation}
						<div class="charge-connection" aria-hidden="true"><Glyph name="plug" size={22} /><span class:flow-active={chargerStatus.observation.charging}></span><Glyph name="car" size={22} /></div>
						<dl class="charger-readings">
							{#if chargerStatus.observation.charged_kwh !== null}<div class="charger-metric"><dt>Energía recibida</dt><dd>{number(chargerStatus.observation.charged_kwh)} kWh</dd></div>{/if}
							{#if chargerStatus.observation.power_w !== null}<div class="charger-metric"><dt>Potencia recibida</dt><dd>{number(chargerStatus.observation.power_w / 1000, true)} kW</dd></div>{/if}
							{#if chargerStatus.observation.active_seconds !== null}<div><dt>Tiempo activo recibido</dt><dd>{activeDuration(chargerStatus.observation.active_seconds)}</dd></div>{/if}
						</dl>
						<p class="charger-source">Punto {chargerStatus.observation.loadpoint} · evcc</p>
						
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
							<summary>Fuente y detalles de la consulta</summary><p>Esta observación no incluye el inicio de sesión ni un coste actual. El coste está disponible en las sesiones guardadas, cuando hay datos suficientes.</p>{#if chargerStatus.observation}<dl class="charger-secondary">							<div><dt>Conexión</dt><dd>{chargerStatus.observation.connected ? 'Conectado' : 'Desconectado'}</dd></div>
							<div><dt>Estado recibido</dt><dd>{chargerStatus.observation.charging ? 'Carga en curso' : 'Sin carga en curso'}</dd></div>
</dl>{/if}
							{#if chargerStatus.observation}<p>Recibida por Pulse {absoluteDateTime(chargerStatus.observation.received_at)}</p>{/if}
							{#if chargerStatus.reason_code && chargerStatus.reason_code !== 'none'}<p>Motivo técnico: <code>{chargerStatus.reason_code}</code></p>{/if}
						</details>
					{/if}
					<a class="context-link" href="/energy">Abrir Energía <Glyph name="arrow" size={16} /></a>
				</section>

				{#if latestAutomaticCharge}
					<section class="latest-charge" aria-label="Última sesión detectada del cargador">
						<div class="context-heading"><h3>Última carga detectada</h3><span class="badge">evcc</span></div>
						<p class="charge-date">{absoluteDateTime(latestAutomaticCharge.started_at)}</p>
						<div class="latest-charge-values"><span>{number(latestAutomaticCharge.kwh)} kWh</span><strong>{latestAutomaticCharge.pricing_status === 'ready' && latestAutomaticCharge.pvpc ? euros(latestAutomaticCharge.pvpc.cost_eur) : '—'}</strong></div>

						<details class="charger-details"><summary>Detalles de la carga detectada</summary><p class="context-copy">{latestAutomaticCharge.pricing_status === 'ready' ? 'Estimación PVPC guardada · no es la factura total.' : latestAutomaticCharge.pricing_status === 'pending' ? 'PVPC pendiente de datos suficientes.' : latestAutomaticCharge.pricing_status === 'ineligible' ? 'Sesión sin datos suficientes para calcular PVPC.' : 'Cálculo PVPC desactivado.'}</p><p>Punto {latestAutomaticCharge.loadpoint} · sesión comunicada por evcc</p><p>Fin: {absoluteDateTime(latestAutomaticCharge.ended_at)}</p><p>Tiempo activo: {activeDuration(latestAutomaticCharge.active_seconds)}</p><p>Recibida por Pulse: {absoluteDateTime(latestAutomaticCharge.received_at)}. La recepción no confirma el momento de medición física.</p></details>
					</section>
				{:else if automaticChargeError}<p class="context-copy" role="status">{automaticChargeError}</p>{/if}

				{#if latestCharge}
					<section class="latest-charge" aria-label="Última carga registrada">
						<div class="context-heading"><h3>{latestCharge.source === 'demo' ? 'Última carga de ejemplo' : 'Última carga manual'}</h3><span class="badge" class:badge-demo={latestCharge.source === 'demo'}>{latestCharge.source === 'demo' ? 'Demo' : 'Manual'}</span></div>
						<p class="charge-date">{absoluteDateTime(latestCharge.started_at)}</p>
						<div class="latest-charge-values"><span>{number(latestCharge.kwh)} kWh</span><strong>{euros(latestCharge.cost_eur)}</strong></div>
						<details class="charger-details"><summary>Detalles de la carga guardada</summary><p class="context-copy">{latestCharge.pricing_method === 'pvpc' ? 'Estimación PVPC guardada' : 'Importe guardado'} · no es la factura total.</p>
						{#if latestCharge.ended_at}<p class="charge-date">Fin: {absoluteDateTime(latestCharge.ended_at)}</p>{/if}</details>
					</section>
				{/if}

				<details class="connection-context connection-details"><summary>Conexión del vehículo <span>{connectionLabel}</span></summary><p class="context-copy">{connectionReason}</p><a class="context-link" href="/settings">Revisar integración y perfil <Glyph name="arrow" size={16} /></a></details>
			</aside>
		</div>
	</article>

	<section class="overview-mobility" aria-labelledby="mobility-title">
	<header class="mobility-heading"><div><p class="profile-eyebrow">EL DETALLE DE TU MOVILIDAD</p><h2 id="mobility-title">Tu actividad reciente</h2></div><a class="context-link" href="/activity">Abrir diario <Glyph name="arrow" size={16} /></a></header>
	<div class="overview-records" aria-label="Energía, actividad y mantenimiento">
		<article class="record-summary energy-summary">
			<div class="record-heading"><h2>Energía registrada</h2><a href="/energy">Ver cargas <Glyph name="arrow" size={16} /></a></div>
			<div class="record-totals">
				<div><span>Registros manuales</span><strong>{number(pulseState.energy.totals_by_source.manual.kwh)} <small>kWh</small></strong><span>{euros(pulseState.energy.totals_by_source.manual.cost_eur)} guardados</span></div>
				{#if pulseState.mode === 'demo'}<div><span>Demostración</span><strong>{number(pulseState.energy.totals_by_source.demo.kwh)} <small>kWh</small></strong><span>{euros(pulseState.energy.totals_by_source.demo.cost_eur)} ficticios</span></div>{/if}
			</div>
			{#if recentCharges.length}
				<ol class="charge-history-mini" aria-label="Últimos registros de energía">
					{#each recentCharges as session (session.id)}
						<li><a href="/energy"><span class="mini-charge-label">{absoluteDateTime(session.started_at)} <small>{session.source === 'demo' ? 'Demo' : 'Manual'}</small></span><span class="mini-charge-bar" aria-hidden="true"><span style={`width: ${Math.max(0, session.kwh) / largestRecentCharge * 100}%`}></span></span><strong>{number(session.kwh)} kWh</strong><span>{euros(session.cost_eur)}</span></a></li>
					{/each}
				</ol>
			{:else}<p>No hay cargas guardadas. Puedes registrar una en Energía.</p>{/if}
			<p class="record-note">Los importes guardados y los ejemplos permanecen separados. Las sesiones detectadas del cargador están en Energía.</p>
		</article>

		<article class="record-summary journey-summary">
			<div class="record-heading"><h2>Último trayecto</h2><a href="/activity">Abrir diario <Glyph name="arrow" size={16} /></a></div>
			{#if latestJourney}
				<p class="journey-source">{pulseState.mode === 'demo' ? 'Trayecto ficticio · demo' : 'Registro guardado en Pulse'} · {absoluteDateTime(latestJourney.started_at)}</p>
				<div class="journey-path"><span class="journey-stop" aria-hidden="true"></span><strong>{latestJourney.from_label}</strong><span class="journey-line" aria-hidden="true"></span><Glyph name="arrow" size={18} /><strong>{latestJourney.to_label}</strong></div>
				<dl class="journey-metrics"><div><dt>Distancia</dt><dd>{number(latestJourney.distance_km)} km</dd></div><div><dt>Duración</dt><dd>{count(latestJourney.duration_min)} min</dd></div><div><dt>Energía del registro</dt><dd>{number(latestJourney.energy_kwh)} kWh</dd></div></dl>
			{:else}
				<div class="quiet-empty"><Glyph name="navigation" size={26} /><h3>Tu diario empieza con un trayecto</h3><p>Pulse todavía no recibe recorridos del coche. Puedes preparar un destino en Mapa.</p><a class="context-link" href="/navigation">Preparar un destino <Glyph name="arrow" size={16} /></a></div>
			{/if}
		</article>

		<article class="record-summary maintenance-summary">
			<div class="record-heading"><h2>Tu coche, al día</h2><a href="/activity">Mantenimiento <Glyph name="arrow" size={16} /></a></div>
			{#if nextMaintenance}
				<p class="maintenance-date"><Glyph name="clock" size={18} />{dateOnly(nextMaintenance.due_date)}</p>
				<h3>{nextMaintenance.title}</h3>
				<p>Recordatorio guardado en Pulse · pendiente de completar.{pulseState.mode === 'demo' ? ' La demo puede incluir tareas de ejemplo.' : ''}</p>
			{:else}<div class="quiet-empty"><Glyph name="check" size={26} /><h3>Sin recordatorios pendientes</h3><p>Añade revisiones y tareas a tu diario para tenerlas a mano. Esto no confirma el mantenimiento del coche.</p></div>{/if}
		</article>

		<article class="record-summary protection-summary">
			<div class="record-heading"><h2>Historial de estados</h2><a href="/protection">Ver cambios <Glyph name="arrow" size={16} /></a></div>
			<strong class="protection-count">{count(protectionCount)} <span>{protectionDescription}</span></strong>
			<p>{pulseState.mode === 'demo' ? 'Eventos ficticios de la demostración.' : protectionCount ? 'Cambios guardados entre observaciones recibidas.' : 'Aún no hay cambios observados en Pulse.'} No hay cámaras ni modo centinela.</p>
		</article>
	</div>
	</section>

	<footer class="overview-footer">Estado de Pulse actualizado: {absoluteDateTime(profileUpdatedAt)} <span aria-hidden="true">·</span> Conexión: {connectionLabel}</footer>
</div>

<style>
 .overview-page { display: grid; gap: 32px; min-width: 0; }
 .overview-hero, .profile-heading, .vehicle-scene, .vehicle-task { min-width: 0; }
 .overview-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
 .profile-eyebrow, .context-eyebrow { color: var(--muted-strong); font-size: .6875rem; letter-spacing: .13em; font-weight: 650; line-height: 1.6; margin: 0 0 10px; }
 .overview-heading h2 { margin: 0; font-size: clamp(2rem, 3.3vw, 3rem); font-weight: 600; line-height: 1.12; letter-spacing: -.045em; overflow-wrap: anywhere; }
 .overview-heading p:not(.profile-eyebrow) { margin: 8px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
 .overview-heading :global(.status-pill) { flex: 0 0 auto; min-height: 32px; font-size: .75rem; }
 .overview-layout { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(290px, .85fr); gap: 36px; align-items: start; }
 .vehicle-display { position: relative; background: radial-gradient(ellipse at 50% 75%, var(--surface-soft), transparent 70%); border-radius: 30px; padding-top: 0; }
 .vehicle-readings { display: flex; align-items: center; justify-content: center; gap: 40px; margin: 0; font-variant-numeric: tabular-nums; }
 .vehicle-readings > div { min-width: 0; }
 .vehicle-readings dt { color: var(--muted); font-size: .8125rem; line-height: 1.5; }
 .vehicle-readings dd { margin: 5px 0 0; overflow-wrap: anywhere; }
 .range-reading { text-align: center; }
 .range-reading dd { font-size: clamp(2.75rem, 5vw, 4.25rem); font-weight: 550; letter-spacing: -.06em; line-height: 1.08; }
 .battery-reading dd { display: flex; align-items: center; gap: 7px; font-size: 1.25rem; font-weight: 600; }
 .battery-level { display: block; width: 166px; height: 5px; margin: 14px auto 0; border: 0; border-radius: 9px; background: var(--line); }
 .battery-level::-webkit-meter-bar { height: 5px; border: 0; border-radius: 9px; background: var(--line); }
 .battery-level::-webkit-meter-optimum-value { background: var(--forest); border-radius: 9px; }
 .battery-level::-moz-meter-bar { background: var(--forest); border-radius: 9px; }
 .vehicle-scene :global(.vehicle-frame) { height: clamp(215px, 22vw, 270px); }
 .vehicle-scene :global(figcaption) { padding: 0 10px 12px; font-size: .6875rem; }
 .color-picker { margin: 8px auto 16px; }
 .color-picker > summary { display: flex; width: fit-content; min-height: 44px; align-items: center; justify-content: center; gap: 8px; margin: auto; color: var(--forest); font-size: .75rem; cursor: pointer; list-style: none; }
 .color-picker > summary::-webkit-details-marker { display: none; }
 .color-picker > summary span { color: var(--muted); }
 .vehicle-colors { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 4px; }
 .color-swatch { display: grid; width: 44px; height: 44px; place-items: center; border: 1px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
 .color-swatch span { width: 22px; height: 22px; border: 1px solid var(--line-strong); border-radius: 50%; background: var(--swatch); box-shadow: inset 0 2px 5px rgb(255 255 255 / 25%); }
 .color-swatch.selected { border-color: var(--forest); }
 .color-swatch:disabled { cursor: not-allowed; opacity: .55; }
 .color-name { color: var(--muted); font-size: .75rem; margin-left: 6px; }
 .vehicle-state { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 0 0 18px; padding: 18px 0; border-block: 1px solid var(--line); }
 .vehicle-state > div { min-width: 0; text-align: center; }
 .vehicle-state dt { display: flex; justify-content: center; align-items: center; gap: 6px; color: var(--muted); font-size: .75rem; }
 .vehicle-state dd { margin: 8px 0 0; font-size: .8125rem; font-weight: 600; overflow-wrap: anywhere; }
 .control-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; max-width: 500px; margin-inline: auto; }
 .control-action, .alternate-action { display: flex; min-width: 0; min-height: 82px; flex-direction: column; align-items: center; gap: 10px; padding: 3px; border: 0; border-radius: 12px; background: transparent; color: var(--ink); cursor: pointer; font-size: .75rem; line-height: 1.35; text-align: center; text-decoration: none; }
 .control-circle { display: grid; width: 52px; height: 52px; flex: 0 0 52px; place-items: center; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); color: var(--ink); transition: background-color var(--motion-fast) ease, border-color var(--motion-fast) ease; }
 .control-action:hover:not(:disabled) .control-circle, .alternate-action:hover:not(:disabled) .control-circle { border-color: var(--forest); background: var(--forest-soft); color: var(--forest); }
 .control-action:disabled, .alternate-action:disabled { color: var(--muted); cursor: not-allowed; }
 .control-action:disabled .control-circle, .alternate-action:disabled .control-circle { color: var(--muted); }
 .control-label { font-weight: 600; overflow-wrap: anywhere; }
 .alternate-actions { margin-top: 12px; }
 .alternate-actions summary, .field-source-details summary, .charger-details summary, .connection-details summary { min-height: 44px; display: flex; align-items: center; gap: 10px; cursor: pointer; color: var(--muted-strong); font-size: .75rem; font-weight: 600; list-style: revert; }
 .alternate-actions summary { justify-content: center; }
 .alternate-action-list { display: flex; flex-wrap: wrap; justify-content: center; gap: 14px; padding: 10px 0; }
 .control-footnote { color: var(--muted); text-align: center; font-size: .6875rem; line-height: 1.5; margin: 0; }
 .observation-summary { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 14px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
 .observation-summary p { display: flex; flex-wrap: wrap; gap: 5px; margin: 0; color: var(--muted); font-size: .6875rem; line-height: 1.5; }
 .observation-summary strong { font-weight: 500; color: var(--muted-strong); }
 .observation-summary .freshness-summary { flex-basis: 100%; justify-content: center; }
 .field-source-details { margin-top: 6px; }
 .field-source-details summary { justify-content: center; }
 .field-source-list { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; margin: 8px 0; }
 .field-source-list div { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }
 .field-source-list dt, .received-times span { color: var(--muted); }
 .field-source-list dd { margin: 0; overflow-wrap: anywhere; }
 .field-source-list, .received-times { font-size: .75rem; line-height: 1.6; }
 .received-times p { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0; }
 .received-times strong { font-weight: 500; overflow-wrap: anywhere; }
 .vehicle-task { display: grid; gap: 16px; }
 .charger-context { padding: 22px; border-radius: 24px; background: var(--surface); border: 1px solid var(--line); }
 .context-eyebrow { display: flex; align-items: center; gap: 8px; color: var(--forest); margin-bottom: 16px; }
 .context-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
 .context-heading h3 { margin: 0; font-size: .9375rem; font-weight: 600; line-height: 1.45; }
 .charger-context .context-heading h3 { font-size: 1.25rem; letter-spacing: -.025em; }
 .context-heading :global(.badge) { font-size: .6875rem; }
 .charge-connection { display: flex; align-items: center; gap: 16px; color: var(--muted); margin: 16px 0; }
 .charge-connection span { flex: 1; height: 2px; background: var(--line); }
 .charge-connection .flow-active { background: var(--forest); }
 .charger-secondary { margin: 10px 0; display: grid; gap: 8px; font-size: .75rem; }
 .charger-secondary dd { margin: 4px 0; }
 .charger-readings { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 12px; margin: 20px 0 0; }
 .charger-readings > div { min-width: 0; }
 .charger-readings dt { font-size: .75rem; color: var(--muted); line-height: 1.5; }
 .charger-readings dd { margin: 6px 0 0; font-size: .8125rem; font-weight: 600; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
 .charger-metric { padding-bottom: 16px; border-bottom: 1px solid var(--line); }
 .charger-metric dd { font-size: clamp(1.3rem, 2.2vw, 1.8rem); font-weight: 550; letter-spacing: -.035em; }
 .charger-source { color: var(--muted); font-size: .75rem; margin: 18px 0 8px; overflow-wrap: anywhere; }
 .context-copy, .charger-details p { color: var(--muted); font-size: .75rem; line-height: 1.6; margin: 10px 0; overflow-wrap: anywhere; }
 .context-link { display: inline-flex; min-height: 44px; align-items: center; gap: 9px; color: var(--forest); font-size: .8125rem; font-weight: 600; text-decoration: none; }
 .charger-context .context-link { display: flex; justify-content: space-between; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
 .latest-charge { padding-inline: 4px; }
 .charge-date { color: var(--muted); font-size: .6875rem; line-height: 1.55; margin: 8px 0; }
 .latest-charge-values { display: flex; justify-content: space-between; gap: 16px; font-size: 1.125rem; margin-top: 10px; font-variant-numeric: tabular-nums; }
 .latest-charge-values strong { font-size: 1.5rem; font-weight: 550; letter-spacing: -.03em; }
 .latest-charge + .latest-charge { border-top: 1px solid var(--line); padding-top: 12px; }
 .connection-context { padding-top: 12px; border-top: 1px solid var(--line); }
 .connection-details summary { display: flex; justify-content: space-between; flex-wrap: wrap; }
 .connection-details summary span { color: var(--muted); font-weight: 400; font-size: .6875rem; }
 .overview-mobility { border-top: 1px solid var(--line); padding-top: 32px; }
 .mobility-heading { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 30px; }
 .mobility-heading h2 { margin: 0; font-size: clamp(1.5rem, 2.2vw, 2rem); font-weight: 550; letter-spacing: -.035em; line-height: 1.25; }
 .overview-records { display: grid; grid-template-columns: 1.1fr 1fr; gap: 30px 40px; }
 .record-summary { min-width: 0; }
 .record-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
 .record-heading h2 { font-size: .9375rem; font-weight: 600; line-height: 1.45; margin: 0; }
 .record-heading a { display: inline-flex; min-height: 44px; align-items: center; gap: 6px; font-size: .75rem; color: var(--forest); text-decoration: none; }
 .record-totals { display: flex; flex-wrap: wrap; gap: 20px 36px; margin-bottom: 20px; }
 .record-totals > div { display: grid; gap: 6px; }
 .record-totals span { color: var(--muted); font-size: .75rem; }
 .record-totals strong { font-size: 1.5rem; font-weight: 550; letter-spacing: -.035em; }
 .record-totals strong small { font-size: .875rem; font-weight: 400; letter-spacing: normal; }
 .charge-history-mini { list-style: none; margin: 0; padding: 0; }
 .charge-history-mini li { border-bottom: 1px solid var(--line); }
 .charge-history-mini a { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(44px, .8fr) auto auto; align-items: center; gap: 12px; min-height: 64px; text-decoration: none; font-size: .75rem; }
 .mini-charge-label { color: var(--muted-strong); line-height: 1.55; overflow-wrap: anywhere; }
 .mini-charge-label small { display: block; color: var(--muted); font-size: .6875rem; }
 .mini-charge-bar { height: 4px; border-radius: 9px; background: var(--line); }
 .mini-charge-bar > span { display: block; height: 100%; border-radius: inherit; background: var(--forest); }
 .record-summary p { color: var(--muted); line-height: 1.6; font-size: .8125rem; }
 .record-summary .record-note, .journey-source { font-size: .6875rem; }
 .journey-path { display: flex; align-items: center; gap: 10px; padding: 20px 0; min-width: 0; }
 .journey-path strong { font-size: .875rem; min-width: 0; overflow-wrap: anywhere; }
 .journey-stop { width: 8px; height: 8px; flex: 0 0 8px; border: 2px solid var(--forest); border-radius: 50%; }
 .journey-line { height: 1px; background: var(--line-strong); flex: 1; min-width: 8px; }
 .journey-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 0; }
 .journey-metrics dt { color: var(--muted); font-size: .75rem; }
 .journey-metrics dd { font-size: 1.125rem; font-weight: 550; margin: 8px 0 0; overflow-wrap: anywhere; }
 .quiet-empty { padding: 16px 0; }
 .quiet-empty :global(svg) { color: var(--muted); }
 .record-summary h3 { font-size: 1rem; font-weight: 600; line-height: 1.4; margin: 12px 0 0; overflow-wrap: anywhere; }
 .maintenance-summary, .protection-summary { border-top: 1px solid var(--line); padding-top: 22px; }
 .maintenance-date { display: flex; align-items: center; gap: 8px; margin: 14px 0 0; }
 .protection-count { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; margin-top: 18px; font-size: 1.75rem; font-weight: 550; }
 .protection-count span { color: var(--muted); font-size: .8125rem; font-weight: 400; }
 .overview-footer { color: var(--muted); font-size: .6875rem; line-height: 1.6; overflow-wrap: anywhere; padding-top: 20px; border-top: 1px solid var(--line); }
 summary:focus-visible, .color-swatch:focus-visible, .control-action:focus-visible, .alternate-action:focus-visible, .context-link:focus-visible, .record-heading a:focus-visible, .charge-history-mini a:focus-visible, .inline-action:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
 @media (max-width: 1120px) {
  .overview-layout { grid-template-columns: 1fr; gap: 30px; }
  .vehicle-scene { max-width: 760px; width: 100%; margin-inline: auto; }
  .vehicle-task { grid-template-columns: 1fr 1fr; gap: 24px; }
  .charger-context { grid-row: span 3; }
  .latest-charge + .latest-charge { border-top: 0; padding-top: 0; }
 }
 @media (max-width: 700px) {
  .overview-page { gap: 30px; }
  .overview-heading { margin-bottom: 12px; gap: 12px; }
  .profile-eyebrow { font-size: .625rem; margin-bottom: 6px; }
  .overview-heading h2 { font-size: 1.875rem; }
  .overview-heading :global(.status-pill) { max-width: 40%; white-space: normal; }
  .overview-heading p:not(.profile-eyebrow) { font-size: .75rem; }
  .overview-heading p span { display: none; }
  .vehicle-readings { gap: 28px; }
  .range-reading dd { font-size: 3.25rem; }
  .vehicle-scene :global(.vehicle-frame) { height: 165px; }
  .vehicle-state { gap: 6px; padding-block: 16px; }
  .vehicle-state dt { flex-direction: row; gap: 5px; font-size: .6875rem; }
  .vehicle-state dt :global(svg) { display: none; }
  .color-picker { margin-bottom: 8px; }
  .vehicle-state dd { font-size: .75rem; }
  .control-actions { gap: 4px; }
  .control-action { font-size: .6875rem; }
  .control-circle { width: 48px; height: 48px; flex-basis: 48px; border-radius: 16px; }
  .vehicle-task { grid-template-columns: 1fr; gap: 22px; }
  .charger-context { padding: 22px; border-radius: 22px; }
  .charger-readings { gap: 16px; }
  .charger-metric dd { font-size: 1.75rem; }
  .latest-charge + .latest-charge { border-top: 1px solid var(--line); padding-top: 20px; }
  .field-source-list { grid-template-columns: 1fr; }
  .mobility-heading { display: block; margin-bottom: 20px; }
  .mobility-heading > a { margin-top: 8px; }
  .overview-records { grid-template-columns: 1fr; gap: 28px; }
  .record-heading { gap: 12px; }
  .mini-charge-bar { display: none; }
  .charge-history-mini a { grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; }
  .observation-summary { gap: 4px 8px; }
 }
 @media (prefers-reduced-motion: reduce) { .control-circle { transition: none; } }
</style>
