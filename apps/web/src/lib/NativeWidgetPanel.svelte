<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import Glyph from './Glyph.svelte';
	import { getNativeBridge, getNativePlatform, type NativeBridgeStatus } from './native';
	import type { PageProps } from './types';

	let { online, revision, confirmAction, drafts }: {
		online: boolean;
		revision: number;
		confirmAction: (title: string, message: string, confirmLabel?: string) => Promise<boolean>;
		drafts: PageProps['drafts'];
	} = $props();
	const bridge = getNativeBridge();
	const platform = getNativePlatform();
	let status = $state<NativeBridgeStatus | null>(null);
	let loading = $state(true);
	let busy = $state(false);
	let pairingCode = $state('');
	let codeVisible = $state(false);
	let deviceName = $state('Pulse móvil');
	let deviceNameBaseline = 'Pulse móvil';
	let cacheEnabled = $state(false);
	let pinResult = $state('');
	let requestId = 0;
	let disposed = false;
	let codeInput = $state<HTMLInputElement>();
	let panelElement = $state<HTMLElement>();
	let headingElement = $state<HTMLHeadingElement>();
	let statusError = $state('');
	let actionError = $state('');
	const statusErrorMessage = 'No se pudo consultar el estado de la app. Vuelve a intentarlo.';

	const canLink = $derived(Boolean(online && status?.configured && status.authenticated && !busy && !loading && /^[-A-Za-z0-9_]{43}$/.test(pairingCode) && deviceName.trim().length > 0 && deviceName.trim().length <= 60));
	const canManageWidget = $derived(Boolean(online && status?.configured && status.authenticated && status.widgetLinked && !busy && !loading));

	onMount(() => drafts.register('native-link', () => deviceName !== deviceNameBaseline || pairingCode.length > 0));

	function capturePanelFocus(): HTMLElement | null {
		if (typeof document === 'undefined' || !panelElement) return null;
		const active = document.activeElement;
		return active instanceof HTMLElement && panelElement.contains(active) ? active : null;
	}

	async function restorePanelFocus(previous: HTMLElement | null): Promise<void> {
		await tick();
		if (!previous || disposed || !document.hasFocus() || document.activeElement !== document.body) return;
		const target = previous.isConnected && !previous.matches(':disabled') ? previous : headingElement;
		if (target?.isConnected && !target.matches(':disabled')) target.focus();
	}

	$effect(() => {
		void revision;
		if (!online) clearCode();
		void refreshStatus();
	});

	async function refreshStatus() {
		if (!bridge || disposed) return;
		const current = ++requestId;
		loading = true;
		statusError = '';
		try {
			const next = await bridge.status();
			if (disposed || current !== requestId) return;
			status = next;
			cacheEnabled = next.widgetCacheEnabled;
			if (!next.configured || !next.authenticated || !next.widgetLinked) clearCode();
		} catch {
			if (!disposed && current === requestId) {
				status = null;
				statusError = statusErrorMessage;
			}
		} finally {
			if (!disposed && current === requestId) loading = false;
		}
	}

	function clearCode() {
		pairingCode = '';
		codeVisible = false;
		if (codeInput) codeInput.value = '';
	}

	function safeError(action: 'cache' | 'clear' | 'pin'): string {
		if (!online) return 'Necesitas conexión para completar esta acción.';
		if (!status?.configured) return 'El acceso de esta app aún no está configurado.';
		if (!status.authenticated) return 'Inicia sesión en Pulse antes de continuar.';
		if (action === 'cache') return 'No se pudo guardar la preferencia de caché. Inténtalo de nuevo.';
		if (action === 'clear') return 'No se pudo desvincular el widget. Inténtalo de nuevo.';
		return 'No se pudo abrir la opción de fijado del sistema.';
	}

	async function linkWidget() {
		if (!bridge || loading || !canLink) return;
		const previousFocus = capturePanelFocus();
		busy = true;
		actionError = '';
		const linkedName = deviceName.trim();
		try {
			await bridge.linkWidget({ pairingCode, deviceName: linkedName });
			if (disposed) return;
			deviceName = linkedName;
			deviceNameBaseline = linkedName;
			clearCode();
			await refreshStatus();
		} catch {
			if (!disposed) {
				actionError = 'No se pudo confirmar el resultado del enlace. Revisa los dispositivos enlazados en Pulse antes de crear un código nuevo.';
				clearCode();
				await refreshStatus();
			}
		} finally {
			if (!disposed) {
				clearCode();
				busy = false;
				await restorePanelFocus(previousFocus);
			}
		}
	}

	async function changeCache(event: Event) {
		if (!bridge || loading || !canManageWidget) return;
		const previousFocus = capturePanelFocus();
		const enabled = (event.currentTarget as HTMLInputElement).checked;
		busy = true;
		actionError = '';
		try {
			await bridge.setWidgetCache({ enabled });
			if (disposed) return;
			cacheEnabled = enabled;
			await refreshStatus();
		} catch {
			if (!disposed) {
				cacheEnabled = status?.widgetCacheEnabled ?? false;
				actionError = safeError('cache');
			}
		} finally {
			if (!disposed) {
				busy = false;
				await restorePanelFocus(previousFocus);
			}
		}
	}

	async function clearWidget() {
		if (!bridge || loading || !canManageWidget) return;
		const previousFocus = capturePanelFocus();
		const confirmed = await confirmAction('Desvincular widget', 'Se eliminarán el permiso de acceso y la copia guardada en este dispositivo.', 'Desvincular');
		if (!confirmed || disposed || loading || !canManageWidget) return;
		busy = true;
		actionError = '';
		try {
			await bridge.clearWidget();
			if (disposed) return;
			cacheEnabled = false;
			await refreshStatus();
		} catch {
			if (!disposed) actionError = safeError('clear');
		} finally {
			if (!disposed) {
				busy = false;
				await restorePanelFocus(previousFocus);
			}
		}
	}

	async function pinWidget() {
		if (!bridge || loading || !canManageWidget) return;
		const previousFocus = capturePanelFocus();
		busy = true;
		pinResult = '';
		actionError = '';
		try {
			const result = await bridge.pinWidget();
			if (disposed) return;
			pinResult = result.supported
				? 'El sistema te mostrará cómo añadir el widget.'
				: platform === 'ios'
					? 'Mantén pulsado un espacio de la pantalla de inicio, abre la opción para añadir widgets y busca Pulse. Elige el tamaño y añádelo.'
					: 'Mantén pulsado un espacio de la pantalla de inicio, abre Widgets y busca Pulse para añadirlo.';
		} catch {
			if (!disposed) actionError = safeError('pin');
		} finally {
			if (!disposed) {
				busy = false;
				await restorePanelFocus(previousFocus);
			}
		}
	}

	onDestroy(() => {
		disposed = true;
		requestId += 1;
		clearCode();
	});
