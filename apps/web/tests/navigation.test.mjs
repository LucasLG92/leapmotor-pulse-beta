import test from 'node:test';
import assert from 'node:assert/strict';
import {
	connectorLabels,
	cancelTileJob,
	distanceMeters,
	isNavigationPlace,
	isNavigationStation,
	isPlainProviderText,
	isPublicPoint,
	isSafeTileDataUrl,
	makeManualPlace,
	mapLinks,
	parsePlaceSearchQuery,
	parsePublicCoordinate,
	RequestGeneration,
	sameMapArea,
	takeNextCurrentTileJob
} from '../src/lib/navigation.ts';

test('presenta conectores con nombres legibles sin duplicar CCS', () => {
	assert.equal(connectorLabels(['type2', 'ccs', 'type2_combo']), 'Tipo 2, CCS');
	assert.equal(connectorLabels([]), 'No indicados');
	assert.equal(connectorLabels(['https://untrusted.invalid/']), 'Otro conector');
});

test('el redondeo del centro al abrir o redimensionar no invalida el área consultada', () => {
	const point = {latitude: 40.4138, longitude: -3.6921};
	assert.equal(sameMapArea(point, {latitude: 40.41376, longitude: -3.69218}), true);
	assert.equal(sameMapArea(point, {latitude: 40.4148, longitude: -3.6921}), false);
	assert.equal(sameMapArea(null, point), false);
});

test('valida coordenadas públicas finitas y acota sus rangos', () => {
	assert.equal(isPublicPoint({ latitude: 90, longitude: -180 }), true);
	assert.equal(isPublicPoint({ latitude: Number.NaN, longitude: 0 }), false);
	assert.equal(isPublicPoint({ latitude: 91, longitude: 0 }), false);
	assert.equal(parsePublicCoordinate(' 40.4168 ', -90, 90), 40.4168);
	assert.equal(parsePublicCoordinate('', -90, 90), null);
	assert.equal(parsePublicCoordinate('40,4168', -90, 90), null);
	assert.equal(parsePublicCoordinate('91', -90, 90), null);
	assert.deepEqual(makeManualPlace('40.4168', '-3.7038'), {
		id: 'manual:40.416800,-3.703800', name: 'Punto público', latitude: 40.4168, longitude: -3.7038
	});
	assert.equal(makeManualPlace('40.4168', 'https://example.com'), null);
});

test('acepta búsquedas explícitas dentro del límite y rechaza consultas cortas o largas', () => {
	assert.equal(parsePlaceSearchQuery('  Madrid  '), 'Madrid');
	assert.equal(parsePlaceSearchQuery(' a '), null);
	assert.equal(parsePlaceSearchQuery('x'.repeat(121)), null);
	assert.equal(parsePlaceSearchQuery('x'.repeat(120)), 'x'.repeat(120));
});

test('filtra texto proveedor con etiquetas o URL y construye enlaces en hosts fijos', () => {
	assert.equal(isPlainProviderText('Estación pública'), true);
	assert.equal(isPlainProviderText('<img src=x onerror=alert(1)>'), false);
	assert.equal(isPlainProviderText('https://example.com'), false);
	const place = { id: 'node:123', name: 'Plaza Mayor & centro', latitude: 40.4168, longitude: -3.7038 };
	assert.equal(isNavigationPlace(place), true);
	assert.equal(isNavigationPlace({ ...place, name: '<script>alert(1)</script>' }), false);
	const links = mapLinks(place);
	assert.ok(links);
	assert.equal(new URL(links.apple).origin, 'https://maps.apple.com');
	assert.equal(new URL(links.google).origin, 'https://www.google.com');
	assert.equal(new URL(links.apple).searchParams.get('daddr'), '40.4168,-3.7038');
	assert.equal(new URL(links.apple).searchParams.get('dirflg'), 'd');
	assert.equal(new URL(links.google).searchParams.get('api'), '1');
	assert.equal(new URL(links.google).pathname, '/maps/dir/');
	assert.equal(new URL(links.google).searchParams.get('destination'), '40.4168,-3.7038');
	assert.equal(new URL(links.google).searchParams.get('travelmode'), 'driving');
	assert.equal(mapLinks({ ...place, latitude: Number.POSITIVE_INFINITY }), null);
});

