import test from 'node:test';
import assert from 'node:assert/strict';
import { DraftGuard } from '../src/lib/draft-guard.ts';

test('consulta el estado actual sin copiar ni persistir los valores del formulario', () => {
	const guard = new DraftGuard();
	let dirty = false;
	const cleanup = guard.register('charge', () => dirty);
	assert.equal(guard.hasDirty(), false);
	dirty = true;
	assert.equal(guard.hasDirty(), true);
	dirty = false;
	assert.equal(guard.hasDirty(), false);
	cleanup();
	dirty = true;
	assert.equal(guard.hasDirty(), false);
});

test('el cleanup de una vista anterior no elimina el registro de una vista nueva', () => {
	const guard = new DraftGuard();
	const oldCleanup = guard.register('charge', () => false);
	const currentCleanup = guard.register('charge', () => true);
	oldCleanup();
	assert.equal(guard.hasDirty(), true);
	currentCleanup();
	assert.equal(guard.hasDirty(), false);
});

test('caducar sesión o restaurar una copia elimina los callbacks anteriores y permite el remount', () => {
	const guard = new DraftGuard();
	const oldCleanup = guard.register('preferences', () => true);
	guard.register('widget-profile', () => true);
	guard.clear();
	assert.equal(guard.hasDirty(), false);
	const currentCleanup = guard.register('preferences', () => true);
	oldCleanup();
	assert.equal(guard.hasDirty(), true);
	currentCleanup();
	assert.equal(guard.hasDirty(), false);
});

test('conserva otros formularios pendientes y protege una salida si un getter falla', () => {
	const guard = new DraftGuard();
	const cleanup = guard.register('maintenance', () => true);
	guard.register('push-device', () => false);
	assert.equal(guard.hasDirty(), true);
	cleanup();
	assert.equal(guard.hasDirty(), false);
	guard.register('native-link', () => { throw new Error('vista inválida'); });
	assert.equal(guard.hasDirty(), true);
	guard.clear();
	assert.equal(guard.hasDirty(), false);
});
