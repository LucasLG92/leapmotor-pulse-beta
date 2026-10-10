<script lang="ts">
	import { dateTime } from './format';
	import type { AbrpStatus, PageProps } from './types';

	let {
		abrp,
		mode,
		online,
		busy,
		mutate,
		confirmAction
	}: {
		abrp: AbrpStatus | undefined;
		mode: PageProps['pulseState']['mode'];
		online: boolean;
		busy: boolean;
		mutate: PageProps['mutate'];
		confirmAction: PageProps['confirmAction'];
	} = $props();

	let saving = $state(false);
	let actionError = $state('');
	let statusLabel = $derived(!online ? 'Sin conexión a Pulse' : !abrp ? 'Estado pendiente' :
		!abrp.enabled ? 'No configurado en el servidor' : abrp.reason_code === 'consent_required' ? 'Desconectado' : mode === 'demo' ? 'Pausado durante la demo' :
		abrp.status === 'sharing' ? 'Enviando una lectura' : abrp.status === 'shared' ? 'Última lectura confirmada' :
		abrp.status === 'error' ? 'Envío sin confirmar' : abrp.status === 'waiting' ? 'Esperando una lectura válida' :
		abrp.status === 'paused' ? 'Pausado' : abrp.consent ? 'Enlace activo' : 'Desconectado');
	let actionDisabled = $derived(!online || busy || saving || !abrp || !abrp.enabled || (mode === 'demo' && !abrp.consent));

	function explanation(): string {
		if (!online) return 'Conéctate para comprobar y cambiar el consentimiento.';
		if (!abrp) return 'Esperando el estado de la integración desde Pulse.';
		if (!abrp.enabled) return 'La integración debe habilitarse en el servidor. No se envían datos mientras esté apagada.';
		if (mode === 'demo' || abrp.reason_code === 'demo_excluded') return 'La demostración no se comparte. Puedes desconectar el consentimiento durante la demo y volver a decidir al salir.';
		switch (abrp.reason_code) {
			case 'consent_required': return 'Desconectado. Tú eliges si Pulse comparte las lecturas válidas.';
			case 'waiting_observation': return 'Enlace activo. Esperando una lectura nueva del vehículo para compartirla.';
			case 'observation_invalid': return 'La lectura disponible no cumple los requisitos de procedencia o formato.';
			case 'observation_stale': return 'La lectura disponible ha superado la antigüedad permitida.';
			case 'already_attempted': return 'Pulse espera una lectura que pueda compartir, respetando el intervalo entre envíos.';
			case 'sharing': return 'Pulse está enviando la lectura de batería y su hora de observación.';
			case 'shared': return 'ABRP confirmó la recepción de la última lectura.';
			case 'send_failed': return 'No se confirmó el envío. Pulse no repetirá esta misma lectura.';
			default: return abrp.consent ? 'El consentimiento está activo; Pulse compartirá lecturas que cumplan los requisitos.' : 'Desconectado por defecto. Tú eliges si Pulse comparte las lecturas válidas.';
		}
	}

	async function toggleConsent() {
		if (actionDisabled || !abrp) return;
		const enabling = !abrp.consent;
		saving = true;
		try {
			if (enabling) {
				const accepted = await confirmAction(
					'Compartir lecturas con ABRP',
					'Pulse enviará a ABRP el porcentaje de batería y la hora de observación para ayudar a planificar rutas. No se envían campos de ubicación ni de identificación del vehículo; la conexión vincula tu cuenta ABRP. Puedes desconectarlo cuando quieras.',
					'Compartir con ABRP'
				);
				if (!accepted) return;
			}

			// Recheck the live props after the dialog; demo and connection state may have changed meanwhile.
			if (!online || busy || !abrp?.enabled || abrp.consent === enabling || (enabling && mode === 'demo')) return;
			actionError = '';
			const saved = await mutate(
				'/api/integrations/abrp',
				'PUT',
				{ enabled: enabling },
				enabling ? 'Consentimiento guardado.' : 'ABRP desconectado.'
			);
			if (!saved) actionError = enabling ? 'No se pudo guardar el consentimiento. Comprueba el estado antes de volver a intentarlo.' : 'No se pudo desconectar ABRP. Comprueba el estado antes de volver a intentarlo.';
		} catch {
			actionError = enabling ? 'No se pudo guardar el consentimiento. Comprueba el estado antes de volver a intentarlo.' : 'No se pudo desconectar ABRP. Comprueba el estado antes de volver a intentarlo.';
		} finally {
			saving = false;
		}
	}
