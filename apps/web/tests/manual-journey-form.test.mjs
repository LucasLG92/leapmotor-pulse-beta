import test from 'node:test';
import assert from 'node:assert/strict';
import { convertJourneyBasis, emptyJourneyForm, journeyQuantityInput, localJourneyTime, validateJourneyForm } from '../src/lib/manual-journey-form.ts';

const now = Date.parse('2026-11-01T12:00:00Z');
const form = () => ({ ...emptyJourneyForm(), timeBasis: 'utc', started: '2026-10-13T08:00', ended: '2026-10-13T09:00' });

test('editar cantidades pequeñas conserva su valor y sigue admitiendo cambios de etiquetas', () => {
	for (const value of [0.0000006, Number.MIN_VALUE, 1.23456789e-20, 0, 5000]) {
		const input = journeyQuantityInput(value);
		assert.equal(Number(input), value);
		assert.ok(!input.includes('e'));
		const result = validateJourneyForm({ ...form(), distance: input, energy: input, to: 'Nuevo destino' }, now);
		assert.deepEqual(result.errors, {});
		assert.equal(result.values.distance_km, value);
		assert.equal(result.values.energy_kwh, value);
	}
	assert.equal(journeyQuantityInput(null), '');
});

test('una ausencia no se convierte en energía o distancia cero y conserva unidades explícitas', () => {
	const input = form();
	const result = validateJourneyForm(input, now);
	assert.deepEqual(result.errors, {});
	assert.equal(result.values.distance_km, null);
	assert.equal(result.values.energy_kwh, null);
	assert.equal(result.values.from_label, null);
	assert.equal(result.values.started_at, '2026-10-13T08:00:00.000Z');
	input.distance = '0'; input.energy = '0,0';
	assert.equal(validateJourneyForm(input, now).values.distance_km, 0);
	assert.equal(validateJourneyForm(input, now).values.energy_kwh, 0);
});

test('rechaza valores no finitos, fechas normalizadas por JS y fin futuro o invertido', () => {
	for (const distance of ['NaN', 'Infinity', '-1', '50001', '1e9']) {
		assert.ok(validateJourneyForm({ ...form(), distance }, now).errors.distance);
	}
	for (const ended of ['2026-02-30T09:00', '2026-10-13T07:59', '2026-11-02T09:00']) {
		assert.ok(validateJourneyForm({ ...form(), ended }, now).errors.ended);
	}
});

test('las paradas deben estar dentro del recorrido, ordenadas y sin solapamientos', () => {
	const input = form();
	input.stops = [{ started: '2026-10-13T08:10', ended: '2026-10-13T08:20' },
		{ started: '2026-10-13T08:15', ended: '2026-10-13T08:30' }];
	assert.ok(validateJourneyForm(input, now).errors['stop-1-started']);
	input.stops = [{ started: '2026-10-13T07:55', ended: '2026-10-13T08:20' }];
	assert.ok(validateJourneyForm(input, now).errors['stop-0-started']);
	input.stops = [{ started: '2026-10-13T08:10', ended: '2026-10-13T08:20' }];
	assert.equal(validateJourneyForm(input, now).values.stops[0].ended_at, '2026-10-13T08:20:00.000Z');
});

test('UTC permite registrar un recorrido que cruza la repetición de hora local de Madrid', context => {
	const old = process.env.TZ; process.env.TZ = 'Europe/Madrid';
	context.after(() => { if (old === undefined) delete process.env.TZ; else process.env.TZ = old; });
	const input = { ...form(), timeBasis: 'local', started: '2026-10-25T02:30', ended: '2026-10-25T03:10' };
	assert.ok(validateJourneyForm(input, now).errors.started);
	assert.equal(convertJourneyBasis(input, 'utc'), null);
	input.timeBasis = 'utc'; input.started = '2026-10-25T00:50'; input.ended = '2026-10-25T01:20';
	assert.equal(Date.parse(validateJourneyForm(input, now).values.ended_at) - Date.parse(validateJourneyForm(input, now).values.started_at), 1800000);
	input.timeBasis = 'local'; input.started = '2026-03-29T02:30'; input.ended = '2026-03-29T03:30';
	assert.ok(validateJourneyForm(input, now).errors.started);
});

test('cambiar una zona válida conserva instantes y al editar conserva la precisión original', context => {
	const old = process.env.TZ; process.env.TZ = 'Europe/Madrid';
	context.after(() => { if (old === undefined) delete process.env.TZ; else process.env.TZ = old; });
	const input = { ...form(), started: '2026-10-13T08:00:13.123', ended: '2026-10-13T09:00:12.456' };
	const local = convertJourneyBasis(input, 'local');
	assert.equal(local.started, '2026-10-13T10:00:13.123');
	assert.deepEqual(validateJourneyForm(local, now).values, validateJourneyForm(input, now).values);
	const original = { started_at: '2026-10-13T08:00:13.123456Z', ended_at: '2026-10-13T09:00:12.456789Z', from_label: null, to_label: null, distance_km: null, energy_kwh: null, stops: [] };
	input.started = localJourneyTime(original.started_at, 'utc'); input.ended = localJourneyTime(original.ended_at, 'utc');
	const saved = validateJourneyForm(input, now, original).values;
	assert.equal(saved.started_at, original.started_at); assert.equal(saved.ended_at, original.ended_at);
});
