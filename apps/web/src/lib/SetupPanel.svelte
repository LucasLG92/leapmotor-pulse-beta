<script lang="ts">
	import type { PulseState } from './types';
	import Glyph from './Glyph.svelte';

	let { pulseState, online }: { pulseState: PulseState; online: boolean } = $props();
	let setup = $derived(pulseState.setup ?? null);

	let setupRows = $derived(setup ? [
		{
			group: 'Vehículo y carga',
			items: [
				{
					label: 'Lector del vehículo',
					enabled: setup.vehicle_reader_enabled,
					on: 'Conectar un proveedor autorizado y comprobar lecturas actuales con el coche.',
					off: 'Habilitar un lector autorizado en el servidor y después comprobar lecturas con el coche.'
				},
				{
					label: 'Integración de cargador',
					enabled: setup.charger_enabled,
					on: 'Conectar el servicio de carga y contrastar una sesión con el cargador.',
					off: 'Habilitar la integración de cargador en el servidor y después contrastar una sesión real.'
				},
				{
					label: 'Energía por intervalos',
					enabled: setup.interval_energy_enabled,
					on: 'Comprobar los intervalos recibidos frente al contador o cargador.',
					off: 'Habilitar la lectura por intervalos en el servidor y después contrastarla con el contador o cargador.'
				}
			]
		},
		{
			group: 'Móvil',
			items: [
				{
					label: 'Aplicación móvil',
					enabled: setup.native_app_enabled,
					on: 'Instalar la app en un dispositivo y comprobar el inicio de sesión y la lectura.',
					off: 'Habilitar la app móvil en el servidor y después comprobarla en un dispositivo.'
				},
				{
					label: 'Widgets nativos',
					enabled: setup.native_widgets_enabled,
					on: 'Enlazar un dispositivo y comprobar que el widget muestra una lectura permitida.',
					off: 'Habilitar los widgets en el servidor y después enlazar y comprobar un dispositivo.'
				},
				{
					label: 'Notificaciones web',
					enabled: setup.web_push_enabled,
					on: 'Suscribir un navegador y comprobar la recepción en ese dispositivo.',
					off: 'Habilitar las notificaciones web en el servidor y después comprobar la recepción en un navegador.'
				}
			]
		},
		{
			group: 'Integraciones',
			items: [
				{
					label: 'Home Assistant',
					enabled: setup.home_assistant_enabled,
					on: 'Conectar Home Assistant y comprobar que recibe un estado actualizado.',
					off: 'Habilitar la integración en el servidor y después comprobar la recepción en Home Assistant.'
				},
				{
					label: 'ABRP',
					enabled: setup.abrp_enabled,
					on: 'Conectar ABRP y comprobar el envío de una lectura válida de batería.',
					off: 'Habilitar ABRP en el servidor y después comprobar el envío de una lectura válida.'
				},
				{
					label: 'Navegación',
					enabled: setup.navigation_enabled,
					on: 'Comprobar una búsqueda y la respuesta del servicio de navegación.',
					off: 'Habilitar navegación en el servidor y después comprobar una búsqueda real.'
				}
			]
		}
	] : []);
</script>

<section class="setup-card card" aria-labelledby="setup-heading">
	<details class="setup-disclosure">
		<summary>
			<span class="setup-summary-copy"><strong id="setup-heading">Preparar Pulse</strong><span>Conexiones y próximos pasos</span></span>
			<span class="setup-chevron" aria-hidden="true"><Glyph name="chevron" size={18} /></span>
		</summary>
		{#if !online}
			<p class="setup-offline" role="status">Sin conexión. Vuelve a conectar Pulse para consultar la configuración actual y el estado de las conexiones.</p>
		{:else if !setup}
			<p class="setup-offline" role="status">La información de preparación no está disponible en esta versión del servidor.</p>
		{:else}
			<div class="setup-content">
				<p class="setup-explanation">Esta vista muestra la configuración del servidor. El funcionamiento con el coche, servicios y dispositivos requiere una comprobación real.</p>
				<div class="setup-access">
					<strong>Acceso</strong>
					<span>{setup.auth_mode === 'oidc' ? 'Inicio de sesión configurado' : 'Acceso local: configurar HTTPS e inicio de sesión antes de publicar'}</span>
				</div>
				{#each setupRows as group (group.group)}
					<section class="setup-group" aria-label={group.group}>
						<h3>{group.group}</h3>
						<ul>
							{#each group.items as item (item.label)}
								<li>
									<div class="setup-row-heading"><strong>{item.label}</strong><span>{item.enabled ? 'Habilitado' : 'Por configurar'}</span>{#if item.enabled}<small>Pendiente de comprobación</small>{/if}</div>
									<p>{item.enabled ? item.on : `Siguiente paso: ${item.off}`}</p>
								</li>
							{/each}
						</ul>
					</section>
				{/each}
				{#if setup.charger_enabled || setup.interval_energy_enabled}
					<p class="setup-note">La habilitación del cargador o de los intervalos no acredita energía real. {pulseState.settings.pricing_method === 'manual' ? 'Para nuevas cargas con precio horario, selecciona PVPC en «Método de cálculo» en estos Ajustes.' : 'El método seleccionado es PVPC; contrasta el cálculo con una carga real.'}</p>
				{/if}
				<p class="setup-note">Comandos físicos: bloqueados; validación individual pendiente. Cámaras y centinela: pendientes de estudio.</p>
			</div>
		{/if}
	</details>
</section>

<style>
	.setup-card { overflow: hidden; }
	.setup-disclosure > summary { display: flex; min-height: 70px; align-items: center; justify-content: space-between; gap: 14px; padding: 16px 19px; cursor: pointer; list-style: none; }
	.setup-disclosure > summary::-webkit-details-marker { display: none; }
	.setup-summary-copy { display: grid; gap: 3px; }
	.setup-summary-copy strong { font-size: 1rem; font-weight: 680; letter-spacing: -.02em; }
	.setup-summary-copy > span:last-child { color: var(--muted); font-size: var(--text-label); line-height: 1.45; }
	.setup-chevron { color: var(--muted); font-size: 1.4rem; transition: transform 150ms ease; }
	.setup-disclosure[open] .setup-chevron { transform: rotate(90deg); }
	.setup-content { display: grid; gap: 14px; padding: 0 19px 18px; }
	.setup-access { display: grid; gap: 3px; padding: 11px 13px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
	.setup-access strong { font-size: var(--text-label); }
	.setup-access span, .setup-group li p, .setup-note, .setup-offline, .setup-explanation { margin: 0; color: var(--muted-strong); font-size: var(--text-label); line-height: 1.5; }
	.setup-group { display: grid; gap: 6px; }
	.setup-group h3 { margin: 0; color: var(--muted); font-size: var(--text-caption); font-weight: 720; letter-spacing: .09em; text-transform: uppercase; }
	.setup-group ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
	.setup-group li { display: grid; gap: 3px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; }
	.setup-row-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 3px 10px; }
	.setup-row-heading strong { font-size: var(--text-label); }
	.setup-row-heading span { color: var(--muted); font-size: var(--text-caption); font-weight: 650; }
	.setup-row-heading small { color: var(--accent); font-size: var(--text-caption); font-weight: 650; }
	.setup-note { padding-top: 3px; }
	.setup-offline { margin: 0; padding: 0 19px 18px; }
	.setup-disclosure > summary:focus-visible { outline: 3px solid var(--focus); outline-offset: -4px; }
	@media (max-width: 700px) { .setup-disclosure > summary { padding: 15px 16px; } .setup-content { padding: 0 16px 16px; } .setup-offline { padding: 0 16px 16px; } }
</style>
