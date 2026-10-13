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
const { ApiError, getSession, getState, mutateApi, requestApi } = await import(await moduleUrl('api'));

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
	const inputs = [];
	installNativeBridge({
		status: async () => ({ configured: true, authenticated: true }),
		request: (input) => { requests++; inputs.push(input); return new Promise(resolve => { resolveRequest = resolve; }); }
	}, 'ios');
	context.after(() => installNativeBridge(null));
	const attempt = createStartupAttempt(20);
	context.after(() => attempt.dispose());
	await assert.rejects(attempt.wait(getSession(attempt.signal)), StartupTimeoutError);
	assert.equal(requests, 1);
	resolveRequest({ status: 200, contentType: 'application/json', body: '{"authenticated":true}' });
	await new Promise(resolve => setImmediate(resolve));
	assert.equal('request_id' in inputs[0], false);
});

test('un cliente nativo compatible cancela el transporte de una lectura con request_id correlacionado', async (context) => {
	const controller = new AbortController();
	let resolveRequest;
	const inputs = [], cancellations = [];
	installNativeBridge({
		supportsRequestCancellation: () => true,
		request: (input) => { inputs.push(input); return new Promise(resolve => { resolveRequest = resolve; }); },
		cancelRequest: async (input) => { cancellations.push(input); }
	}, 'android');
	context.after(() => installNativeBridge(null));
	const pending = requestApi('/api/state', { method: 'GET', signal: controller.signal });
	await new Promise(resolve => setImmediate(resolve));
	assert.match(inputs[0].request_id, /^[0-9a-f-]{36}$/);
	controller.abort();
	await assert.rejects(pending, error => error instanceof DOMException && error.name === 'AbortError');
	assert.deepEqual(cancellations, [{ request_id: inputs[0].request_id }]);
	resolveRequest({ status: 200, contentType: 'application/json', body: '{}' });
	await new Promise(resolve => setImmediate(resolve));
});

test('si abort llega durante el despacho, se envía la cancelación correlacionada', async (context) => {
	const controller = new AbortController();
	let requestId;
	const cancellations = [];
	installNativeBridge({
		supportsRequestCancellation: () => true,
		request: (input) => {
			requestId = input.request_id;
			controller.abort();
			return new Promise(() => {});
		},
		cancelRequest: async (input) => { cancellations.push(input); }
	}, 'ios');
	context.after(() => installNativeBridge(null));
	await assert.rejects(requestApi('/api/session', { method: 'GET', signal: controller.signal }),
		error => error instanceof DOMException && error.name === 'AbortError');
	assert.deepEqual(cancellations, [{ request_id: requestId }]);
});

test('una respuesta terminada limpia el listener y no cancela un transporte ya completo', async (context) => {
	const controller = new AbortController();
	const cancellations = [];
	installNativeBridge({
		supportsRequestCancellation: () => true,
		request: async () => ({ status: 200, contentType: 'application/json', body: '{}' }),
		cancelRequest: async (input) => { cancellations.push(input); }
	}, 'android');
	context.after(() => installNativeBridge(null));
	const response = await requestApi('/api/state', { method: 'GET', signal: controller.signal });
	controller.abort();
	assert.equal(response.status, 200);
	assert.deepEqual(cancellations, []);
});

test('solo los POST de navegación allowlisted reciben cancelación; una mutación conserva su espera', async (context) => {
	const controllers = [new AbortController(), new AbortController()];
	const inputs = [], cancellations = [], resolvers = [];
	installNativeBridge({
		supportsRequestCancellation: () => true,
		request: (input) => { inputs.push(input); return new Promise(resolve => { resolvers.push(resolve); }); },
		cancelRequest: async (input) => { cancellations.push(input); }
	}, 'ios');
	context.after(() => installNativeBridge(null));
	const query = requestApi('/api/navigation/places', { method: 'POST', body: '{}', signal: controllers[0].signal });
	await new Promise(resolve => setImmediate(resolve));
	assert.match(inputs[0].request_id, /^[0-9a-f-]{36}$/);
	controllers[0].abort();
	await assert.rejects(query, error => error instanceof DOMException && error.name === 'AbortError');
	assert.deepEqual(cancellations, [{ request_id: inputs[0].request_id }]);

	let mutationSettled = false;
	const mutation = requestApi('/api/charges', { method: 'POST', body: '{"request_id":"synthetic"}', signal: controllers[1].signal })
		.then(value => { mutationSettled = true; return value; });
	await new Promise(resolve => setImmediate(resolve));
	assert.equal('request_id' in inputs[1], false);
	controllers[1].abort();
	await new Promise(resolve => setImmediate(resolve));
	assert.equal(mutationSettled, false);
	assert.equal(cancellations.length, 1);
	resolvers[1]({ status: 200, contentType: 'application/json', body: '{}' });
	assert.equal((await mutation).status, 200);
});
