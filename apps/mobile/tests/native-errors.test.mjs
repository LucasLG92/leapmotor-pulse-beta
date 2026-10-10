import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeRequestFailureMessage } from '../../web/src/lib/native.ts';

test('una escritura de resultado incierto invita a comprobar antes de repetir', () => {
  const message = nativeRequestFailureMessage({ code: 'NATIVE_MUTATION_OUTCOME_UNKNOWN', message: 'detalle privado sintético' });
  assert.match(message, /puede haberse guardado/);
  assert.match(message, /Comprueba el resultado antes de repetirla/);
  assert.doesNotMatch(message, /detalle privado/);
});

test('fallos nativos desconocidos no revelan su mensaje o sus datos', () => {
  for (const error of [null, 'detalle privado', new Error('detalle privado'), { code: 'OTHER', message: 'detalle privado' }]) {
    const message = nativeRequestFailureMessage(error);
    assert.match(message, /Comprueba la conexión/);
    assert.doesNotMatch(message, /detalle privado/);
  }
});
