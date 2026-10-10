<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError } from './api';
	import type { PageProps, PushCapabilities, PushDevice, WebPushSubscriptionInput } from './types';

	let { online, revision, busy, native, notifications, runNotificationAction, confirmAction, drafts }: {
		online: boolean;
		revision: number;
		busy: boolean;
		native: boolean;
		notifications: PageProps['notifications'];
		runNotificationAction: PageProps['runNotificationAction'];
		confirmAction: PageProps['confirmAction'];
		drafts: PageProps['drafts'];
	} = $props();
	let capabilities = $state<PushCapabilities | null>(null);
	let devices = $state<PushDevice[]>([]);
	let maxDevices = $state(8);
	let loading = $state(false);
	let loaded = $state(false);
	let error = $state('');
	let actionMessage = $state('');
	let actionError = $state('');
	let actionBusy = $state(false);
	let deviceName = $state('Este navegador');
	let deviceNameBaseline = 'Este navegador';
	let permission: NotificationPermission | 'unsupported' = $state('unsupported');
	let secureContext = $state(false);
	let supported = $state(false);
	let currentEndpointHash: string | null = $state(null);
	let disposed = false;
	let requestId = 0;

	const currentDevice = $derived(devices.find((device) => device.endpoint_hash === currentEndpointHash) ?? null);
	function isPushDeviceDirty(): boolean {
		return deviceName !== deviceNameBaseline;
	}

	onMount(() => drafts.register('push-device', isPushDeviceDirty));

	$effect(() => {
		const serverDeviceName = currentDevice?.name;
		if (!serverDeviceName || isPushDeviceDirty()) return;
		deviceName = serverDeviceName;
		deviceNameBaseline = serverDeviceName;
	});

	const currentSubscriptionNeedsRenewal = $derived(Boolean(
		capabilities?.enabled && currentEndpointHash && capabilities.application_server_key &&
		!localSubscriptionMatchesKey()
	));
	function permissionDenied(): boolean {
		const current: NotificationPermission | 'unsupported' = permission;
		return current === 'denied';
	}

	const canActivate = $derived(Boolean(online && !busy && !actionBusy && !loading && capabilities?.enabled && supported && !permissionDenied() && deviceName.trim().length > 0 && deviceName.trim().length <= 60 && !currentSubscriptionNeedsRenewal && (devices.length < maxDevices || currentDevice)));

	function detectSupport() {
		secureContext = typeof window !== 'undefined' && window.isSecureContext;
		supported = secureContext && typeof Notification !== 'undefined' && 'PushManager' in window && 'serviceWorker' in navigator;
		permission = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
	}

	function decodeBase64Url(value: string): Uint8Array {
		const base64 = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
		const decoded = atob(base64);
		return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
	}

	function localSubscriptionMatchesKey(): boolean {
		if (!supported) return false;
		// Only called after a local subscription has been found and hashed.
		return localKeyMatches;
	}

	let localKeyMatches = $state(false);

	async function endpointDigest(endpoint: string): Promise<string> {
		const bytes = new TextEncoder().encode(endpoint);
		const digest = await crypto.subtle.digest('SHA-256', bytes);
		return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
	}

	async function inspectLocalSubscription(publicKey: string | null, request: number) {
		currentEndpointHash = null;
		localKeyMatches = false;
		if (!supported) return;
		try {
			const registration = await navigator.serviceWorker.getRegistration();
			const subscription = await registration?.pushManager.getSubscription();
			if (!subscription || disposed || request !== requestId) return;
			const endpointHash = await endpointDigest(subscription.endpoint);
			if (disposed || request !== requestId) return;
			currentEndpointHash = endpointHash;
			const localKey = subscription.options.applicationServerKey;
			if (publicKey) {
				const expected = decodeBase64Url(publicKey);
				localKeyMatches = localKey !== null && equalBytes(new Uint8Array(localKey), expected);
			}
		} catch {
			// A local browser API failure does not reveal subscription material.
		}
	}

	function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
		return left.length === right.length && left.every((byte, index) => byte === right[index]);
	}

	async function loadData(force = false) {
		if (disposed || (!force && (busy || actionBusy)) || loading) return;
		if (!online) {
			error = 'Conéctate para consultar la disponibilidad y los dispositivos registrados.';
			return;
		}
		const request = ++requestId;
		loading = true;
		error = '';
		try {
			const nextCapabilities = await notifications.capabilities();
			const nextDevices = await notifications.devices();
			if (disposed || request !== requestId) return;
			capabilities = nextCapabilities;
			devices = nextDevices.items;
			maxDevices = nextDevices.max_devices;
			loaded = true;
			await inspectLocalSubscription(nextCapabilities.application_server_key, request);
		} catch {
			if (!disposed && request === requestId) error = 'No se pudo consultar la disponibilidad o la lista de dispositivos. Inténtalo de nuevo.';
		} finally {
			if (!disposed && request === requestId) loading = false;
		}
	}

	$effect(() => {
		void revision;
		void online;
		void busy;
		detectSupport();
		if (!native && !busy) queueMicrotask(() => void loadData());
	});

	function fixedActionError(error: unknown, fallback: string): string {
		if (error instanceof DOMException && error.name === 'NotAllowedError') return 'El navegador no concedió permiso para las notificaciones.';
		return fallback;
	}

	function canReconcile(error: unknown): boolean {
		return !(error instanceof ApiError && error.status === 401);
	}

	function subscriptionInput(subscription: PushSubscription): WebPushSubscriptionInput | null {
		const p256dh = subscription.getKey('p256dh');
		const auth = subscription.getKey('auth');
		if (!p256dh || !auth) return null;
		const base64 = (value: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(value)))
			.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
		return {
			endpoint: subscription.endpoint,
			expirationTime: subscription.expirationTime,
			keys: { p256dh: base64(p256dh), auth: base64(auth) }
		};
	}

	async function getOrCreateSubscription(forceReplace = false): Promise<PushSubscription> {
		const registration = await navigator.serviceWorker.getRegistration();
		if (!registration) throw new Error('No se encontró el service worker de Pulse. Recarga la aplicación e inténtalo de nuevo.');
		let subscription = await registration.pushManager.getSubscription();
		const key = capabilities?.application_server_key;
		if (!key) throw new Error('El servidor no tiene disponible una clave de notificaciones.');
		if (subscription && !forceReplace) {
			const localKey = subscription.options.applicationServerKey;
			if (!localKey || !equalBytes(new Uint8Array(localKey), decodeBase64Url(key))) {
				throw new Error('La clave de notificaciones cambió. Usa «Renovar suscripción» para actualizar este navegador.');
			}
			return subscription;
		}
		if (subscription && forceReplace) {
			const oldHash = await endpointDigest(subscription.endpoint);
			const serverDevice = devices.find((device) => device.endpoint_hash === oldHash);
			if (serverDevice) await notifications.revoke(serverDevice.id);
			await subscription.unsubscribe();
			subscription = null;
		}
		const decodedKey = decodeBase64Url(key);
		const applicationServerKey = Uint8Array.from(decodedKey).buffer as ArrayBuffer;
		return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
	}

	async function registerCurrentSubscription(subscription: PushSubscription) {
		const input = subscriptionInput(subscription);
		if (!input) throw new Error('El navegador no pudo preparar una suscripción válida.');
		const submittedName = deviceName;
		const registeredName = submittedName.trim();
		await notifications.register(registeredName, input);
		if (disposed) return;
		if (deviceName === submittedName) deviceName = registeredName;
		deviceNameBaseline = registeredName;
		await loadData(true);
		if (disposed) return;
		permission = Notification.permission;
		actionMessage = 'Notificaciones activadas para este navegador.';
	}

	async function activate() {
		if (!canActivate || !deviceName.trim() || deviceName.trim().length > 60) return;
		actionMessage = '';
		actionError = '';
		actionBusy = true;
		try {
			await runNotificationAction(async () => {
				// Keep the permission prompt as the first awaited browser operation in this click handler.
				if (Notification.permission === 'default') {
					const nextPermission = await Notification.requestPermission();
					if (disposed) return;
					permission = nextPermission;
				}
				if (disposed || permission !== 'granted') return;
				try {
					const subscription = await getOrCreateSubscription();
					if (disposed) return;
					await registerCurrentSubscription(subscription);
				} catch (error) {
					// A failed response can leave a valid local subscription. Reconcile the server list without retrying the write.
					if (canReconcile(error)) try { await loadData(true); } catch { /* The stable message below is sufficient. */ }
					throw error;
				}
			});
			if (disposed) return;
			if (permission === 'denied') actionError = 'Las notificaciones están bloqueadas en el navegador. Puedes permitirlas en los ajustes del sitio.';
		} catch (error) {
			if (!disposed) actionError = fixedActionError(error, 'No se pudo activar esta suscripción. Comprueba la lista antes de volver a intentarlo.');
		} finally {
			if (!disposed) actionBusy = false;
		}
	}

	async function renewSubscription() {
		if (busy || actionBusy || !online || !deviceName.trim() || deviceName.trim().length > 60 || !capabilities?.enabled || permission === 'denied') return;
		actionMessage = '';
		actionError = '';
		actionBusy = true;
		try {
			await runNotificationAction(async () => {
				if (Notification.permission === 'default') {
					const nextPermission = await Notification.requestPermission();
					if (disposed) return;
					permission = nextPermission;
				}
				if (disposed || permission !== 'granted') return;
				const confirmed = await confirmAction(
					'Renovar notificaciones',
					'Pulse revocará la suscripción anterior, la quitará de este navegador y creará una nueva con la clave actual.',
					'Renovar suscripción'
				);
				if (!confirmed || disposed) return;
				const subscription = await getOrCreateSubscription(true);
				if (disposed) return;
				try {
					await registerCurrentSubscription(subscription);
				} catch (error) {
					if (canReconcile(error)) try { await loadData(true); } catch { /* Do not retry registration automatically. */ }
					throw error;
				}
			});
			if (disposed) return;
		} catch (error) {
			if (!disposed) actionError = fixedActionError(error, 'No se pudo renovar esta suscripción. Comprueba la lista antes de volver a intentarlo.');
		} finally { if (!disposed) actionBusy = false; }
	}

	async function revokeDevice(device: PushDevice) {
		if (busy || actionBusy || !online) return;
		actionMessage = '';
		actionError = '';
		actionBusy = true;
		try {
			await runNotificationAction(async () => {
				const confirmed = await confirmAction('Desactivar notificaciones', `Se desactivarán los avisos en «${device.name}». El permiso del navegador seguirá activo.`, 'Desactivar');
				if (!confirmed || disposed) return;
				await notifications.revoke(device.id);
				if (disposed) return;
				if (device.endpoint_hash === currentEndpointHash && supported) {
					const registration = await navigator.serviceWorker.getRegistration();
					if (disposed) return;
					const subscription = await registration?.pushManager.getSubscription();
					if (disposed) return;
					if (subscription && await endpointDigest(subscription.endpoint) === device.endpoint_hash) {
						if (disposed) return;
						await subscription.unsubscribe();
					}
					if (disposed) return;
					currentEndpointHash = null;
					localKeyMatches = false;
				}
				await loadData(true);
				if (disposed) return;
				actionMessage = 'Suscripción desactivada. El permiso del navegador sigue activo.';
			});
			if (disposed) return;
		} catch (error) {
			if (canReconcile(error)) try { await loadData(true); } catch { /* A fixed error remains visible if reconciliation also fails. */ }
			if (!disposed) actionError = fixedActionError(error, 'No se pudo desactivar este dispositivo. Inténtalo de nuevo.');
		} finally { if (!disposed) actionBusy = false; }
	}

	async function sendTest(device: PushDevice) {
		if (busy || actionBusy || !online || device.state !== 'active') return;
		actionMessage = '';
		actionError = '';
		actionBusy = true;
		try {
			await runNotificationAction(async () => {
				const confirmed = await confirmAction('Enviar prueba', `Pulse enviará un aviso de prueba a «${device.name}».`, 'Enviar prueba');
				if (!confirmed || disposed) return;
				await notifications.test(device.id);
				if (disposed) return;
				actionMessage = 'Pulse aceptó la solicitud de prueba. Comprueba el dispositivo.';
				await loadData(true);
			});
			if (disposed) return;
		} catch (error) {
			if (canReconcile(error)) try { await loadData(true); } catch { /* Includes the backend's invalid-destination cleanup. */ }
			if (!disposed) actionError = error instanceof ApiError && error.status === 429
				? 'Las pruebas están limitadas a una por minuto. Espera antes de enviar otra.'
				: fixedActionError(error, 'No se pudo enviar la prueba. El servidor no confirma la entrega física.');
		} finally { if (!disposed) actionBusy = false; }
	}

	function deviceState(device: PushDevice): string {
		if (device.state === 'session_required') return 'Reactivar con esta sesión';
		if (device.last_result === 'accepted') return 'Último aviso aceptado por el servicio';
		if (device.last_result === 'pending') return 'Último aviso pendiente';
		if (device.last_result === 'failed') return 'Último aviso fallido';
		if (device.last_result === 'uncertain') return 'Resultado del último aviso incierto';
		return 'Activa';
	}

	function expiryLabel(value: string | null): string {
		if (!value) return 'Caducidad no informada';
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? 'Caducidad no disponible' : `Caduca ${new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }).format(date)}`;
	}

	onDestroy(() => { disposed = true; requestId += 1; });
