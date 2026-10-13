import assert from 'node:assert/strict';
import test from 'node:test';
import { createNativeBridge } from '../src/bridge.ts';

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('exportar informes delega sólo days y conserva cancel/completed sin URI', async () => {
  const inputs = [];
  const bridge = createNativeBridge({ async exportJourneyReport(input) {
    inputs.push(input); return { status: input.days === 30 ? 'cancelled' : 'completed' };
  } });
  assert.deepEqual(await bridge.exportJourneyReport({ days: 30 }), { status: 'cancelled' });
  assert.deepEqual(await bridge.exportJourneyReport({ days: 90 }), { status: 'completed' });
  assert.deepEqual(inputs, [{ days: 30 }, { days: 90 }]);
  assert.equal(createNativeBridge({}).exportJourneyReport, undefined);
});
function fixture(overrides = {}) {
  const listeners = [], starts = [], stops = [];
  const plugin = {
    async addListener(event, callback) {
      const listener = { event, callback, removed: 0 };
      listeners.push(listener);
      return { async remove() { listener.removed += 1; if (overrides.removeFails) throw new Error('fixture'); } };
    },
    async startEvents(value) { starts.push(value.subscriptionId); if (overrides.startFails) throw new Error('fixture'); },
    async stopEvents(value) { stops.push(value.subscriptionId); },
    ...overrides.plugin
  };
  return { bridge: createNativeBridge(plugin), listeners, starts, stops };
}

test('el fallo al establecer stream retira ambos listeners y detiene su lease', async () => {
  const { bridge, listeners, stops } = fixture({ startFails: true });
  await assert.rejects(bridge.subscribe(() => {}, () => {}), /No se pudo establecer/);
  assert.deepEqual(listeners.map((listener) => listener.removed), [1, 1]);
  assert.deepEqual(stops, ['1']);
});

test('la limpieza de una suscripción anterior no detiene ni recibe datos de la nueva', async () => {
  const { bridge, listeners, starts, stops } = fixture();
  const revisions = [];
  const old = await bridge.subscribe(() => assert.fail('listener antiguo'), () => assert.fail('error antiguo'));
  const current = await bridge.subscribe((revision) => revisions.push(revision), () => {});
  listeners[0].callback({ revision: 1, subscriptionId: '1' });
  listeners[1].callback({ subscriptionId: '1' });
  listeners[2].callback({ revision: 2, subscriptionId: '1' });
  listeners[2].callback({ revision: -1, subscriptionId: '2' });
  listeners[2].callback({ revision: 3, subscriptionId: '2' });
  old(); await tick();
  assert.deepEqual(stops, []);
  assert.deepEqual(revisions, [3]);
  assert.deepEqual(starts, ['1', '2']);
  current(); current(); await tick();
  assert.deepEqual(stops, ['2']);
  assert.deepEqual(listeners.map((listener) => listener.removed), [1, 1, 1, 1]);
});

test('fallos al retirar listeners no impiden parar el stream ni dejan rechazos sin manejar', async () => {
  const { bridge, listeners, stops } = fixture({ removeFails: true });
  const cleanup = await bridge.subscribe(() => {}, () => {});
  cleanup(); await tick();
  assert.deepEqual(stops, ['1']);
  assert.deepEqual(listeners.map((listener) => listener.removed), [1, 1]);
});

test('un error antes de resolver startEvents llega al caller y después se puede limpiar', async () => {
  let resolveStart;
  const { bridge, listeners, stops } = fixture({ plugin: { startEvents: () => new Promise((resolve) => { resolveStart = resolve; }) } });
  let errors = 0;
  const pending = bridge.subscribe(() => {}, () => { errors += 1; });
  await tick();
  listeners[1].callback({ subscriptionId: '1' });
  assert.equal(errors, 1);
  resolveStart();
  const cleanup = await pending;
  cleanup(); await tick();
  assert.deepEqual(stops, ['1']);
});

test('una suscripción reemplazada mientras se instala el listener no inicia stream', async () => {
  let resolveFirstListener;
  let calls = 0;
  const starts = [], stops = [], removed = [];
  const bridge = createNativeBridge({
    addListener: async () => {
      const number = ++calls;
      if (number === 1) await new Promise((resolve) => { resolveFirstListener = resolve; });
      return { async remove() { removed.push(number); } };
    },
    async startEvents({ subscriptionId }) { starts.push(subscriptionId); },
    async stopEvents({ subscriptionId }) { stops.push(subscriptionId); }
  });
  const old = bridge.subscribe(() => {}, () => {});
  const rejected = assert.rejects(old, /No se pudo establecer/);
  const current = await bridge.subscribe(() => {}, () => {});
  resolveFirstListener(); await rejected;
  assert.deepEqual(starts, ['2']);
  assert.deepEqual(stops, []);
  assert.deepEqual(removed.sort(), [1, 4]);
  current(); await tick();
  assert.deepEqual(stops, ['2']);
});
