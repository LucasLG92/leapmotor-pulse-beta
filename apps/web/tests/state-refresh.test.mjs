import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

async function moduleUrl(name, imports = {}) {
	const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
	let output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
	for (const [path, url] of Object.entries(imports)) output = output.replace(`from '${path}'`, `from '${url}'`);
	return `data:text/javascript;base64,${Buffer.from(output).toString('base64')}`;
}
const { createStateRefresh } = await import(await moduleUrl('state-refresh', { './startup-read': await moduleUrl('startup-read') }));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

test('los eventos durante una lectura se agrupan y el resultado anterior no se aplica', async () => {
	const requests = [], applied = [];
	const reader = createStateRefresh(() => { const request = deferred(); requests.push(request); return request.promise; }, state => applied.push(state));
	const first = reader.refresh();
	for (let i = 0; i < 10; i++) assert.equal(reader.refresh(), first);
	assert.equal(requests.length, 1);
	requests[0].resolve('anterior'); await flush();
	assert.deepEqual(applied, []); assert.equal(requests.length, 2);
	requests[1].resolve('actual'); assert.equal(await first, true);
	assert.deepEqual(applied, ['actual']);
});

test('caducar una sesión impide que su respuesta o error afecte a la siguiente', async () => {
	const old = deferred(), next = deferred(), applied = [], signals = [];
	let calls = 0;
	const reader = createStateRefresh(signal => { signals.push(signal); return ++calls === 1 ? old.promise : next.promise; }, state => applied.push(state));
	const previous = reader.refresh(); reader.invalidate();
	assert.equal(signals[0].aborted, true);
	const current = reader.refresh(); next.resolve('sesión nueva');
	assert.equal(await current, true); assert.equal(await previous, false);
	old.reject(new Error('error tardío de otra sesión')); await flush();
	assert.deepEqual(applied, ['sesión nueva']);
});

test('un evento invalida la respuesta antes de que termine el debounce de otra consulta', async () => {
	const requests = [], applied = [];
	const reader = createStateRefresh(() => { const request = deferred(); requests.push(request); return request.promise; }, state => applied.push(state));
	const flight = reader.refresh();
	assert.equal(reader.markDirty(), true);
	requests[0].resolve('anterior'); await flush();
	assert.deepEqual(applied, []); assert.equal(requests.length, 2);
	requests[1].resolve('actual'); assert.equal(await flight, true);
	assert.deepEqual(applied, ['actual']);
});

test('un fallo de red se comunica y una consulta posterior puede recuperar el estado', async () => {
	const applied = []; let calls = 0;
	const reader = createStateRefresh(async () => { if (++calls === 1) throw new Error('sin red'); return 'recuperado'; }, value => applied.push(value));
	await assert.rejects(reader.refresh(), /sin red/);
	assert.equal(await reader.refresh(), true); assert.deepEqual(applied, ['recuperado']);
});

test('una lectura que no termina agota el plazo y libera el refresco para reintentar', async () => {
	let calls = 0; const applied = [];
	const reader = createStateRefresh(() => ++calls === 1 ? new Promise(() => {}) : Promise.resolve('actual'), value => applied.push(value), 20);
	await assert.rejects(reader.refresh(), error => error.name === 'TimeoutError');
	assert.equal(await reader.refresh(), true); assert.deepEqual(applied, ['actual']);
});
