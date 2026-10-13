import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import ts from 'typescript';

async function moduleUrl(name) {
	const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
	let output = ts.transpileModule(source, { compilerOptions: {
		module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022
	} }).outputText;
	if (name === 'api') output = output.replace("from './native'", `from '${nativeUrl}'`)
		.replace("from './startup-read'", `from '${startupUrl}'`);
	return `data:text/javascript;base64,${Buffer.from(output).toString('base64')}`;
}
const nativeUrl = await moduleUrl('native');
const startupUrl = await moduleUrl('startup-read');
const { installNativeBridge } = await import(nativeUrl);
const { createStartupAttempt, StartupTimeoutError } = await import(startupUrl);
const { ApiError, getSession, getState, mutateApi } = await import(await moduleUrl('api'));

test('las lecturas web transmiten la señal y conservan no-store y mismo origen', async (context) => {
	const originalFetch = globalThis.fetch;
	context.after(() => { globalThis.fetch = originalFetch; });
	const calls = [];
	globalThis.fetch = async (path, init) => {
		calls.push({ path, init });
		return Response.json(path === '/api/session' ? { authenticated: false } : { mode: 'unconfigured' });
	};
	const attempt = createStartupAttempt();
	context.after(() => attempt.dispose());
	await attempt.wait(getSession(attempt.signal));
	await attempt.wait(getState(attempt.signal));
	assert.deepEqual(calls.map(call => call.path), ['/api/session', '/api/state']);
	for (const { init } of calls) {
		assert.equal(init.signal, attempt.signal);
		assert.equal(init.method, 'GET');
		assert.equal(init.credentials, 'same-origin');
		assert.equal(init.cache, 'no-store');
	}
});

test('el plazo también termina la espera de un cuerpo JSON HTTP incompleto', async (context) => {
	let headersSent = false;
	const server = createServer((request, response) => {
		response.writeHead(200, { 'Content-Type': 'application/json' });
		response.write('{"authenticated":');
		headersSent = true;
	});
	await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
	context.after(() => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
	const originalFetch = globalThis.fetch;
	context.after(() => { globalThis.fetch = originalFetch; });
	globalThis.fetch = (path, init) => originalFetch(`http://127.0.0.1:${server.address().port}${path}`, init);
	const attempt = createStartupAttempt(1000);
	context.after(() => attempt.dispose());
	await assert.rejects(attempt.wait(getSession(attempt.signal)), StartupTimeoutError);
	assert.equal(headersSent, true);
	assert.equal(attempt.signal.aborted, true);
});

test('401 sigue siendo una sesión rechazada y no se convierte en timeout', async (context) => {
	const originalFetch = globalThis.fetch;
	context.after(() => { globalThis.fetch = originalFetch; });
	globalThis.fetch = async () => Response.json({ detail: 'Acceso no autorizado.' }, { status: 401 });
	const attempt = createStartupAttempt();
	context.after(() => attempt.dispose());
	await assert.rejects(attempt.wait(getState(attempt.signal)), error => error instanceof ApiError && error.status === 401);
});

test('las mutaciones conservan su resultado y no heredan el deadline de arranque', async (context) => {
	const originalFetch = globalThis.fetch;
	context.after(() => { globalThis.fetch = originalFetch; });
	let received;
	globalThis.fetch = async (path, init) => { received = init; return Response.json({ ok: true }); };
	assert.deepEqual(await mutateApi('/api/settings', 'PUT', 'synthetic-csrf', { example: true }), { ok: true });
	assert.equal(received.signal, undefined);
	assert.equal(received.headers.get('X-Pulse-CSRF'), 'synthetic-csrf');
});

test('la pérdida de una respuesta no provoca replay automático del alta manual', async (context) => {
	const originalFetch = globalThis.fetch;
	context.after(() => { globalThis.fetch = originalFetch; });
	const payload = { started_at: '2026-10-13T10:00:00Z', kwh: 12,
		pricing_method: 'manual', request_id: 'a'.repeat(32) };
	const bodies = [];
	globalThis.fetch = async (_path, init) => {
		bodies.push(JSON.parse(init.body));
		if (bodies.length === 1) throw new TypeError('synthetic lost response');
		return Response.json({ ok: true });
	};
	await assert.rejects(mutateApi('/api/charges', 'POST', 'synthetic-csrf', payload));
	assert.equal(bodies.length, 1);
	await mutateApi('/api/charges', 'POST', 'synthetic-csrf', payload);
	assert.deepEqual(bodies, [payload, payload]);
});

test('el bridge nativo conserva la reserva y request_id en el cuerpo, sin headers especiales', async (context) => {
	const calls = [];
	installNativeBridge({
		status: async () => ({ configured: true, authenticated: true }),
		request: async (request) => {
			calls.push(request);
			return { status: 200, contentType: 'application/json', body: JSON.stringify(
				request.path === '/api/charges/requests'
					? { id: 'b'.repeat(32), expires_at: '2026-10-20T10:00:00Z' } : { ok: true }) };
		}
	}, 'ios');
	context.after(() => installNativeBridge(null));
	const reservation = await mutateApi('/api/charges/requests', 'POST', 'synthetic-csrf');
	const payload = { started_at: '2026-10-13T10:00:00Z', kwh: 12, request_id: reservation.id };
	await mutateApi('/api/charges', 'POST', 'synthetic-csrf', payload);
	assert.deepEqual(calls.map(call => [call.path, call.method, JSON.parse(call.body)]),
		[['/api/charges/requests', 'POST', {}], ['/api/charges', 'POST', payload]]);
});

test('un status nativo tardío no inicia un GET tras el timeout', async (context) => {
	let resolveStatus, calls = 0;
	installNativeBridge({
		status: () => new Promise(resolve => { resolveStatus = resolve; }),
		request: async () => { calls++; return { status: 200, contentType: 'application/json', body: '{}' }; }
	}, 'android');
	context.after(() => installNativeBridge(null));
	const attempt = createStartupAttempt(20);
	context.after(() => attempt.dispose());
	await assert.rejects(attempt.wait(getSession(attempt.signal)), StartupTimeoutError);
	resolveStatus({ configured: true, authenticated: true });
	await new Promise(resolve => setImmediate(resolve));
	assert.equal(calls, 0);
});

test('un GET nativo no cancelable físicamente deja de bloquear el arranque', async (context) => {
	let resolveRequest, requests = 0;
	installNativeBridge({
		status: async () => ({ configured: true, authenticated: true }),
		request: () => { requests++; return new Promise(resolve => { resolveRequest = resolve; }); }
	}, 'ios');
	context.after(() => installNativeBridge(null));
	const attempt = createStartupAttempt(20);
	context.after(() => attempt.dispose());
	await assert.rejects(attempt.wait(getSession(attempt.signal)), StartupTimeoutError);
	assert.equal(requests, 1);
	resolveRequest({ status: 200, contentType: 'application/json', body: '{"authenticated":true}' });
	await new Promise(resolve => setImmediate(resolve));
});