</script>

{#if native}
	<section class="push-panel" aria-labelledby="push-heading">
		<div class="push-heading"><div><h3 id="push-heading">Avisos web</h3></div></div>
		<p class="push-copy">Las notificaciones Web Push no están disponibles dentro de la app nativa.</p>
	</section>
{:else}
	<section class="push-panel" aria-labelledby="push-heading">
		<div class="push-heading"><div><h3 id="push-heading">Avisos de Pulse</h3></div>{#if loaded}<span class="push-limit">{devices.length} / {maxDevices}</span>{/if}</div>
		<p class="push-copy">Activa avisos genéricos en esta PWA. Pulse no incluye datos del vehículo en las notificaciones. Desactivar una suscripción no cambia el permiso del navegador.</p>
		<p class="push-note">Los envíos dependen de la sesión que autorizó cada dispositivo: dura 8 horas por defecto y nunca más de 24. Al caducar, los envíos se detienen; abrir Ajustes no renueva la sesión. Reactiva el dispositivo manualmente.</p>
		{#if !secureContext && supported === false}<p class="push-note" role="status">El navegador requiere una conexión segura para activar avisos.</p>{/if}
		{#if !supported && secureContext}<p class="push-note" role="status">Este navegador no ofrece Web Push y notificaciones compatibles.</p>{/if}
		{#if permission === 'denied'}<p class="push-note" role="status">Las notificaciones están bloqueadas en el navegador. Puedes cambiar el permiso en los ajustes del sitio.</p>{/if}
		{#if capabilities && !capabilities.enabled}<p class="push-note" role="status">El servicio de notificaciones no está habilitado en Pulse.</p>{/if}
		{#if !online}<p class="push-note" role="status">Sin conexión. No se pueden consultar ni cambiar las suscripciones.</p>{/if}
		{#if loading && !loaded}<p class="push-loading" role="status">Consultando el servicio…</p>{/if}
		{#if error}<div class="push-error" role="alert"><span>{error}</span><button class="button button-small button-secondary" type="button" disabled={!online || busy || actionBusy || loading} onclick={() => void loadData()}>Reintentar</button></div>{/if}
		{#if !error && supported && capabilities?.enabled && permission !== 'denied'}
			<div class="push-activation">
				{#if currentDevice?.state !== 'active'}<div class="push-name-field"><label for="push-device-name">Nombre de este dispositivo</label><input id="push-device-name" bind:value={deviceName} maxlength="60" autocomplete="off" aria-describedby="push-name-hint" disabled={busy || actionBusy} /><span id="push-name-hint">Máximo 60 caracteres. Solo se usa para reconocerlo aquí.</span></div>{/if}
				{#if currentSubscriptionNeedsRenewal}
					<p class="push-note" role="status">La clave pública cambió. Renueva la suscripción para seguir usando este navegador.</p>
					<button class="button button-secondary button-block" type="button" disabled={busy || actionBusy || !online || !deviceName.trim() || deviceName.trim().length > 60} onclick={() => void renewSubscription()}>{actionBusy ? 'Renovando…' : 'Renovar suscripción'}</button>
				{:else if currentDevice?.state === 'session_required'}
					<p class="push-note" role="status">La sesión que autorizó los avisos ha caducado. Reactiva este navegador de forma explícita.</p>
					<button class="button button-secondary button-block" type="button" disabled={!canActivate} onclick={() => void activate()}>{actionBusy ? 'Reactivando…' : 'Reactivar notificaciones'}</button>
				{:else if currentDevice?.state === 'active'}
					<p class="push-note" role="status">Las notificaciones ya están activas en este navegador.</p>
				{:else}
					<button class="button button-primary button-block" type="button" disabled={!canActivate} onclick={() => void activate()}>{actionBusy ? 'Activando…' : 'Activar en este navegador'}</button>
				{/if}
				{#if devices.length >= maxDevices && !currentDevice}<p class="push-note" role="status">Has alcanzado el límite de dispositivos. Desactiva uno para añadir otro.</p>{/if}
			</div>
		{/if}
		{#if devices.length}
			<ul class="push-device-list" aria-label="Dispositivos con notificaciones">
				{#each devices as device (device.id)}
					<li class="push-device-row">
						<div class="push-device-info"><strong>{device.name}</strong><span>{expiryLabel(device.expires_at)}</span><span>{deviceState(device)}</span></div>
						<div class="push-device-actions">
							{#if device.state === 'active'}<button class="button button-small button-secondary" type="button" disabled={!online || busy || actionBusy} onclick={() => void sendTest(device)}>{actionBusy ? 'Enviando…' : 'Enviar prueba'}</button>{/if}
							<button class="button button-small button-quiet push-revoke" type="button" disabled={!online || busy || actionBusy} onclick={() => void revokeDevice(device)}>Desactivar</button>
						</div>
					</li>
				{/each}
			</ul>
		{:else if loaded}
			<p class="push-empty">Aún no hay dispositivos con avisos activados.</p>
		{/if}
		{#if actionError}<p class="push-error-text" role="alert">{actionError}</p>{/if}
		{#if actionMessage}<p class="push-success" role="status" aria-live="polite">{actionMessage}</p>{/if}
		<p class="push-footnote">Puedes enviar una prueba por minuto. Una prueba aceptada por el servicio no confirma su entrega. Tras activarlo, puedes retirar cada dispositivo desde aquí; el permiso del navegador se gestiona por separado.</p>
	</section>
{/if}

<style>
	.push-panel { display: grid; gap: 11px; margin-top: 15px; padding-top: 16px; border-top: 1px solid var(--line); }
	.push-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
	.push-heading { flex-wrap: wrap; }
	.push-heading h3 { margin: 4px 0 0; font-size: 1.125rem; font-weight: 650; letter-spacing: -.02em; }
	.push-limit { padding: 5px 8px; border-radius: 999px; background: var(--surface); color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.push-copy, .push-note, .push-empty, .push-footnote, .push-loading { margin: 0; color: var(--muted); font-size: 1rem; line-height: 1.55; overflow-wrap: anywhere; }
	.push-activation { display: grid; gap: 9px; }
	.push-name-field { display: grid; gap: 5px; }
	.push-name-field label { color: var(--ink); font-size: .875rem; font-weight: 600; }
	.push-name-field input { min-height: 44px; width: 100%; min-width: 0; padding: 0 10px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface); color: var(--ink); font: inherit; font-size: 1rem; }
	.push-name-field span { color: var(--muted); font-size: .75rem; line-height: 1.45; overflow-wrap: anywhere; }
	.push-device-list { display: grid; gap: 7px; margin: 1px 0 0; padding: 0; list-style: none; }
	.push-device-row { display: grid; gap: 7px; min-width: 0; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-soft); }
	.push-device-info { display: grid; gap: 3px; min-width: 0; color: var(--muted); font-size: .75rem; line-height: 1.45; overflow-wrap: anywhere; }
	.push-device-info strong { overflow-wrap: anywhere; color: var(--ink); font-size: 1rem; font-weight: 640; }
	.push-device-actions { display: flex; flex-wrap: wrap; gap: 5px; }
	.push-panel :global(.button) { min-height: 44px; font-size: .875rem; }
	.push-revoke { color: var(--danger); }
	.push-error { display: grid; gap: 8px; padding: 10px; border: 1px solid color-mix(in srgb, var(--danger) 24%, var(--line)); border-radius: 9px; background: color-mix(in srgb, var(--danger) 6%, var(--surface)); color: var(--danger); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.push-error .button { justify-self: start; }
	.push-error-text { margin: 0; color: var(--danger); font-size: 1rem; line-height: 1.5; overflow-wrap: anywhere; }
	.push-success { margin: 0; color: var(--forest); font-size: 1rem; line-height: 1.5; }
	.push-footnote { padding-top: 8px; border-top: 1px solid var(--line); font-size: .75rem; }
	.push-panel :global(.button) { transition: background-color .12s ease, color .12s ease, border-color .12s ease, opacity .12s ease; }
	@media (max-width: 430px) { .push-device-actions { display: grid; grid-template-columns: 1fr; } .push-device-actions .button { width: 100%; } }
	@media (prefers-reduced-motion: reduce) { .push-panel :global(.button) { transition: none; } }
</style>