</script>

{#if bridge}
	<section bind:this={panelElement} class="native-widget-panel" aria-labelledby="native-widget-heading">
		<div class="native-widget-heading"><div><p class="section-label">APP NATIVA</p><h3 bind:this={headingElement} id="native-widget-heading" tabindex="-1">Widget de Pulse</h3></div><span class="badge">{platform === 'ios' ? 'iPhone' : platform === 'android' ? 'Android' : 'Este dispositivo'}</span></div>
		<p class="native-widget-copy">Enlaza el widget de este móvil y decide si quieres guardar una vista mínima para consultarla sin conexión.</p>
		{#if loading}<p class="native-widget-status" role="status">Consultando el estado de la app…</p>{/if}
		{#if status && !status.configured}<p class="native-widget-status" role="status">El acceso de esta app aún no está configurado.</p>{/if}
		{#if status && !status.authenticated}<p class="native-widget-status" role="status">Inicia sesión para gestionar el widget.</p>{/if}
		{#if status?.widgetLinked}
			<p class="native-widget-linked" role="status"><span class="status-dot"></span> Widget enlazado a este dispositivo</p>
			<label class="native-cache-option"><input type="checkbox" checked={cacheEnabled} disabled={!canManageWidget} onchange={changeCache} /><span><strong>Guardar una copia en este móvil</strong><small>Desactivada por defecto. Solo conserva los campos elegidos durante un máximo de 2 horas; nunca ubicación, identidad ni credenciales. El sistema puede mantener la última vista hasta refrescar el widget.</small></span></label>
			<div class="native-widget-actions">
				<button class="button button-secondary button-small" type="button" disabled={!canManageWidget} onclick={pinWidget}><Glyph name="overview" size={15} /> {platform === 'ios' ? 'Cómo añadirlo' : 'Añadir widget'}</button>
				<button class="button button-quiet button-small" type="button" disabled={!canManageWidget} onclick={clearWidget}>Desvincular</button>
			</div>
			{#if pinResult}<p class="native-widget-status" role="status">{pinResult}</p>{/if}
		{:else if status?.configured && status.authenticated}
			<form class="native-widget-link-form" onsubmit={(event) => { event.preventDefault(); void linkWidget(); }}>
				<label for="native-pairing-code">Código de enlace</label>
				<div class="native-code-input"><input bind:this={codeInput} bind:value={pairingCode} id="native-pairing-code" type={codeVisible ? 'text' : 'password'} inputmode="text" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="43" aria-describedby="native-code-help" /><button class="button button-quiet button-small" type="button" aria-pressed={codeVisible} onclick={() => { codeVisible = !codeVisible; }}> {codeVisible ? 'Ocultar' : 'Mostrar'}</button></div>
				<p id="native-code-help" class="native-widget-status">El código se borra al enlazar, al perder la conexión o al salir de Ajustes.</p>
				<label for="native-device-name">Nombre de este dispositivo</label>
				<input id="native-device-name" bind:value={deviceName} maxlength="60" autocomplete="off" disabled={busy} />
				<button class="button button-primary button-block" type="submit" disabled={!canLink}>{busy ? 'Enlazando…' : 'Enlazar widget'}</button>
			</form>
		{/if}
		{#if !online}<p class="native-widget-status" role="status">Sin conexión. No se pueden enlazar ni cambiar las preferencias del widget.</p>{/if}
		{#if statusError}
			<p class="native-widget-error" role="alert">{statusError}</p>
			<div class="native-widget-actions">
				<button class="button button-secondary button-small" type="button" disabled={!online || loading || busy} onclick={() => void refreshStatus()}>Reintentar consulta</button>
			</div>
		{/if}
		{#if actionError}<p class="native-widget-error" role="alert">{actionError}</p>{/if}
	</section>
{/if}

<style>
	.native-widget-panel { display: grid; gap: 11px; padding-top: 16px; border-top: 1px solid var(--line); }
	.native-widget-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 12px; }
	.native-widget-heading h3 { margin: 4px 0 0; font-size: 1.125rem; }
	.native-widget-copy, .native-widget-status { margin: 0; color: var(--muted); font-size: 1rem; line-height: 1.55; overflow-wrap: anywhere; }
	.native-widget-linked { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0; color: var(--ink); font-size: .875rem; font-weight: 620; }
	.native-cache-option { display: flex; align-items: flex-start; gap: 10px; min-height: 44px; padding: 10px 0; border-block: 1px solid var(--line); cursor: pointer; }
	.native-cache-option input { width: 24px; height: 24px; flex: 0 0 24px; margin: 0; accent-color: var(--forest); }
	.native-cache-option span { display: grid; gap: 4px; }
	.native-cache-option strong { font-size: .875rem; }
	.native-cache-option small { color: var(--muted); font-size: .75rem; line-height: 1.5; overflow-wrap: anywhere; }
	.native-widget-actions { display: flex; flex-wrap: wrap; gap: 8px; }
	.native-widget-link-form { display: grid; gap: 8px; }
	.native-widget-link-form label { color: var(--ink); font-size: .875rem; font-weight: 600; }
	.native-widget-link-form input { min-width: 0; width: 100%; min-height: 44px; padding: 0 10px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface); color: var(--ink); font: inherit; font-size: 1rem; }
	.native-code-input { display: flex; align-items: center; gap: 6px; }
	.native-code-input input { flex: 1; letter-spacing: .08em; }
	.native-widget-actions :global(.button), .native-code-input :global(.button) { min-height: 44px; font-size: .875rem; }
	.native-widget-error { margin: 0; color: var(--danger, #a33131); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
</style>
