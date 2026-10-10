import test from 'node:test';
import assert from 'node:assert/strict';
import { observeServiceWorkerUpdate } from '../src/lib/pwa-update.ts';

class Worker extends EventTarget {
	state = 'installing';
	change(state) { this.state = state; this.dispatchEvent(new Event('statechange')); }
}

class Registration extends EventTarget {
	installing = null;
	waiting = null;
	begin(worker) { this.installing = worker; this.dispatchEvent(new Event('updatefound')); }
}

test('ofrece el worker que ya estaba instalándose sin necesitar otro updatefound', () => {
	const registration = new Registration(), worker = new Worker();
	registration.installing = worker;
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => true, () => offers++);
	assert.equal(offers, 0);
	registration.installing = null;
	registration.waiting = worker;
	worker.change('installed');
	assert.equal(offers, 1);
	stop();
});

test('ofrece una actualización ya en espera y no duplica el mismo aviso', () => {
	const registration = new Registration(), worker = new Worker();
	worker.state = 'installed'; registration.waiting = worker;
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => true, () => offers++);
	registration.dispatchEvent(new Event('updatefound'));
	assert.equal(offers, 1);
	stop();
});

test('detecta futuras instalaciones y permite la siguiente versión sin aplicar ninguna', () => {
	const registration = new Registration();
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => true, () => offers++);
	for (let i = 0; i < 2; i++) {
		const worker = new Worker(); registration.begin(worker);
		worker.change('installed'); registration.installing = null; registration.waiting = worker;
		registration.dispatchEvent(new Event('updatefound'));
	}
	assert.equal(offers, 2);
	stop();
});

test('la primera instalación sin controlador no se anuncia como actualización', () => {
	const registration = new Registration(), worker = new Worker();
	registration.installing = worker;
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => false, () => offers++);
	worker.change('installed'); registration.waiting = worker;
	registration.dispatchEvent(new Event('updatefound'));
	assert.equal(offers, 0);
	stop();
});

test('un intento fallido o sustituido no anuncia su estado como versión disponible', () => {
	const registration = new Registration(), old = new Worker(), failed = new Worker(), current = new Worker();
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => true, () => offers++);
	registration.begin(failed); failed.change('redundant');
	registration.begin(old); registration.begin(current);
	old.change('installed'); assert.equal(offers, 0);
	current.change('installed'); assert.equal(offers, 1);
	stop();
});

test('desmontar retira el observador y descarta estados y versiones posteriores', () => {
	const registration = new Registration(), worker = new Worker();
	registration.installing = worker;
	let offers = 0;
	const stop = observeServiceWorkerUpdate(registration, () => true, () => offers++);
	stop(); stop(); worker.change('installed');
	const later = new Worker(); registration.begin(later); later.change('installed');
	assert.equal(offers, 0);
});