</script>

<div class="abrp-panel">
	<div class="abrp-status-row" role="status" aria-live="polite" aria-atomic="true">
		<strong>{statusLabel}</strong>
		<span class="badge" class:badge-success={abrp?.status === 'shared' && online && mode !== 'demo'} class:badge-demo={mode === 'demo'}>{abrp?.status === 'shared' && online && mode !== 'demo' ? 'Recepción confirmada' : 'Solo batería y hora'}</span>
	</div>
	<p class="abrp-explanation">{explanation()}</p>

	{#if abrp?.enabled && abrp.consent}
		<div class="abrp-fields" aria-label="Datos compartidos">
			{#each abrp.fields as field}
				<span>{field === 'battery_pct' ? 'Porcentaje de batería' : 'Hora de observación'}</span>
			{/each}
		</div>
		<dl class="abrp-meta">
			<div><dt>Antigüedad máxima</dt><dd>{Math.round(abrp.max_age_seconds / 60)} min</dd></div>
			{#if abrp.last_shared_at}<div><dt>Última confirmación de ABRP</dt><dd>{dateTime(abrp.last_shared_at)}</dd></div>{/if}
		</dl>
	{:else}
		<p class="abrp-privacy">Solo se consideran lecturas nuevas y válidas. No se envían campos de ubicación o identificación del vehículo, ni datos de demostración.</p>
	{/if}

	<div class="abrp-actions">
		{#if abrp?.consent}
			<button class="button button-secondary abrp-action" type="button" disabled={actionDisabled} onclick={toggleConsent}>
				{saving ? 'Desconectando…' : 'Desconectar ABRP'}
			</button>
		{:else}
			<button class="button button-primary abrp-action" type="button" disabled={actionDisabled} aria-describedby="abrp-consent-note" onclick={toggleConsent}>
				{saving ? 'Guardando…' : 'Conectar ABRP'}
			</button>
		{/if}
		{#if actionDisabled && !saving}
			<span class="abrp-action-hint">{!online ? 'Sin conexión' : !abrp ? 'Estado pendiente' : !abrp.enabled ? 'Configuración pendiente' : mode === 'demo' ? 'No disponible en demo' : 'Acción no disponible'}</span>
		{/if}
	</div>
	{#if !abrp?.consent}<p id="abrp-consent-note" class="abrp-privacy">La conexión está apagada por defecto. La confirmación explica el propósito y los datos antes de activar el envío.</p>{/if}
	{#if actionError}<p class="abrp-error" role="alert">{actionError}</p>{/if}
	<p class="abrp-validation">Integración manual en piloto privado; validación con cuenta y vehículo pendiente.</p>
</div>

<style>
	.abrp-status-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
	.abrp-status-row strong { color: var(--ink); font-size: 1rem; font-weight: 650; }
	.abrp-explanation { margin: 10px 0 0; max-width: 66ch; color: var(--muted); font-size: 1rem; line-height: 1.6; }
	.abrp-fields { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
	.abrp-fields span { padding: 6px 9px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); font-size: .875rem; }
	.abrp-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 16px 0 0; }
	.abrp-meta dt { color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.abrp-meta dd { margin: 4px 0 0; color: var(--ink); font-size: .875rem; overflow-wrap: anywhere; }
	.abrp-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 18px; }
	.abrp-action { min-width: 180px; min-height: 48px; }
	.abrp-action-hint, .abrp-privacy, .abrp-validation { margin: 0; color: var(--muted); font-size: .75rem; line-height: 1.6; }
	.abrp-privacy { margin-top: 13px; }
	.abrp-validation { margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--line); }
	.abrp-error { margin: 12px 0 0; color: var(--danger); font-size: .875rem; line-height: 1.5; }
	@media (max-width: 430px) { .abrp-meta { grid-template-columns: 1fr; } .abrp-action { width: 100%; } }
</style>
