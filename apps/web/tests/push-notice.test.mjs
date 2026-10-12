import test from 'node:test';
import assert from 'node:assert/strict';
import { genericPushNotice, parsePushNotice } from '../src/lib/push-notice.ts';

test('mantiene los avisos legacy con textos y rutas fijos', () => {
	assert.deepEqual(parsePushNotice('{"version":1,"kind":"test"}'), {
		title: 'Leapmotor Pulse', body: 'Aviso de prueba de Pulse. Comprueba este dispositivo.', tag: 'pulse-test', path: '/settings'
	});
	assert.equal(parsePushNotice('{"version":1,"kind":"charging_observation"}').path, '/energy');
});

test('mapea avisos de copias y recuperación a mensajes genéricos en Ajustes', () => {
	const backup = parsePushNotice('{"version":1,"kind":"backup_alert","origin":"privado","cause":"privada"}');
	const recovery = parsePushNotice('{"version":1,"kind":"backup_recovery","origin":"privado","cause":"privada"}');
	assert.equal(backup.body, 'Una comprobación de copias requiere revisión.');
	assert.equal(recovery.body, 'Una comprobación de copias dejó de registrar avisos.');
	assert.equal(backup.path, '/settings');
	assert.equal(recovery.path, '/settings');
	assert.doesNotMatch(JSON.stringify([backup, recovery]), /privado|privada/);
});

test('los datos inválidos, sobredimensionados y kinds desconocidos usan el fallback sin reflejo', () => {
	for (const input of [
		null,
		'{malformed',
		JSON.stringify({ version: 1, kind: 'unknown', detail: 'dato privado' }),
		JSON.stringify({ version: 2, kind: 'test' }),
		'x'.repeat(4097)
	]) assert.deepEqual(parsePushNotice(input), genericPushNotice);
	assert.doesNotMatch(JSON.stringify(parsePushNotice('{"version":1,"kind":"unknown","detail":"dato privado"}')), /dato privado/);
});
