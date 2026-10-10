/// <reference lib="webworker" />

import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import { NetworkOnly } from 'workbox-strategies';

declare const self: ServiceWorkerGlobalScope & {
	__WB_MANIFEST: Array<{ url: string; revision: string | null }>;
};

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

registerRoute(
	// SSE pasa directamente a la red: su respuesta abierta no debe retener este servicio.
	({ url }) => url.origin === self.location.origin && url.pathname.startsWith('/api/') && url.pathname !== '/api/events',
	new NetworkOnly()
);

const offlineShell = createHandlerBoundToURL('/index.html');
registerRoute(new NavigationRoute(offlineShell, {
	allowlist: [/^\/$/, /^\/(energy|navigation|activity|protection|settings)\/?$/]
}));

self.addEventListener('activate', (event: ExtendableEvent) => {
	event.waitUntil(self.clients.claim());
});

self.addEventListener('message', (event: ExtendableMessageEvent) => {
	if (event.data?.type === 'PULSE_SKIP_WAITING') event.waitUntil(self.skipWaiting());
});

const pushNotice = {
	title: 'Leapmotor Pulse',
	generic: { body: 'Hay una novedad disponible en Pulse.', tag: 'pulse-generic', path: '/' },
	test: { body: 'Aviso de prueba de Pulse. Comprueba este dispositivo.', tag: 'pulse-test', path: '/settings' },
	charging: { body: 'Hay una observación de carga disponible.', tag: 'pulse-charging-observation', path: '/energy' }
} as const;

self.addEventListener('push', (event: PushEvent) => {
	event.waitUntil((async () => {
		let notice: typeof pushNotice.generic | typeof pushNotice.test | typeof pushNotice.charging = pushNotice.generic;
		try {
			const data = event.data;
			if (data) {
				const bytes = data.arrayBuffer();
				if (bytes.byteLength <= 4096) {
					const payload: unknown = JSON.parse(new TextDecoder().decode(bytes));
					if (payload && typeof payload === 'object' && 'version' in payload && payload.version === 1 && 'kind' in payload) {
						if (payload.kind === 'test') notice = pushNotice.test;
						else if (payload.kind === 'charging_observation') notice = pushNotice.charging;
					}
				}
			}
		} catch {
			// Invalid payloads still produce a generic user-visible notice.
		}
		await self.registration.showNotification(pushNotice.title, {
			body: notice.body,
			icon: '/icon-192.png',
			badge: '/icon-192.png',
			tag: notice.tag,
			data: { path: notice.path }
		});
	})());
});

self.addEventListener('notificationclick', (event: NotificationEvent) => {
	event.notification.close();
	event.waitUntil((async () => {
		const rawPath = event.notification.data && typeof event.notification.data === 'object'
			? event.notification.data.path : null;
		const path = rawPath === '/settings' || rawPath === '/energy' ? rawPath : '/';
		const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
		const existing = windows.find((client) => {
			try { return new URL(client.url).origin === self.location.origin; }
			catch { return false; }
		});
		if (existing) {
			await existing.focus();
			return;
		}
		await self.clients.openWindow(new URL(path, self.location.origin).href);
	})());
});
