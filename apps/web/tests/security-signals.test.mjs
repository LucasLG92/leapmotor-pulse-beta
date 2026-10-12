import test from 'node:test';
import assert from 'node:assert/strict';
import { currentSecuritySignals, validSecurityCode } from '../src/lib/security-signals.ts';

function snapshot(overrides = {}) {
	return {
		id: 'snapshot-1',
		source: 'provider',
		observed_at: '2026-10-12T10:00:00Z',
		provider_received_at: '2026-10-12T10:00:01Z',
		server_received_at: '2026-10-12T10:00:02Z',
		vehicle: { vehicle_security_code: 0, sentry_code: 255 },
		field_sources: { vehicle_security_code: 'vehicle_security_code', sentry_code: 'sentry_code' },
		...overrides
	};
}

const fresh = { temporal_status: 'fresh' };

test('acepta los límites decimales 0 y 255 y rechaza coerciones o valores fuera de rango', () => {
	assert.equal(validSecurityCode(0), 0);
	assert.equal(validSecurityCode(255), 255);
	for (const value of [-1, 256, 1.5, NaN, Infinity, '12', null, undefined, true]) {
		assert.equal(validSecurityCode(value), null);
	}
});

test('expone únicamente campos presentes bajo sus nombres exactos y muestra decimal', () => {
	const readings = currentSecuritySignals(snapshot(), fresh, 'telemetry_read_only', true);
	assert.deepEqual(readings.map(({ key, code, source }) => ({ key, code, source })), [
		{ key: 'vehicle_security_code', code: 0, source: 'vehicle_security_code' },
		{ key: 'sentry_code', code: 255, source: 'sentry_code' }
	]);
	assert.deepEqual(currentSecuritySignals(snapshot({
		vehicle: { vehicle_security_code: 12, sentry_code: null },
		field_sources: { vehicle_security_code: 'vehicle_security_code' }
	}), fresh, 'telemetry_read_only', true).map(({ key }) => key), ['vehicle_security_code']);
});

test('solo acepta fuentes string que coinciden exactamente con cada nombre de campo', () => {
	for (const source of [undefined, null, 'provider', 'Vehicle_security_code', 12, true]) {
		const readings = currentSecuritySignals(snapshot({
			field_sources: { vehicle_security_code: source, sentry_code: 'sentry_code' }
		}), fresh, 'telemetry_read_only', true);
		assert.deepEqual(readings.map(({ key }) => key), ['sentry_code']);
	}
	assert.deepEqual(currentSecuritySignals(snapshot({
		field_sources: { vehicle_security_code: 'vehicle_security_code', sentry_code: null }
	}), fresh, 'telemetry_read_only', true).map(({ key }) => key), ['vehicle_security_code']);
});

test('trata campos ausentes e inválidos como desconocidos', () => {
	assert.deepEqual(currentSecuritySignals(snapshot({ vehicle: {} }), fresh, 'telemetry_read_only', true), []);
	assert.deepEqual(currentSecuritySignals(snapshot({ vehicle: { vehicle_security_code: 256, sentry_code: '4' } }), fresh, 'telemetry_read_only', true), []);
	assert.deepEqual(currentSecuritySignals(snapshot({ vehicle: null }), fresh, 'telemetry_read_only', true), []);
	assert.deepEqual(currentSecuritySignals(snapshot({ field_sources: null }), fresh, 'telemetry_read_only', true), []);
});

test('oculta lecturas si falta snapshot, no hay conexión, es demo o no es temporalmente reciente', () => {
	assert.deepEqual(currentSecuritySignals(null, fresh, 'telemetry_read_only', true), []);
	assert.deepEqual(currentSecuritySignals(snapshot(), fresh, 'telemetry_read_only', false), []);
	assert.deepEqual(currentSecuritySignals(snapshot(), fresh, 'demo', true), []);
	for (const temporal_status of ['stale', 'unknown', 'unavailable']) {
		assert.deepEqual(currentSecuritySignals(snapshot(), { temporal_status }, 'telemetry_read_only', true), []);
	}
});