test('valida cargadores públicos como datos simples y acotados', () => {
	const station = {
		id: 'node:456', name: 'Carga pública', latitude: 40.417, longitude: -3.704,
		operator: null, address: 'Calle pública 1', connectors: ['Type 2'], power_kw: 2_000, distance_m: 5_100
	};
	assert.equal(isNavigationStation(station), true);
	assert.equal(isNavigationStation({ ...station, operator: undefined, address: undefined, power_kw: undefined }), true);
	assert.equal(isNavigationStation({ ...station, id: 'i'.repeat(80), connectors: Array(12).fill('Type 2'), power_kw: 0 }), true);
	assert.equal(isNavigationStation({ ...station, address: 'https://example.com' }), false);
	assert.equal(isNavigationStation({ ...station, distance_m: -1 }), false);
	assert.equal(isNavigationStation({ ...station, distance_m: 5_101 }), false);
	assert.equal(isNavigationStation({ ...station, connectors: Array(13).fill('Type 2') }), false);
	assert.equal(isNavigationStation({ ...station, power_kw: 2_001 }), false);
	assert.equal(isNavigationStation({ ...station, id: 'i'.repeat(81) }), false);
});

test('calcula distancia recta con la fórmula de gran círculo', () => {
	assert.equal(distanceMeters({ latitude: 40, longitude: -3 }, { latitude: 40, longitude: -3 }), 0);
	assert.ok(Math.abs(distanceMeters({ latitude: 0, longitude: 0 }, { latitude: 1, longitude: 0 }) - 111_195) <= 10);
	assert.equal(Number.isNaN(distanceMeters({ latitude: 91, longitude: 0 }, { latitude: 0, longitude: 0 })), true);
});

test('solo acepta tiles PNG en data URI y dentro del tamaño cliente documentado', () => {
	assert.equal(isSafeTileDataUrl('data:image/png;base64,iVBORw0KGgo='), true);
	assert.equal(isSafeTileDataUrl('https://tiles.example/0/0/0.png'), false);
	assert.equal(isSafeTileDataUrl('data:image/svg+xml;base64,PHN2Zz4='), false);
	assert.equal(isSafeTileDataUrl(`data:image/png;base64,${'A'.repeat(180_001)}`), false);
});

test('al cambiar la vista cancela los tiles activos y descarta la cola del viewport anterior', () => {
	const active = Array.from({ length: 2 }, () => {
		const controller = new AbortController();
		return { cancelled: false, controller };
	});
	active.forEach(cancelTileJob);
	assert.deepEqual(active.map((job) => job.controller.signal.aborted), [true, true]);

	const stale = Array.from({ length: 18 }, (_, index) => ({ id: `old-${index}`, cancelled: false }));
	const current = Array.from({ length: 4 }, (_, index) => ({ id: `new-${index}`, cancelled: false }));
	const queue = [...stale, ...current];
	const visibleIds = new Set(current.map((job) => job.id));
	const started = [];
	let next;
	while ((next = takeNextCurrentTileJob(queue, (job) => visibleIds.has(job.id), cancelTileJob))) {
		started.push(next.id);
	}

	assert.deepEqual(started, current.map((job) => job.id));
	assert.ok(stale.every((job) => job.cancelled));
	assert.ok(current.every((job) => !job.cancelled));
});

test('la generación aborta el trabajo anterior y descarta resultados tardíos', () => {
	const gate = new RequestGeneration();
	const first = gate.begin();
	const second = gate.begin();
	assert.equal(first.signal.aborted, true);
	assert.equal(gate.isCurrent(first.generation), false);
	assert.equal(gate.isCurrent(second.generation), true);
	gate.invalidate();
	assert.equal(second.signal.aborted, true);
	assert.equal(gate.isCurrent(second.generation), false);
	const afterInvalidation = gate.begin();
	assert.equal(gate.isCurrent(afterInvalidation.generation), true);
	gate.dispose();
	assert.equal(afterInvalidation.signal.aborted, true);
	assert.equal(gate.isCurrent(afterInvalidation.generation), false);
	const afterDispose = gate.begin();
	assert.equal(afterDispose.signal.aborted, true);
	assert.equal(gate.isCurrent(afterDispose.generation), false);
});
