import test from 'node:test';
import assert from 'node:assert/strict';
import {
	createVehicleArtworkRequest,
	isCacheableVehicleArtworkResponse,
	singleFlightVehicleArtwork,
	vehicleArtworkRequestUrl
} from '../src/lib/vehicle-artwork-cache.ts';

const origin = 'https://pulse.example.test';
const artworkUrl = `${origin}/assets/b10-stellar-silver-abc123.webp`;
const allowed = new Set([artworkUrl]);

function artworkResponse({ status = 200, contentType = 'image/webp', redirected = false, url = artworkUrl } = {}) {
	return {
		status,
		redirected,
		url,
		headers: new Headers({ 'content-type': contentType })
	};
}

test('solo permite GET exacto, sin query/hash, same-origin y dentro de la allowlist', () => {
	assert.equal(vehicleArtworkRequestUrl(new Request(artworkUrl), allowed, origin), artworkUrl);
	assert.equal(vehicleArtworkRequestUrl(new Request(`${artworkUrl}?v=2`), allowed, origin), null);
	assert.equal(vehicleArtworkRequestUrl(new Request(`${artworkUrl}#variant`), allowed, origin), null);
	assert.equal(vehicleArtworkRequestUrl(new Request(`${origin}/api/profile`), allowed, origin), null);
	assert.equal(vehicleArtworkRequestUrl(new Request('https://cdn.example.test/vehicle.webp'), allowed, origin), null);
	assert.equal(vehicleArtworkRequestUrl(new Request(artworkUrl, { method: 'POST', body: 'x' }), allowed, origin), null);
});

test('solo admite respuestas 200 no redirigidas con URL exacta y MIME WebP', () => {
	assert.equal(isCacheableVehicleArtworkResponse(artworkResponse(), artworkUrl), true);
	assert.equal(isCacheableVehicleArtworkResponse(artworkResponse({ status: 302 }), artworkUrl), false);
	assert.equal(isCacheableVehicleArtworkResponse(artworkResponse({ redirected: true }), artworkUrl), false);
	assert.equal(isCacheableVehicleArtworkResponse(artworkResponse({ contentType: 'text/html' }), artworkUrl), false);
	assert.equal(isCacheableVehicleArtworkResponse(artworkResponse({ url: `${origin}/login` }), artworkUrl), false);
});

test('el request de calentamiento omite credenciales y rechaza redirects', () => {
	const request = createVehicleArtworkRequest(artworkUrl);
	assert.equal(request.method, 'GET');
	assert.equal(request.mode, 'same-origin');
	assert.equal(request.credentials, 'omit');
	assert.equal(request.redirect, 'error');
});

test('un único trabajo comparte la respuesta hasta que Workbox termina de guardar', async () => {
	const pending = new Map();
	let calls = 0;
	let finishResponse;
	let finishCacheWrite;
	const response = new Promise((resolve) => { finishResponse = resolve; });
	const done = new Promise((resolve) => { finishCacheWrite = resolve; });
	const work = () => {
		calls++;
		return { response, done };
	};

	const first = singleFlightVehicleArtwork(pending, artworkUrl, work);
	const second = singleFlightVehicleArtwork(pending, artworkUrl, work);
	assert.equal(calls, 1);
	assert.equal(first, second);
	finishResponse('cached response');
	assert.equal(await second, 'cached response');
	assert.equal(pending.has(artworkUrl), true);

	finishCacheWrite();
	await done;
	await Promise.resolve();
	assert.equal(pending.has(artworkUrl), false);
	assert.equal(await singleFlightVehicleArtwork(pending, artworkUrl, () => {
		calls++;
		return { response: Promise.resolve('next response'), done: Promise.resolve() };
	}), 'next response');
	assert.equal(calls, 2);
});
