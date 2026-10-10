<script lang="ts">
	import { dateTime } from './format';
	import type { MqttStatus } from './types';

	let { mqtt, online }: { mqtt: MqttStatus | undefined; online: boolean } = $props();
	const fieldLabels = { battery_pct: 'Batería', range_km: 'Autonomía', charging: 'Estado de carga' };
	let headline = $derived(!online ? 'Sin conexión a Pulse' : !mqtt ? 'Estado pendiente' :
		mqtt.status === 'disabled' ? 'Desactivado' : mqtt.status === 'connecting' ? 'Conectando' :
		mqtt.status === 'error' ? 'Conexión interrumpida' : 'Puente conectado');
	let detail = $derived(!online ? 'Conéctate para comprobar el estado de la integración.' : !mqtt ?
		'El servidor aún no ha informado del estado.' : !mqtt.enabled ?
		'Actívalo en la configuración del servidor y elige qué campos quieres compartir con Home Assistant.' :
		mqtt.status === 'error' ? 'Pulse reintentará la conexión. Home Assistant puede mostrar las lecturas como no disponibles.' :
		mqtt.status === 'connecting' ? 'Esperando la conexión con tu sistema de casa.' :
		mqtt.data_status === 'demo' ? 'La demostración no se comparte. Esperando una lectura del vehículo.' :
		mqtt.data_status === 'published' ? 'Última lectura entregada al broker. La recepción en Home Assistant requiere su conexión.' :
		mqtt.data_status === 'unavailable' ? 'Sin una lectura válida dentro del plazo. Los sensores quedan no disponibles.' :
		'Esperando una lectura del vehículo.');
</script>

<div class="home-integration" aria-live="polite">
	<div class="integration-status"><strong>{headline}</strong><span class="badge">Solo lectura</span></div>
	<p class="integration-detail">{detail}</p>
	{#if mqtt?.enabled}
		<div class="integration-fields" aria-label="Campos compartidos">
			{#each mqtt.fields as field}<span>{fieldLabels[field]}</span>{/each}
		</div>
		<dl class="integration-meta">
			<div><dt>Antigüedad máxima</dt><dd>{mqtt.max_age_seconds / 60} min</dd></div>
			{#if mqtt.observed_at}<div><dt>Hora de la observación</dt><dd>{dateTime(mqtt.observed_at)}</dd></div>{/if}
		</dl>
		<p class="integration-limit">Se comparten lecturas guardadas, sin ubicación ni identidad. Su antigüedad no acredita el estado físico actual del coche.</p>
	{:else}
		<p class="integration-limit">Batería, autonomía y carga, solo con tu selección explícita. La integración permanece apagada por defecto.</p>
	{/if}
	<p class="integration-charging">El cálculo PVPC está disponible en Energía. Para registrar una carga automáticamente necesitamos una integración validada con tu cargador.</p>
</div>

<style>
	.integration-status { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
	.integration-status strong { font-size: 1rem; font-weight: 650; color: var(--ink); }
	.integration-detail { margin: 10px 0 0; max-width: 66ch; color: var(--muted); font-size: 1rem; line-height: 1.6; }
	.integration-fields { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
	.integration-fields span { border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px; color: var(--ink); font-size: .875rem; }
	.integration-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 18px 0 0; }
	.integration-meta dt { color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.integration-meta dd { margin: 4px 0 0; color: var(--ink); font-size: .875rem; overflow-wrap: anywhere; }
	.integration-limit, .integration-charging { margin: 14px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.6; overflow-wrap: anywhere; }
	.integration-charging { border-top: 1px solid var(--line); padding-top: 14px; }
	@media (max-width: 430px) { .integration-meta { grid-template-columns: 1fr; } }
</style>
