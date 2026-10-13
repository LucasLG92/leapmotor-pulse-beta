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

<div class="overview-page">
 <article class="vehicle-home" aria-labelledby="vehicle-profile-title">
  <header class="vehicle-heading">
   <div><p class="eyebrow">TU GARAJE PERSONAL</p><h2 id="vehicle-profile-title">{profileName}<span class="model-signature">{profileModel}</span></h2></div>
   <a class="personalize-link" href="/settings" aria-label="Personalizar perfil del vehículo"><Glyph name="settings" size={20}/></a>
  </header>
  <div class="hero-layout">
   <div class="car-presentation"><span class="stage-lettering" aria-hidden="true">B10</span><span class="stage-brand" aria-hidden="true">LEAPMOTOR</span>
    <VehicleArtwork color={pulseState.settings.vehicle_color ?? null}/>
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

    

				
   </div>
   <div class="car-command-center">
    <div class="reading-status" class:uncertain={readingIsUncertain}><span class="status-dot" aria-hidden="true"></span>{pulseState.mode === 'demo' ? 'Lecturas simuladas' : readingStatus}</div>
    {#if vehicleValues?.range_km == null && vehicleValues?.battery_pct == null}
     <div class="readings-empty">
      <span class="connection-symbol" aria-hidden="true"><Glyph name="car" size={23}/><span></span><Glyph name="plug" size={20}/></span>
      <h3>{pulseState.mode === 'demo' ? 'La demo no tiene lecturas' : pulseState.mode === 'telemetry_read_only' ? 'Esperando una lectura' : 'Prepara la conexión'}</h3>
      <p>{pulseState.mode === 'demo' ? 'No hay valores de batería o autonomía disponibles en esta demostración.' : pulseState.mode === 'telemetry_read_only' ? temporalExplanation : 'La batería y la autonomía aparecerán aquí cuando Pulse reciba lecturas del coche.'}</p>
      <a class="connection-next" href={pulseState.mode === 'telemetry_read_only' || pulseState.mode === 'demo' ? '/protection' : '/settings#preparation'}>{pulseState.mode === 'telemetry_read_only' || pulseState.mode === 'demo' ? 'Revisar lecturas' : 'Preparar Pulse'}<Glyph name="arrow" size={18}/></a>
     </div>
    {:else}
    <dl class="vehicle-readings" aria-label="Autonomía y batería">
     <div class="range-reading"><dt>Autonomía estimada</dt><dd>{number(vehicleValues?.range_km)}<span>{vehicleValues?.range_km == null ? '' : ' km'}</span></dd></div>
     <div class="battery-reading"><dt><Glyph name="battery" size={16}/> Batería</dt><dd>{number(vehicleValues?.battery_pct)}<span>{vehicleValues?.battery_pct == null ? '' : ' %'}</span></dd>
      {#if batteryPercentage !== null}<meter aria-label="Nivel de batería recibido" min="0" max="100" value={batteryPercentage}>{number(batteryPercentage)} %</meter>{/if}
     </div>
    </dl>
    {/if}
    <section class="vehicle-controls" aria-labelledby="vehicle-controls-title">
					<div class="controls-intro sr-only"><h3 id="vehicle-controls-title">Acciones del vehículo</h3></div>
					

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


					
					<p class="control-footnote" id="vehicle-controls-help">
						{#if !online}Sin conexión. Vuelve a conectar Pulse para utilizar las acciones disponibles.
						{:else if pulseState.mode === 'demo'}Acciones de ejemplo · requieren confirmación.
						{:else}Controles físicos bloqueados: cada orden requiere una conexión compatible y una comprobación con el coche. <a href="/settings#preparation">Revisar preparación</a>{/if}
					</p>
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

				</section>

   </div>
  </div>
  <div class="vehicle-details-row"><dl class="vehicle-state" aria-label="Estado recibido del vehículo">
					<div><dt><Glyph name={vehicleValues?.locked === true ? 'lock' : vehicleValues?.locked === false ? 'unlock' : 'activity'} size={16} />Cierre</dt><dd>{booleanState(vehicleValues?.locked, 'Cerrado', 'Abierto')}</dd></div>
					<div><dt><Glyph name="climate" size={16} />Clima</dt><dd>{booleanState(vehicleValues?.climate_on, 'Activo', 'Inactivo')}</dd></div>
					<div><dt><Glyph name="charge" size={16} />Carga del coche</dt><dd>{booleanState(vehicleValues?.charging, 'En curso', 'Detenida')}</dd></div>
				<div><dt><Glyph name="car" size={16} />Odómetro</dt><dd>{number(vehicleValues?.odometer_km)}{vehicleValues?.odometer_km == null ? '' : ' km'}</dd></div>
					</dl></div>
  <details class="reading-details"><summary><Glyph name="clock" size={15}/><span>{pulseState.mode === 'demo' ? 'Origen de las lecturas' : displayMode} · {observedAt ? absoluteDateTime(observedAt) : 'Sin lectura disponible'}</span><Glyph name="chevron" size={15}/></summary>
<p>{sourceLabel}</p>
   {#if pulseState.mode === 'telemetry_read_only'}<p>{temporalExplanation} Vigencia física sin comprobar.</p>{:else if pulseState.mode !== 'demo'}<p>{connectionReason}</p>{/if}
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
  </details>
 </article>
 <nav class="everyday-links" aria-label="Accesos frecuentes">
  <a href="/navigation"><span class="everyday-icon"><Glyph name="navigation" size={23}/></span><span><small>EL SIGUIENTE DESTINO</small><strong>¿Dónde vamos?</strong></span><Glyph name="arrow" size={23}/></a>
  <a href="/energy"><span class="everyday-icon"><Glyph name="energy" size={23}/></span><span><small>TU ENERGÍA</small><strong>Registrar una carga</strong></span><Glyph name="arrow" size={23}/></a>
 </nav>
 <section class="journal-preview" aria-labelledby="journal-title">
  <header class="section-heading"><h2 id="journal-title">En tu diario</h2><a class="text-button" href="/activity">Ver todo <Glyph name="arrow" size={16}/></a></header>
  <div class="journal-columns">
   <article class="journey-preview"><div class="preview-label"><Glyph name="navigation" size={18}/><h3>Último trayecto</h3></div>
    {#if latestJourney}<p class="source-label">{pulseState.mode === 'demo' ? 'Demo · trayecto ficticio' : 'Registro guardado'} · {absoluteDateTime(latestJourney.started_at)}</p><div class="journey-path"><strong>{latestJourney.from_label}</strong><Glyph name="arrow" size={18}/><strong>{latestJourney.to_label}</strong></div><dl class="journey-metrics"><div><dt>Distancia</dt><dd>{number(latestJourney.distance_km)} km</dd></div><div><dt>Duración</dt><dd>{count(latestJourney.duration_min)} min</dd></div><div><dt>Energía</dt><dd>{number(latestJourney.energy_kwh)} kWh</dd></div></dl>
    {:else}<h4>Tu primer trayecto empieza aquí</h4><p>Guarda tus recorridos en el diario. Pulse todavía no recibe trayectos del coche.</p><a class="text-button" href="/activity">Abrir diario <Glyph name="arrow" size={16}/></a>{/if}
   </article>
   <article class="energy-preview"><div class="preview-label"><Glyph name="energy" size={18}/><h3>Cargas recientes</h3></div>
    {#if recentCharges.length}<ol class="charge-list">{#each recentCharges as session (session.id)}<li><a href="/energy"><span><strong>{number(session.kwh)} kWh</strong><small>{absoluteDateTime(session.started_at)} · {session.source === 'demo' ? 'Demo' : 'Manual'}</small></span><span class="charge-bar" aria-hidden="true"><span style={`width: ${Math.max(0, session.kwh) / largestRecentCharge * 100}%`}></span></span><strong>{euros(session.cost_eur)}</strong></a></li>{/each}</ol>{:else}<h4>Aún no hay cargas guardadas</h4><p>Registra energía e importe para tener tus cargas a mano.</p><a class="text-button" href="/energy">Añadir una carga <Glyph name="arrow" size={16}/></a>{/if}
    <p class="source-label">Importes guardados · los ejemplos y registros manuales se distinguen por su origen.</p>
   </article>
  </div>
  <a class="maintenance-preview" href="/activity"><Glyph name="clock" size={20}/><span><strong>{nextMaintenance ? nextMaintenance.title : 'Tu mantenimiento, organizado'}</strong><small>{nextMaintenance ? `${dateOnly(nextMaintenance.due_date)} · recordatorio pendiente` : 'Añade revisiones y recordatorios en tu diario'}{pulseState.mode === 'demo' ? ' · puede incluir ejemplos' : ''}</small></span><Glyph name="arrow" size={18}/></a>
 </section>
 <details class="connection-drawer"><summary><span><Glyph name="plug" size={20}/><strong>Conexión y punto de carga</strong></span><span>{connectionLabel}<Glyph name="chevron" size={16}/></span></summary><div class="connection-grid"><aside class="vehicle-task" aria-label="Contexto y estado de carga">



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


			</aside> <section class="connection-context" aria-labelledby="connection-context-title">
			<div class="context-heading"><h3 id="connection-context-title">Conexión y lecturas</h3><span class="badge">{connectionLabel}</span></div>
			<dl class="connection-readings">
				<div><dt>Lector del coche</dt><dd>{collectorLabel}</dd></div>
				<div><dt>Último contacto con el proveedor</dt><dd>{pulseState.connection.last_contact_at ? absoluteDateTime(pulseState.connection.last_contact_at) : 'Sin contacto registrado'}</dd></div>
				<div><dt>Última observación del proveedor</dt><dd>{pulseState.connection.last_observed_at ? absoluteDateTime(pulseState.connection.last_observed_at) : 'Sin observación registrada'}</dd></div>
				<div><dt>Controles físicos</dt><dd>Pendientes de validación</dd></div>
			</dl>
			<details class="charger-details"><summary>Detalles de conexión</summary><p class="context-copy">{connectionReason}</p></details>
			<a class="context-link" href="/settings#preparation">Preparar la conexión <Glyph name="arrow" size={16} /></a>
		</section></div></details>
 <a class="state-history" href="/protection"><Glyph name="protection" size={18}/><span>{count(protectionCount)} {protectionDescription}</span><span>Ver estados <Glyph name="arrow" size={16}/></span></a>
 <footer class="overview-footer">Pulse actualizado {absoluteDateTime(profileUpdatedAt)}</footer>
</div>

<style>
.overview-page { min-width: 0; }
.vehicle-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 28px; }
.vehicle-heading .eyebrow { color: var(--forest); font-size: .625rem; letter-spacing: .16em; }
.vehicle-heading h2 { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; margin: 8px 0 0; font-size: clamp(2rem, 4vw, 3rem); line-height: 1.12; font-weight: 650; letter-spacing: -.055em; }
.model-signature { padding: 6px 10px; border: 1px solid var(--line); border-radius: 7px; font-size: .625rem; font-weight: 650; letter-spacing: .12em; color: var(--muted); }
.personalize-link { display: grid; width: 44px; height: 44px; flex: 0 0 44px; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--muted); background: var(--surface); }
.hero-layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(320px, 1fr); align-items: stretch; gap: 18px; margin-bottom: 22px; }
.car-presentation { position: relative; isolation: isolate; display: flex; flex-direction: column; justify-content: center; min-width: 0; min-height: 410px; padding: 25px 18px 14px; overflow: hidden; border: 1px solid var(--line); border-radius: 30px; background: radial-gradient(ellipse at 50% 24%, var(--surface) 10%, var(--surface-soft) 78%); }
.stage-lettering { position: absolute; z-index: -1; inset: -10px 0 auto; text-align: center; font-size: clamp(8rem, 17vw, 16rem); font-weight: 800; line-height: 1.05; letter-spacing: -.08em; color: color-mix(in srgb, var(--ink) 6%, transparent); user-select: none; }
.stage-brand { position: absolute; top: 24px; left: 26px; color: var(--muted); font-size: .5rem; letter-spacing: .28em; font-weight: 650; }
.car-presentation :global(.vehicle-frame) { height: 310px; }
.car-command-center { min-width: 0; display: flex; flex-direction: column; justify-content: center; padding: 30px; border-radius: var(--radius-panel); background: var(--premium-solid); color: var(--premium-solid-text); --ink: var(--premium-solid-text); --muted: var(--premium-solid-muted); --forest: #a4bfff; --forest-soft: #2a3855; --surface: #242c39; --surface-soft: #242c39; --line: var(--premium-solid-line); }
.reading-status { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: .625rem; margin-bottom: 24px; }
.reading-status.uncertain { color: #efbd78; }
.reading-status.uncertain .status-dot { background: #efbd78; }
.vehicle-readings { display: grid; grid-template-columns: 1.4fr 1fr; margin: 0 0 30px; gap: 14px; }
.vehicle-readings dt { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: .6875rem; }
.vehicle-readings dd { margin: 8px 0 0; font-size: clamp(2.5rem, 4vw, 4rem); font-weight: 500; line-height: 1.05; letter-spacing: -.06em; }
.vehicle-readings dd span { font-size: .875rem; color: var(--muted); letter-spacing: -.01em; margin-left: 4px; }
.battery-reading { border-left: 1px solid var(--line); padding-left: 20px; }
.battery-reading meter { display: block; width: 100%; max-width: 90px; height: 4px; margin-top: 12px; appearance: none; background: var(--line); border: 0; border-radius: 3px; overflow: hidden; }
.battery-reading meter::-webkit-meter-bar { background: var(--line); border: 0; }
.battery-reading meter::-webkit-meter-optimum-value { background: var(--forest); }
.battery-reading meter::-moz-meter-bar { background: var(--forest); }
.control-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.control-action { display: flex; min-width: 0; flex-direction: column; align-items: center; gap: 10px; padding: 0; border: 0; background: none; color: var(--ink); cursor: pointer; text-align: center; font-size: .6875rem; line-height: 1.4; }
.control-circle { display: grid; width: 52px; height: 52px; place-items: center; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); transition: background-color 160ms, border-color 160ms; }
.control-action:not(:disabled):hover .control-circle { background: var(--forest-soft); color: var(--forest); border-color: var(--forest); }
.control-action:active:not(:disabled) .control-circle { background: #3c4d6c; }
.control-action:disabled { color: var(--muted); cursor: not-allowed; }
.control-action:disabled .control-circle { background: var(--surface-soft); opacity: .6; }
.control-footnote { margin: 18px 0 0; color: var(--muted); font-size: .6875rem; line-height: 1.6; }
.control-footnote a { color: var(--forest); text-decoration: underline; }
.alternate-actions { margin-top: 14px; color: var(--muted); font-size: .6875rem; }
.alternate-actions summary { min-height: 36px; display: flex; align-items: center; cursor: pointer; }
.alternate-action-list { display: flex; flex-wrap: wrap; gap: 12px; padding: 10px 0; }
.alternate-action { display: flex; align-items: center; gap: 8px; border: 0; background: none; color: var(--ink); font-size: .75rem; cursor: pointer; }
.alternate-action:disabled { opacity: .55; }
.alternate-action .control-circle { width: 40px; height: 40px; }
.color-picker { position: relative; z-index: 1; max-width: 330px; margin: 4px auto 0; font-size: .625rem; }
.color-picker summary { display: flex; justify-content: center; align-items: center; min-height: 36px; gap: 8px; color: var(--muted); cursor: pointer; list-style: none; }
.color-picker summary::-webkit-details-marker { display: none; }
.color-picker summary > span { color: var(--ink); }
.vehicle-colors { display: flex; align-items: center; flex-wrap: wrap; justify-content: center; padding: 4px 0; gap: 2px; }
.color-swatch { display: grid; width: 44px; height: 44px; place-items: center; border: 1px solid transparent; border-radius: 50%; background: none; cursor: pointer; }
.color-swatch > span { width: 23px; height: 23px; border-radius: 50%; border: 1px solid var(--line-strong); background: var(--swatch); }
.color-swatch.selected { border-color: var(--forest); }
.color-name { width: 100%; text-align: center; color: var(--muted); }
.vehicle-state { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0; padding: 20px 26px; border: 1px solid var(--line); border-radius: 22px; background: var(--surface); }
.vehicle-state dt { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: .6875rem; }
.vehicle-state dd { margin: 8px 0 0; font-size: .9375rem; font-weight: 600; letter-spacing: -.025em; }
.reading-details { color: var(--muted); font-size: .6875rem; }
.reading-details > summary { display: flex; align-items: center; gap: 8px; min-height: 48px; cursor: pointer; list-style: none; }
.reading-details > summary > span { flex: 1; }
.reading-details > summary::-webkit-details-marker { display: none; }
.reading-details p { max-width: 75ch; }
.sensor-readings summary, .field-source-details summary { min-height: 44px; padding-block: 10px; cursor: pointer; }
.sensor-readings dl, .field-source-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
.sensor-readings dd, .field-source-list dd { margin: 3px 0 0; color: var(--ink); }
.received-times p { display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--line); padding-block: 10px; }
.everyday-links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 20px; }
.everyday-links a { display: flex; gap: 16px; align-items: center; padding: 24px; border-radius: 24px; background: var(--forest); color: var(--on-primary); }
.everyday-links a:last-child { background: var(--surface); color: var(--ink); border: 1px solid var(--line); }
.everyday-links a > span:nth-child(2) { flex: 1; min-width: 0; }
.everyday-links small { display: block; font-size: .5625rem; font-weight: 650; letter-spacing: .12em; opacity: .7; }
.everyday-links strong { display: block; margin-top: 5px; font-size: 1.0625rem; font-weight: 550; letter-spacing: -.02em; }
.everyday-icon { display: grid; width: 44px; height: 44px; flex: 0 0 44px; place-items: center; border-radius: 14px; background: color-mix(in srgb, currentColor 10%, transparent); }
.journal-preview { margin-top: 38px; }
.journal-preview .section-heading { align-items: center; margin-bottom: 8px; }
.journal-preview .section-heading h2 { font-size: 1.5rem; font-weight: 550; }
.journal-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 30px; }
.journal-columns article { padding: 22px 0; border-top: 1px solid var(--line); }
.preview-label { display: flex; align-items: center; gap: 8px; }
.preview-label h3 { margin: 0; font-size: .875rem; font-weight: 600; }
.journal-columns h4 { margin-bottom: 8px; font-size: 1.1rem; }
.journal-columns p { color: var(--muted); font-size: .8125rem; }
.journal-columns .source-label { font-size: .6875rem; }
.journey-path { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-block: 22px; font-size: 1.125rem; letter-spacing: -.025em; }
.journey-path strong { font-weight: 550; }
.journey-metrics { display: flex; flex-wrap: wrap; gap: 24px; margin: 0; }
.journey-metrics dt { color: var(--muted); font-size: .6875rem; }
.journey-metrics dd { margin: 5px 0 0; font-size: .875rem; }
.charge-list { list-style: none; margin: 14px 0; padding: 0; }
.charge-list a { display: flex; align-items: center; gap: 18px; min-height: 60px; }
.charge-list strong { font-size: .875rem; font-weight: 550; }
.charge-list small { display: block; color: var(--muted); font-size: .625rem; margin-top: 2px; }
.charge-bar { flex: 1; height: 4px; background: var(--line); border-radius: 2px; }
.charge-bar > span { display: block; height: 100%; background: var(--forest); border-radius: inherit; }
.maintenance-preview { display: flex; align-items: center; gap: 14px; padding: 20px 0; border-top: 1px solid var(--line); }
.maintenance-preview > span { flex: 1; }
.maintenance-preview strong, .maintenance-preview small { display: block; }
.maintenance-preview strong { font-size: .875rem; font-weight: 550; }
.maintenance-preview small { margin-top: 3px; color: var(--muted); font-size: .6875rem; }
.connection-drawer { margin-top: 24px; border: 1px solid var(--line); border-radius: 18px; padding: 4px 20px; background: var(--surface); }
.connection-drawer > summary { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; min-height: 68px; padding-block: 12px; cursor: pointer; list-style: none; }
.connection-drawer > summary::-webkit-details-marker { display: none; }
.connection-drawer > summary > span { display: flex; align-items: center; gap: 10px; font-size: .75rem; color: var(--muted); }
.connection-drawer strong { color: var(--ink); font-weight: 550; }
.connection-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 30px; padding: 16px 0; }
.context-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.context-heading h3 { font-size: .875rem; font-weight: 600; }
.vehicle-task { min-width: 0; }
.vehicle-task p, .connection-context p { color: var(--muted); font-size: .75rem; }
.connection-grid dl { margin-block: 12px; }
.connection-grid dt { color: var(--muted); font-size: .6875rem; }
.connection-grid dd { margin: 4px 0 12px; font-size: .875rem; }
.context-link, .inline-action { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; color: var(--forest); font-size: .75rem; }
.inline-action { background: none; border: 0; cursor: pointer; }
.charger-details summary { min-height: 44px; padding-block: 10px; font-size: .75rem; cursor: pointer; }
.latest-charge { border-top: 1px solid var(--line); padding-block: 12px; }
.latest-charge-values { display: flex; justify-content: space-between; gap: 12px; }
.charge-connection { display: flex; align-items: center; gap: 10px; color: var(--forest); }
.charge-connection > span { width: 70px; height: 1px; background: var(--line); }
.charge-connection > span.flow-active { background: var(--forest); }
.source-notice { border-left: 2px solid var(--accent); padding-left: 10px; }
.state-history { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 18px 0; font-size: .75rem; color: var(--muted); }
.state-history > span:last-child { display: flex; align-items: center; gap: 8px; margin-left: auto; color: var(--forest); }
.overview-footer { margin-top: 18px; color: var(--muted); font-size: .625rem; }
.readings-empty { margin: 0 0 24px; }
.connection-symbol { display: flex; align-items: center; gap: 12px; width: fit-content; margin-bottom: 22px; color: var(--premium-solid-muted); }
.connection-symbol > span { width: 36px; border-top: 1px dashed var(--premium-solid-line); }
.readings-empty h3 { max-width: 14ch; margin: 0; font-size: clamp(1.5rem, 2.4vw, 2rem); line-height: 1.15; font-weight: 580; letter-spacing: -.035em; }
.readings-empty p { max-width: 35ch; margin: 12px 0 16px; color: var(--premium-solid-muted); font-size: .8125rem; line-height: 1.6; }
.connection-next { display: inline-flex; align-items: center; gap: 16px; min-height: 44px; padding: 9px 15px; border: 1px solid var(--premium-solid-line); border-radius: 12px; color: var(--premium-solid-text); font-size: .75rem; font-weight: 600; transition: background-color 140ms ease, border-color 140ms ease; }
.connection-next:hover { background: #252e3c; border-color: #8194b5; }
@media (min-width: 961px) and (max-width: 1100px) {
 .hero-layout { grid-template-columns: minmax(0, 1.2fr) minmax(300px, 1fr); }
 .car-command-center { padding: 24px; }
}
@media (max-width: 700px) {
 .vehicle-heading { margin-bottom: 20px; }
 .vehicle-heading .eyebrow { font-size: .5rem; }
 .vehicle-heading h2 { font-size: 1.9rem; gap: 10px; margin-top: 6px; }
 .model-signature { font-size: .5rem; padding: 4px 7px; }
 .hero-layout { grid-template-columns: minmax(0, 1fr); gap: 12px; margin-bottom: 16px; }
 .car-presentation { min-height: 220px; padding: 20px 10px 8px; border-radius: 24px; }
 .car-presentation :global(.vehicle-frame) { height: 160px; }
 .stage-lettering { top: -2px; font-size: 8.2rem; }
 .stage-brand { top: 18px; left: 20px; font-size: .4375rem; }
 .car-command-center { width: 100%; padding: 20px; border-radius: 24px; }
 .reading-status { margin-bottom: 14px; font-size: .5625rem; }
 .connection-symbol { margin-bottom: 16px; }
 .readings-empty { margin-bottom: 24px; }
 .vehicle-readings { grid-template-columns: 1.3fr 1fr; margin-bottom: 24px; gap: 20px; }
 .vehicle-readings dd { font-size: 2.65rem; margin-top: 5px; }
 .vehicle-readings dt { font-size: .625rem; }
 .battery-reading { padding-left: 24px; }
 .control-actions { gap: 10px; }
 .control-circle { width: 48px; height: 48px; border-radius: 16px; }
 .control-footnote { text-align: center; margin-top: 14px; }
 .alternate-actions { margin-top: 4px; }
 .alternate-actions summary { justify-content: center; min-height: 36px; }
 .vehicle-state { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; padding: 20px; }
 .everyday-links, .journal-columns, .connection-grid { grid-template-columns: minmax(0, 1fr); }
 .everyday-links { gap: 12px; }
 .everyday-links a { padding: 20px; }
 .journal-columns { gap: 0; }
 .connection-drawer { padding-inline: 16px; }
}
</style>
