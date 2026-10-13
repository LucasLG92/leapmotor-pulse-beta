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

test('un guardado sin confirmar mantiene el aviso aunque los campos vuelvan al baseline', () => {
	const guard = new DraftGuard();
	let unconfirmed = true;
	const cleanup = guard.register('charge', () => false, () => unconfirmed);
	assert.equal(guard.hasDirty(), true);
	assert.equal(guard.hasUnconfirmedSave(), true);
	unconfirmed = false;
	assert.equal(guard.hasDirty(), false);
	assert.equal(guard.hasUnconfirmedSave(), false);
	cleanup();
});

test('el cleanup antiguo no borra el guardado sin confirmar del nuevo formulario', () => {
	const guard = new DraftGuard();
	const oldCleanup = guard.register('charge', () => false, () => false);
	const cleanup = guard.register('charge', () => false, () => true);
	oldCleanup();
	assert.equal(guard.hasUnconfirmedSave(), true);
	cleanup();
	assert.equal(guard.hasUnconfirmedSave(), false);
});

test('un getter de guardado incierto inválido protege la salida y clear retira el callback', () => {
	const guard = new DraftGuard();
	guard.register('charge', () => false, () => { throw new Error('synthetic expired view'); });
	assert.equal(guard.hasDirty(), true);
	assert.equal(guard.hasUnconfirmedSave(), true);
	guard.clear();
	assert.equal(guard.hasDirty(), false);
	assert.equal(guard.hasUnconfirmedSave(), false);
});
