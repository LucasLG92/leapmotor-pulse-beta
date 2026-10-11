/// <reference lib="webworker" />

import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import { CacheFirst, NetworkOnly } from 'workbox-strategies';
import { vehicleColors, type VehicleColorId } from './lib/vehicle-colors';
import {
	createVehicleArtworkRequest,
	isCacheableVehicleArtworkResponse,
	singleFlightVehicleArtwork,
	VEHICLE_ARTWORK_CACHE,
	vehicleArtworkRequestUrl
} from './lib/vehicle-artwork-cache';

declare const self: ServiceWorkerGlobalScope & {
	__WB_MANIFEST: Array<{ url: string; revision: string | null }>;
};

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

const artworkUrlById = new Map<VehicleColorId, string>(vehicleColors.map((artwork) => [
	artwork.id,
	new URL(artwork.image, self.location.origin).href
]));
const artworkUrls = new Set(artworkUrlById.values());
const artworkInFlight = new Map<string, { response: Promise<Response>; done: Promise<unknown> }>();
const artworkCache = new CacheFirst({
	cacheName: VEHICLE_ARTWORK_CACHE,
	plugins: [{
		cacheWillUpdate: async ({ request, response }) =>
			isCacheableVehicleArtworkResponse(response, request.url) ? response : null,
		cachedResponseWillBeUsed: async ({ cachedResponse, request }) =>
			isCacheableVehicleArtworkResponse(cachedResponse, request.url) ? cachedResponse : null
	}]
});

function handleVehicleArtwork(request: Request, event: ExtendableEvent): Promise<Response> {
	const url = vehicleArtworkRequestUrl(request, artworkUrls, self.location.origin);
	if (!url) return Promise.reject(new TypeError('Invalid vehicle artwork request'));
	return singleFlightVehicleArtwork(artworkInFlight, url, () => {
		const [response, done] = artworkCache.handleAll({ event, request: createVehicleArtworkRequest(url) });
		return { response, done };
	}).then((response) => response.clone());
}

async function removeOldVehicleArtwork(): Promise<void> {
	if (!(await caches.keys()).includes(VEHICLE_ARTWORK_CACHE)) return;
	const cache = await caches.open(VEHICLE_ARTWORK_CACHE);
	await Promise.all((await cache.keys())
		.filter((request) => !artworkUrls.has(request.url))
		.map((request) => cache.delete(request)));
}

function isSameOriginWindow(source: Client | ServiceWorker | MessagePort | null): source is Client {
	if (!source || !('url' in source) || source.type !== 'window') return false;
	try {
		return new URL(source.url).origin === self.location.origin;
	} catch {
		return false;
	}
}

function isVehicleColorId(value: unknown): value is VehicleColorId {
	return typeof value === 'string' && artworkUrlById.has(value as VehicleColorId);
}

registerRoute(
	({ request }) => vehicleArtworkRequestUrl(request, artworkUrls, self.location.origin) !== null,
	({ request, event }) => handleVehicleArtwork(request, event),
	'GET'
);

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
	event.waitUntil((async () => {
		await removeOldVehicleArtwork();
		await self.clients.claim();
	})());
});

self.addEventListener('message', (event: ExtendableMessageEvent) => {
	if (event.data?.type === 'PULSE_SKIP_WAITING') {
		event.waitUntil(self.skipWaiting());
		return;
	}
	if (event.data?.type !== 'PULSE_WARM_VEHICLE_ARTWORK' || !isSameOriginWindow(event.source)) return;
	const id: unknown = event.data.id;
	if (!isVehicleColorId(id)) return;
	const url = artworkUrlById.get(id);
	if (!url) return;
	event.waitUntil(handleVehicleArtwork(createVehicleArtworkRequest(url), event));
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
