<script lang="ts">
	import { onDestroy } from 'svelte';
	import { ApiError, getChargerSessions, getChargerStatus } from '../lib/api';
	import Glyph from '../lib/Glyph.svelte';
	import VehicleArtwork from '../lib/VehicleArtwork.svelte';
	import { count, dateOnly, euros, number } from '../lib/format';
	import { vehicleColors } from '../lib/vehicle-colors';
	import { sensorValue, telemetrySensorFields } from '../lib/telemetry-sensors';
	import type { ChargerSession, ChargerStatus, Command, PageProps } from '../lib/types';

	let { pulseState, busy, online, revision, mutate, confirmAction, toast, onSessionExpired }: PageProps = $props();

	const telemetryFields = [
		{ key: 'battery_pct', label: 'Batería' },
		{ key: 'range_km', label: 'Autonomía' },
		{ key: 'odometer_km', label: 'Odómetro' },
		{ key: 'locked', label: 'Cierre' },
		{ key: 'climate_on', label: 'Climatización' },
		{ key: 'charging', label: 'Carga' },
		...telemetrySensorFields
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
				charging: vehicle.charging,
				interior_temperature_c: null,
				battery_min_temperature_c: null,
				front_left_pressure_kpa: null,
				front_right_pressure_kpa: null,
				rear_left_pressure_kpa: null,
				rear_right_pressure_kpa: null
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
	let readingStatus = $derived.by(() => {
		if (pulseState.mode === 'demo') return 'Datos de demostración';
		if (pulseState.mode !== 'telemetry_read_only') return 'Sin datos disponibles';
		const status = pulseState.telemetry?.quality.temporal_status;
		if (status === 'fresh') return 'Dentro de ventana temporal de Pulse';
		if (status === 'stale') return 'Lectura atrasada';
		if (status === 'unavailable') return 'Lectura caducada';
		return 'Vigencia desconocida';
	});
	let readingIsUncertain = $derived(
		pulseState.mode !== 'demo' &&
		(pulseState.mode !== 'telemetry_read_only' || pulseState.telemetry?.quality.temporal_status !== 'fresh')
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
	let collectorLabel = $derived(
		pulseState.mode === 'demo' ? 'Demo · sin lector físico' :
		pulseState.telemetry?.collector.running === true ? 'Lector en ejecución' :
		pulseState.setup?.vehicle_reader_enabled === true ? 'Lector configurado · detenido' : 'Lector sin configurar'
	);
	let temporalExplanation = $derived(temporalReason(pulseState.telemetry?.quality.reason_code));
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

	function temporalReason(code: string | null | undefined): string {
		const reasons: Record<string, string> = {
			no_snapshot: 'Todavía no se ha recibido una lectura del coche.',
			observed_at_missing: 'La fuente no ha indicado cuándo se observó esta lectura.',
			observed_at_invalid: 'La fecha de observación recibida no es válida.',
			observed_at_future: 'La fecha recibida está adelantada; no permite comprobar la antigüedad.',
			observation_recent: 'Lectura dentro de la ventana temporal de Pulse.',
			observation_stale: 'Lectura atrasada. Consulta su fecha antes de usarla.',
			observation_expired: 'Lectura caducada. Puedes consultar sus valores anteriores en Estados.'
		};
		return code && reasons[code] ? reasons[code] : 'La antigüedad de la lectura no se ha podido comprobar.';
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

<div class="overview-page cockpit-page">
	<header class="overview-heading cockpit-intro">
		<div class="profile-heading">
			<span class="cockpit-eyebrow">MI COCHE · B10</span>
			<h2 id="vehicle-profile-title">{profileName}</h2>
			<p>{profileModel} <span>· Perfil personal</span></p>
		</div>
		<span class="status-pill" class:status-demo={pulseState.mode === 'demo'} class:status-neutral={pulseState.mode !== 'demo'}>
			<span class="status-dot"></span>{displayMode}
		</span>
	</header>

	<article class="overview-hero cockpit-console" aria-labelledby="vehicle-profile-title">
		<div class="overview-layout cockpit-layout">
			<section class="vehicle-scene cockpit-vehicle" aria-label="Vehículo y lecturas">
				<div class="vehicle-display cockpit-stage">
					<div class="cockpit-artwork">
						<VehicleArtwork color={pulseState.settings.vehicle_color ?? null} />
					</div>
					<div class="cockpit-instruments">
						<div class="cockpit-reading-state" class:cockpit-reading-stale={readingIsUncertain} class:cockpit-reading-demo={pulseState.mode === 'demo'}>
							<Glyph name="clock" size={15} />
							<span>{readingStatus}</span>
						</div>
						<dl class="vehicle-readings cockpit-readings" aria-label="Autonomía y batería">
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
							<meter class="battery-level cockpit-battery-meter" aria-label="Nivel de batería recibido" min="0" max="100" value={batteryPercentage}>{number(batteryPercentage)} %</meter>
						{/if}
						<div class="reading-caption cockpit-provenance" aria-label="Procedencia de las lecturas del coche">
							<p>{sourceLabel} <span aria-hidden="true">·</span> {observedAt ? absoluteDateTime(observedAt) : 'Sin lectura disponible'}</p>
							{#if pulseState.mode === 'telemetry_read_only'}<p class="temporal-explanation">{temporalExplanation} Vigencia física sin comprobar.</p>{:else if pulseState.mode !== 'demo'}<p>{connectionReason}</p>{/if}
						</div>
						{#if chargerStatus?.observation}
							<a class="charge-glance" href="/energy" aria-label="Abrir Energía y el estado del punto de carga">
								<Glyph name="plug" size={18} /><span><strong>Punto de carga · {chargerStateLabel(chargerStatus)}</strong><span>{chargerStatus.observation.power_w == null ? 'Potencia sin dato' : `${number(chargerStatus.observation.power_w / 1000, true)} kW`} · {chargerStatus.observation.charged_kwh == null ? 'Energía sin dato' : `${number(chargerStatus.observation.charged_kwh)} kWh recibidos`}</span></span><Glyph name="chevron" size={16} />
							</a>
						{/if}
					</div>
				</div>

				<nav class="cockpit-quick-actions" aria-label="Accesos frecuentes">
					<a href="/energy"><Glyph name="energy" size={18} /><span>Registrar carga</span></a>
					<a href="/activity"><Glyph name="activity" size={18} /><span>Cuaderno de viaje</span></a>
					<a href="/settings#preparation"><Glyph name="plug" size={18} /><span>Preparar conexión</span></a>
				</nav>

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
					<div><dt><Glyph name="charge" size={16} />Carga del coche</dt><dd>{booleanState(vehicleValues?.charging, 'En curso', 'Detenida')}</dd></div>
				<div><dt><Glyph name="car" size={16} />Odómetro</dt><dd>{number(vehicleValues?.odometer_km)}{vehicleValues?.odometer_km == null ? '' : ' km'}</dd></div>
					</dl>

					<div class="control-actions" aria-label="Acciones y acceso al historial">
						{#each controlActions as action (action.id)}
							<button
								class="control-action"
								type="button"
								disabled={pulseState.mode !== 'demo' || !action.command?.enabled || busy || !online}
								title={pulseState.mode === 'demo' ? action.command?.reason : connectionReason}
								aria-describedby="vehicle-controls-help"
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
									aria-describedby="vehicle-controls-help"
									onclick={() => runCommand(action)}
								>
									<span class="control-circle"><Glyph name={action.icon} size={19} /></span>
									<span>{action.label}</span>
								</button>
							{/each}
						</div>
					</details>
					<p class="control-footnote" id="vehicle-controls-help">
						{#if !online}Sin conexión. Vuelve a conectar Pulse para utilizar las acciones disponibles.
						{:else if pulseState.mode === 'demo'}Acciones de ejemplo · requieren confirmación.
						{:else}Controles físicos bloqueados: cada orden requiere una conexión compatible y una comprobación con el coche. <a href="/settings#preparation">Revisar preparación</a>{/if}
					</p>

				</section>

				{#if latestTelemetry && pulseState.mode === 'telemetry_read_only'}
					<details class="sensor-readings">
						<summary>Temperaturas y neumáticos</summary>
						<dl>
							{#each telemetrySensorFields as field (field.key)}
								<div><dt>{field.label}</dt><dd>{sensorValue(vehicleValues?.[field.key], field.unit)}</dd></div>
							{/each}
						</dl>
						<p>Estas magnitudes usan la misma hora de observación y disponibilidad temporal que la lectura del vehículo. La vigencia física sigue pendiente de comprobar con el coche.</p>
					</details>
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
					<div class="context-heading">
						<h3 id="charger-context-title">Punto de carga</h3>
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
						<p class="charger-received">Recibido por Pulse: {absoluteDateTime(chargerStatus.observation.received_at)}</p>
						{#if vehicleValues?.charging != null && vehicleValues.charging !== chargerStatus.observation.charging}
							<p class="source-notice">El coche y el punto comunican estados distintos. Son fuentes independientes; sus lecturas pueden corresponder a momentos diferentes.</p>
						{/if}
						
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
						<dl class="session-timing"><div><dt>Fin</dt><dd>{absoluteDateTime(latestAutomaticCharge.ended_at)}</dd></div><div><dt>Tiempo activo</dt><dd>{activeDuration(latestAutomaticCharge.active_seconds)}</dd></div></dl>
						<p class="charge-cost-status">{latestAutomaticCharge.pricing_status === 'ready' && latestAutomaticCharge.pvpc ? 'Estimación PVPC guardada' : latestAutomaticCharge.pricing_status === 'pending' ? 'Coste PVPC pendiente' : 'Sin coste calculado'}</p>

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


			</aside>
		</div>
		<section class="connection-context cockpit-status-band" aria-labelledby="connection-context-title">
			<div class="context-heading"><h3 id="connection-context-title">Conexión y lecturas</h3><span class="badge">{connectionLabel}</span></div>
			<dl class="connection-readings">
				<div><dt>Lector del coche</dt><dd>{collectorLabel}</dd></div>
				<div><dt>Último contacto con el proveedor</dt><dd>{pulseState.connection.last_contact_at ? absoluteDateTime(pulseState.connection.last_contact_at) : 'Sin contacto registrado'}</dd></div>
				<div><dt>Última observación del proveedor</dt><dd>{pulseState.connection.last_observed_at ? absoluteDateTime(pulseState.connection.last_observed_at) : 'Sin observación registrada'}</dd></div>
				<div><dt>Controles físicos</dt><dd>Pendientes de validación</dd></div>
			</dl>
			<details class="charger-details"><summary>Detalles de conexión</summary><p class="context-copy">{connectionReason}</p></details>
			<a class="context-link" href="/settings#preparation">Preparar la conexión <Glyph name="arrow" size={16} /></a>
		</section>
	</article>

	<section class="overview-mobility cockpit-journal" aria-labelledby="mobility-title">
	<header class="mobility-heading"><div><span class="cockpit-eyebrow">CUADERNO DE VIAJE</span><h2 id="mobility-title">Tu actividad reciente</h2></div><a class="context-link" href="/activity">Abrir diario <Glyph name="arrow" size={16} /></a></header>
	<div class="overview-records cockpit-journal-grid" aria-label="Energía, actividad y mantenimiento">
		<article class="record-summary cockpit-journal-entry energy-summary">
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

		<article class="record-summary cockpit-journal-entry journey-summary">
			<div class="record-heading"><h2>Último trayecto</h2><a href="/activity">Abrir diario <Glyph name="arrow" size={16} /></a></div>
			{#if latestJourney}
				<p class="journey-source">{pulseState.mode === 'demo' ? 'Trayecto ficticio · demo' : 'Registro guardado en Pulse'} · {absoluteDateTime(latestJourney.started_at)}</p>
				<div class="journey-path"><span class="journey-stop" aria-hidden="true"></span><strong>{latestJourney.from_label}</strong><span class="journey-line" aria-hidden="true"></span><Glyph name="arrow" size={18} /><strong>{latestJourney.to_label}</strong></div>
				<dl class="journey-metrics"><div><dt>Distancia</dt><dd>{number(latestJourney.distance_km)} km</dd></div><div><dt>Duración</dt><dd>{count(latestJourney.duration_min)} min</dd></div><div><dt>Energía del registro</dt><dd>{number(latestJourney.energy_kwh)} kWh</dd></div></dl>
			{:else}
				<div class="quiet-empty"><Glyph name="navigation" size={26} /><h3>Tu diario empieza con un trayecto</h3><p>Pulse todavía no recibe recorridos del coche. Puedes preparar un destino en Mapa.</p><a class="context-link" href="/navigation">Preparar un destino <Glyph name="arrow" size={16} /></a></div>
			{/if}
		</article>

		<article class="record-summary cockpit-journal-entry maintenance-summary">
			<div class="record-heading"><h2>Tu coche, al día</h2><a href="/activity">Mantenimiento <Glyph name="arrow" size={16} /></a></div>
			{#if nextMaintenance}
				<p class="maintenance-date"><Glyph name="clock" size={18} />{dateOnly(nextMaintenance.due_date)}</p>
				<h3>{nextMaintenance.title}</h3>
				<p>Recordatorio guardado en Pulse · pendiente de completar.{pulseState.mode === 'demo' ? ' La demo puede incluir tareas de ejemplo.' : ''}</p>
			{:else}<div class="quiet-empty"><Glyph name="check" size={26} /><h3>Sin recordatorios pendientes</h3><p>Añade revisiones y tareas a tu diario para tenerlas a mano. Esto no confirma el mantenimiento del coche.</p></div>{/if}
		</article>

		<article class="record-summary cockpit-journal-entry protection-summary">
			<div class="record-heading"><h2>Historial de estados</h2><a href="/protection">Ver cambios <Glyph name="arrow" size={16} /></a></div>
			<strong class="protection-count">{count(protectionCount)} <span>{protectionDescription}</span></strong>
			<p>{pulseState.mode === 'demo' ? 'Eventos ficticios de la demostración.' : protectionCount ? 'Cambios guardados entre observaciones recibidas.' : 'Aún no hay cambios observados en Pulse.'} No hay cámaras ni modo centinela.</p>
		</article>
	</div>
	</section>

	<footer class="overview-footer">Estado de Pulse actualizado: {absoluteDateTime(profileUpdatedAt)} <span aria-hidden="true">·</span> Conexión: {connectionLabel}</footer>
</div>

<style>
	.overview-page { display: grid; gap: clamp(34px, 4.4vw, 58px); min-width: 0; }
	.overview-hero, .profile-heading, .vehicle-scene, .vehicle-task { min-width: 0; }
	.overview-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin: 0; }
	.overview-heading h2 { margin: 0; font-size: clamp(2.125rem, 4.2vw, 3.5rem); font-weight: 590; line-height: 1.05; letter-spacing: -.05em; overflow-wrap: anywhere; }
	.overview-heading p { margin: 10px 0 0; color: var(--muted); font-size: var(--text-label); line-height: 1.5; overflow-wrap: anywhere; }
	.overview-heading :global(.status-pill) { flex: 0 1 auto; min-height: 36px; font-size: var(--text-caption); }
	.overview-layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(19rem, .82fr); gap: clamp(24px, 3.2vw, 48px); align-items: start; }
	.vehicle-display { display: grid; min-width: 0; grid-template-columns: minmax(0, 1.2fr) minmax(0, .8fr); align-items: center; gap: clamp(20px, 3vw, 40px); padding: 0; border: 0; border-radius: 0; background: transparent; }
	.vehicle-scene :global(.vehicle-artwork) { min-width: 0; margin: 0; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
	.vehicle-scene :global(.vehicle-frame) { height: clamp(220px, 23vw, 330px); }
	.vehicle-scene :global(figcaption) { padding: 8px 0 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.45; text-align: left; }
	.vehicle-readings { display: grid; grid-template-columns: minmax(0, 1fr); align-items: end; gap: 20px; margin: 0; padding-top: 0; font-variant-numeric: tabular-nums; }
	.vehicle-readings > div { min-width: 0; }
	.vehicle-readings dt { color: var(--muted); font-size: var(--text-caption); font-weight: 650; letter-spacing: .04em; line-height: 1.5; text-transform: uppercase; }
	.vehicle-readings dd { margin: 5px 0 0; overflow-wrap: anywhere; }
	.range-reading dd { font-size: clamp(3rem, 5.4vw, 5.25rem); font-weight: 570; letter-spacing: -.055em; line-height: .98; }
	.battery-reading { align-self: end; padding-bottom: 0; }
	.battery-reading dd { display: flex; align-items: center; gap: 7px; font-size: clamp(1.125rem, 2.2vw, 1.5rem); font-weight: 610; }
	.battery-reading dd :global(svg) { flex-shrink: 0; color: var(--forest); }
	.reading-caption { max-width: 68ch; margin: 15px 0 0; }
	.reading-caption p { margin: 4px 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.55; overflow-wrap: anywhere; }
	.reading-caption .temporal-explanation { color: var(--muted-strong); }
	.battery-level { display: block; width: min(100%, 420px); height: 7px; margin: 10px 0 0; border: 0; border-radius: 9px; background: var(--line); }
	.battery-level::-webkit-meter-bar { height: 6px; border: 0; border-radius: 9px; background: var(--line); }
	.battery-level::-webkit-meter-optimum-value { border-radius: 9px; background: var(--forest); }
	.battery-level::-moz-meter-bar { border-radius: 9px; background: var(--forest); }
	.charge-glance { display: flex; width: fit-content; max-width: 100%; min-height: 56px; align-items: center; gap: 12px; margin: 18px 0 0; padding: 10px 14px; border: 1px solid color-mix(in srgb, var(--forest) 22%, var(--line)); border-radius: 14px; background: var(--forest-soft); color: var(--forest); text-decoration: none; }
	.charge-glance > span { display: grid; min-width: 0; gap: 4px; }
	.charge-glance strong { font-size: var(--text-caption); overflow-wrap: anywhere; }
	.charge-glance span span { color: var(--muted-strong); font-size: var(--text-caption); overflow-wrap: anywhere; }
	.charge-glance :global(svg) { flex: 0 0 auto; }
	.color-picker { margin: 14px 0 26px; }
	.color-picker > summary { display: flex; width: fit-content; min-height: 48px; align-items: center; gap: 8px; color: var(--forest); font-size: var(--text-caption); cursor: pointer; list-style: none; }
	.color-picker > summary::-webkit-details-marker { display: none; }
	.color-picker > summary span { color: var(--muted); }
	.vehicle-colors { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 4px; }
	.color-swatch { display: grid; width: 48px; height: 48px; place-items: center; border: 1px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
	.color-swatch span { width: 24px; height: 24px; border: 1px solid var(--line-strong); border-radius: 50%; background: var(--swatch); box-shadow: inset 0 2px 5px rgb(255 255 255 / 25%); }
	.color-swatch.selected { border-color: var(--forest); }
	.color-swatch:disabled { cursor: not-allowed; opacity: .55; }
	.color-name { margin-left: 6px; color: var(--muted); font-size: var(--text-caption); }
	.vehicle-state { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px 12px; margin: 0 0 18px; padding: 18px 0; border-block: 1px solid var(--line); }
	.vehicle-state > div { min-width: 0; }
	.vehicle-state dt { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: var(--text-caption); line-height: 1.4; }
	.vehicle-state dt :global(svg) { flex: 0 0 auto; }
	.vehicle-state dd { margin: 7px 0 0; font-size: var(--text-label); font-weight: 610; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
	.control-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; max-width: 540px; }
	.control-action, .alternate-action { display: flex; min-width: 48px; min-height: 88px; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 8px; padding: 6px; border: 1px solid transparent; border-radius: 14px; background: transparent; color: var(--ink); cursor: pointer; font-size: var(--text-caption); line-height: 1.35; text-align: left; text-decoration: none; }
	.control-action:hover:not(:disabled), .alternate-action:hover:not(:disabled) { background: var(--surface-soft); }
	.control-circle { display: grid; width: 52px; height: 52px; flex: 0 0 52px; place-items: center; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); color: var(--muted-strong); transition: background-color var(--motion-fast) ease, border-color var(--motion-fast) ease, color var(--motion-fast) ease; }
	.control-action:hover:not(:disabled) .control-circle, .alternate-action:hover:not(:disabled) .control-circle { border-color: var(--forest); background: var(--forest-soft); color: var(--forest); }
	.control-action:disabled, .alternate-action:disabled { color: var(--muted); cursor: not-allowed; }
	.control-action:disabled .control-circle, .alternate-action:disabled .control-circle { color: var(--muted); }
	.control-label { font-weight: 600; overflow-wrap: anywhere; }
	.inline-action { display: inline-flex; min-width: 48px; min-height: 48px; align-items: center; justify-content: center; padding: 8px 14px; border: 1px solid var(--line-strong); border-radius: 12px; background: var(--surface); color: var(--ink); font-size: var(--text-caption); font-weight: 600; cursor: pointer; }
	.inline-action:hover:not(:disabled) { border-color: var(--forest); background: var(--forest-soft); color: var(--forest); }
	.inline-action:disabled { opacity: .55; cursor: not-allowed; }
	.alternate-actions { margin-top: 12px; }
	.alternate-actions summary, .field-source-details summary, .charger-details summary { display: flex; min-height: 48px; align-items: center; gap: 10px; color: var(--muted-strong); font-size: var(--text-caption); font-weight: 600; cursor: pointer; list-style: revert; }
	.alternate-actions summary { justify-content: flex-start; }
	.alternate-action-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(5.5rem, 1fr)); gap: 8px; padding: 8px 0; }
	.control-footnote { max-width: 70ch; margin: 8px 0 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.55; }
	.control-footnote a { color: var(--forest); text-decoration: underline; text-underline-offset: 3px; }
	.field-source-details { margin-top: 6px; }
	.sensor-readings { margin-top: 6px; }
	.sensor-readings summary { display: flex; min-height: 48px; align-items: center; justify-content: flex-start; color: var(--muted-strong); font-size: var(--text-caption); font-weight: 600; cursor: pointer; list-style: revert; }
	.sensor-readings dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 11rem), 1fr)); gap: 14px 20px; margin: 12px 0; }
	.sensor-readings dl > div { min-width: 0; }
	.sensor-readings dt { color: var(--muted); font-size: var(--text-caption); }
	.sensor-readings dd { margin: 5px 0 0; font-size: var(--text-label); font-weight: 650; overflow-wrap: anywhere; }
	.sensor-readings p { color: var(--muted); font-size: var(--text-caption); line-height: 1.6; }
	.field-source-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr)); gap: 10px 16px; margin: 8px 0; }
	.field-source-list div { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }
	.field-source-list dt, .received-times span { color: var(--muted); }
	.field-source-list dd { margin: 0; overflow-wrap: anywhere; }
	.field-source-list, .received-times { font-size: var(--text-caption); line-height: 1.6; }
	.received-times p { display: flex; flex-wrap: wrap; gap: 5px 8px; margin: 8px 0; }
	.received-times strong { font-weight: 500; overflow-wrap: anywhere; }
	.vehicle-task { display: grid; min-width: 0; gap: 20px; }
	.charger-context { min-width: 0; padding: clamp(20px, 2.3vw, 30px); border: 1px solid var(--line); border-radius: 24px; background: var(--surface-soft); }
	.context-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
	.context-heading h3 { margin: 0; font-size: 1rem; font-weight: 620; line-height: 1.4; }
	.charger-context .context-heading h3 { font-size: clamp(1.25rem, 2vw, 1.5rem); letter-spacing: -.035em; }
	.context-heading :global(.badge) { font-size: var(--text-caption); }
	.charge-connection { display: flex; align-items: center; gap: 16px; margin: 18px 0; color: var(--muted); }
	.charge-connection span { height: 2px; flex: 1; background: var(--line-strong); }
	.charge-connection .flow-active { background: var(--forest); }
	.charger-secondary { display: grid; gap: 8px; margin: 10px 0; font-size: var(--text-caption); }
	.charger-secondary dd { margin: 4px 0; }
	.charger-readings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 16px; margin: 22px 0 0; }
	.charger-readings > div { min-width: 0; }
	.charger-readings dt { color: var(--muted); font-size: var(--text-caption); line-height: 1.5; }
	.charger-readings dd { margin: 6px 0 0; font-size: var(--text-label); font-weight: 600; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
	.charger-metric { padding-bottom: 12px; border-bottom: 1px solid var(--line); }
	.charger-metric dd { font-size: clamp(1.5rem, 2.5vw, 2rem); font-weight: 570; letter-spacing: -.04em; }
	.charger-source { margin: 18px 0 8px; color: var(--muted); font-size: var(--text-caption); overflow-wrap: anywhere; }
	.charger-received, .charge-cost-status { margin: 8px 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.5; overflow-wrap: anywhere; }
	.source-notice { margin: 12px 0 0; padding: 12px 0; border-block: 1px solid var(--line); color: var(--muted-strong); font-size: var(--text-caption); line-height: 1.6; }
	.session-timing, .connection-readings { display: grid; gap: 10px; margin: 14px 0 0; font-size: var(--text-caption); line-height: 1.5; }
	.session-timing > div, .connection-readings > div { display: grid; min-width: 0; gap: 4px; }
	.session-timing dt, .connection-readings dt { color: var(--muted); }
	.session-timing dd, .connection-readings dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
	.context-copy, .charger-details p { margin: 10px 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.6; overflow-wrap: anywhere; }
	.context-link { display: inline-flex; min-height: 48px; align-items: center; gap: 9px; color: var(--forest); font-size: var(--text-label); font-weight: 600; text-decoration: none; }
	.context-link:hover, .record-heading a:hover { text-decoration: underline; text-underline-offset: 4px; }
	.charger-context .context-link { display: flex; justify-content: space-between; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
	.latest-charge { min-width: 0; padding: 8px 2px 0; }
	.charge-date { margin: 8px 0; color: var(--muted); font-size: var(--text-caption); line-height: 1.55; }
	.latest-charge-values { display: flex; justify-content: space-between; gap: 16px; margin-top: 10px; font-size: 1.125rem; font-variant-numeric: tabular-nums; }
	.latest-charge-values strong { font-size: 1.5rem; font-weight: 570; letter-spacing: -.035em; }
	.latest-charge + .latest-charge { padding-top: 20px; border-top: 1px solid var(--line); }
	.connection-context { margin-top: 28px; padding-top: 22px; border-top: 1px solid var(--line); }
	.connection-readings { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px 24px; }
	.connection-context > .context-link { margin-top: 4px; }
	.overview-mobility { padding-top: clamp(26px, 3.4vw, 42px); border-top: 1px solid var(--line); }
	.mobility-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
	.mobility-heading h2 { margin: 0; font-size: clamp(1.75rem, 3vw, 2.5rem); font-weight: 570; letter-spacing: -.045em; line-height: 1.12; }
	.overview-records { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 34px 48px; }
	.record-summary { min-width: 0; padding-top: 18px; border-top: 1px solid var(--line); }
	.record-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
	.record-heading h2 { margin: 0; font-size: var(--text-label); font-weight: 620; line-height: 1.45; }
	.record-heading a { display: inline-flex; min-height: 48px; align-items: center; gap: 6px; color: var(--forest); font-size: var(--text-caption); text-decoration: none; }
	.record-totals { display: flex; flex-wrap: wrap; gap: 20px 36px; margin-bottom: 20px; }
	.record-totals > div { display: grid; min-width: 0; gap: 6px; }
	.record-totals span { color: var(--muted); font-size: var(--text-caption); }
	.record-totals strong { font-size: 1.625rem; font-weight: 570; letter-spacing: -.04em; }
	.record-totals strong small { font-size: var(--text-label); font-weight: 400; letter-spacing: normal; }
	.charge-history-mini { margin: 0; padding: 0; list-style: none; }
	.charge-history-mini li { border-bottom: 1px solid var(--line); }
	.charge-history-mini a { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(44px, .8fr) auto auto; align-items: center; gap: 12px; min-height: 64px; font-size: var(--text-caption); text-decoration: none; }
	.mini-charge-label { color: var(--muted-strong); line-height: 1.55; overflow-wrap: anywhere; }
	.mini-charge-label small { display: block; color: var(--muted); font-size: var(--text-caption); }
	.mini-charge-bar { height: 4px; border-radius: 9px; background: var(--line); }
	.mini-charge-bar > span { display: block; height: 100%; border-radius: inherit; background: var(--forest); }
	.record-summary p { color: var(--muted); font-size: var(--text-label); line-height: 1.6; }
	.record-summary .record-note, .journey-source { font-size: var(--text-caption); }
	.journey-path { display: flex; min-width: 0; align-items: center; gap: 10px; padding: 18px 0; }
	.journey-path strong { min-width: 0; font-size: var(--text-label); overflow-wrap: anywhere; }
	.journey-stop { width: 8px; height: 8px; flex: 0 0 8px; border: 2px solid var(--forest); border-radius: 50%; }
	.journey-line { height: 1px; min-width: 8px; flex: 1; background: var(--line-strong); }
	.journey-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 0; }
	.journey-metrics dt { color: var(--muted); font-size: var(--text-caption); }
	.journey-metrics dd { margin: 7px 0 0; font-size: 1.125rem; font-weight: 570; overflow-wrap: anywhere; }
	.quiet-empty { padding: 16px 0; }
	.quiet-empty :global(svg) { color: var(--forest); }
	.record-summary h3 { margin: 12px 0 0; font-size: 1rem; font-weight: 610; line-height: 1.4; overflow-wrap: anywhere; }
	.maintenance-date { display: flex; align-items: center; gap: 8px; margin: 14px 0 0; }
	.protection-count { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; margin-top: 18px; font-size: 2rem; font-weight: 570; letter-spacing: -.04em; }
	.protection-count span { color: var(--muted); font-size: var(--text-label); font-weight: 400; letter-spacing: normal; }
	.overview-footer { padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: var(--text-caption); line-height: 1.6; overflow-wrap: anywhere; }
	.overview-page summary:focus-visible, .color-swatch:focus-visible, .control-action:focus-visible, .alternate-action:focus-visible, .context-link:focus-visible, .record-heading a:focus-visible, .charge-history-mini a:focus-visible, .inline-action:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
	@media (max-width: 1120px) {
		.overview-layout { grid-template-columns: minmax(0, 1fr); gap: 26px; }
		.vehicle-scene { max-width: 860px; width: 100%; margin-inline: auto; }
		.vehicle-task { grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 20px 28px; }
		.charger-context { grid-row: span 2; }
		.latest-charge + .latest-charge { padding-top: 0; border-top: 0; }
	}
	@media (max-width: 700px) {
		.overview-page { gap: 38px; }
		.overview-heading { align-items: flex-start; gap: 12px; }
		.overview-heading h2 { font-size: clamp(1.875rem, 8vw, 2.5rem); }
		.overview-heading :global(.status-pill) { max-width: 45%; white-space: normal; }
		.overview-heading p { font-size: var(--text-caption); }
		.overview-heading p span { display: none; }
		.overview-layout { gap: 24px; }
		.vehicle-display { grid-template-columns: minmax(0, 1fr); gap: 14px; }
		.vehicle-scene :global(.vehicle-frame) { height: clamp(190px, 52vw, 250px); }
		.vehicle-readings { grid-template-columns: minmax(0, 1fr) auto; gap: 8px 12px; }
		.range-reading dd { font-size: clamp(2.75rem, 12vw, 3.5rem); }
		.battery-reading dd { font-size: 1.125rem; }
		.color-picker { margin-bottom: 20px; }
		.vehicle-state { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 12px; padding-block: 16px; }
		.control-actions { gap: 4px; }
		.control-action { align-items: center; padding-inline: 2px; text-align: center; }
		.control-circle { width: 50px; height: 50px; flex-basis: 50px; border-radius: 17px; }
		.vehicle-task { grid-template-columns: minmax(0, 1fr); gap: 18px; }
		.charger-context { grid-row: auto; padding: 20px; border-radius: 20px; }
		.charger-readings { gap: 16px 12px; }
		.field-source-list { grid-template-columns: minmax(0, 1fr); }
		.connection-readings { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
		.mobility-heading { align-items: flex-start; flex-direction: column; gap: 4px; margin-bottom: 18px; }
		.overview-records { grid-template-columns: minmax(0, 1fr); gap: 22px; }
		.record-summary { padding-top: 16px; }
		.charge-history-mini a { grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; }
		.mini-charge-bar { display: none; }
		.journey-path { flex-wrap: wrap; }
		.journey-line { flex-basis: 16px; }
	}
	@media (max-width: 360px) {
		.overview-heading { flex-wrap: wrap; }
		.overview-heading :global(.status-pill) { max-width: 100%; }
		.vehicle-state { gap: 14px 8px; }
		.control-label { max-width: 7ch; }
		.journey-metrics { grid-template-columns: minmax(0, 1fr); gap: 10px; }
		.journey-metrics > div { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
		.journey-metrics dd { text-align: right; }
		.latest-charge-values { flex-wrap: wrap; }
	}
</style>
