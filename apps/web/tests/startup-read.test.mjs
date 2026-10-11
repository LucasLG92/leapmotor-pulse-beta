import test from 'node:test';
import assert from 'node:assert/strict';
import { getEventListeners } from 'node:events';
import { createStartupAttempt, StartupTimeoutError, waitForRead } from '../src/lib/startup-read.ts';

test('un arranque terminado conserva el resultado y libera sus esperas', async () => {
	const attempt = createStartupAttempt(100);
	assert.equal(await attempt.wait(Promise.resolve('ready')), 'ready');
	assert.equal(attempt.signal.aborted, false);
	assert.equal(getEventListeners(attempt.signal, 'abort').length, 0);
	attempt.dispose();
});

test('las lecturas secuenciales comparten un plazo y una lectura infinita termina', async () => {
	const attempt = createStartupAttempt(25);
	assert.equal(await attempt.wait(Promise.resolve('session')), 'session');
	await assert.rejects(attempt.wait(new Promise(() => {})), StartupTimeoutError);
	assert.equal(attempt.signal.aborted, true);
	assert.equal(getEventListeners(attempt.signal, 'abort').length, 0);
	attempt.dispose();
});

test('cancelar al desmontar descarta un resultado tardío y no conserva listeners', async () => {
	const attempt = createStartupAttempt(100);
	let resolveLate;
	const result = attempt.wait(new Promise(resolve => { resolveLate = resolve; }));
	attempt.dispose();
	await assert.rejects(result, { name: 'AbortError' });
	resolveLate('late state');
	assert.equal(getEventListeners(attempt.signal, 'abort').length, 0);
	await assert.rejects(attempt.wait(Promise.resolve('new read')), { name: 'AbortError' });
});

test('un rechazo posterior al timeout queda consumido por la espera original', async () => {
	const attempt = createStartupAttempt(10);
	let rejectLate;
	const result = attempt.wait(new Promise((resolve, reject) => { rejectLate = reject; }));
	await assert.rejects(result, StartupTimeoutError);
	rejectLate(new Error('late bridge rejection'));
	await new Promise(resolve => setImmediate(resolve));
	assert.equal(getEventListeners(attempt.signal, 'abort').length, 0);
	attempt.dispose();
});

test('una lectura ya cancelada no vuelve a publicar su resultado', async () => {
	const controller = new AbortController();
	controller.abort(new StartupTimeoutError());
	await assert.rejects(waitForRead(Promise.resolve('late'), controller.signal), StartupTimeoutError);
});
