import test from 'node:test';
import assert from 'node:assert/strict';
import { exportNativeJourneyReport, journeyReportExportError } from '../src/lib/journey-report-export.ts';

test('el puente sólo recibe el periodo y completed no acredita recepción física', async () => {
	const inputs = [];
	const result = await exportNativeJourneyReport({ async exportJourneyReport(input) {
		inputs.push(input); return { status: 'completed' };
	} }, 90);
	assert.deepEqual(inputs, [{ days: 90 }]);
	assert.deepEqual(result, { status: 'completed' });
});

test('cancelar conserva un resultado distinto del guardado y del error', async () => {
	assert.deepEqual(await exportNativeJourneyReport({ async exportJourneyReport() {
		return { status: 'cancelled' };
	} }, 365), { status: 'cancelled' });
});

test('una app anterior sin método o un plugin no implementado pide actualización', async () => {
	for (const bridge of [null, {}, { exportJourneyReport: async () => { throw { code: 'UNIMPLEMENTED', message: 'detalle privado sintético' }; } }]) {
		await assert.rejects(exportNativeJourneyReport(bridge, 30), error => {
			assert.equal(error.code, 'NATIVE_REPORT_EXPORT_UNAVAILABLE');
			assert.match(journeyReportExportError(error).message, /Actualiza la app/);
			assert.ok(!error.message.includes('sintético')); return true;
		});
	}
});

test('periodos inválidos no alcanzan el plugin', async () => {
	let calls = 0;
	const bridge = { async exportJourneyReport() { calls++; return { status: 'completed' }; } };
	for (const days of [true, '30', 31, Infinity, null]) {
		await assert.rejects(exportNativeJourneyReport(bridge, days), { code: 'NATIVE_REPORT_EXPORT_INVALID' });
	}
	assert.equal(calls, 0);
});

test('respuesta inválida o con ruta extra queda sin confirmar y no se refleja', async () => {
	for (const result of [null, {}, { status: 'saved' }, { status: 'completed', path: 'ruta privada sintética' }]) {
		await assert.rejects(exportNativeJourneyReport({ async exportJourneyReport() { return result; } }, 30), error => {
			assert.equal(error.code, 'NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN');
			assert.match(journeyReportExportError(error).message, /vacía o parcial/); return true;
		});
	}
});

test('errores conocidos preservan sólo el código seguro y desconocidos nunca filtran detalle', async () => {
	for (const code of ['NATIVE_REPORT_EXPORT_UNAUTHENTICATED', 'NATIVE_REPORT_EXPORT_INVALID', 'NATIVE_REPORT_EXPORT_BUSY', 'NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN', 'desconocido']) {
		await assert.rejects(exportNativeJourneyReport({ async exportJourneyReport() {
			throw { code, message: 'material privado sintético' };
		} }, 30), error => {
			assert.ok(!error.message.includes('material'));
			assert.equal(journeyReportExportError(error).sessionExpired, code === 'NATIVE_REPORT_EXPORT_UNAUTHENTICATED'); return true;
		});
	}
	const hostile = { get code() { throw new Error('material privado sintético'); } };
	await assert.rejects(exportNativeJourneyReport({ async exportJourneyReport() { throw hostile; } }, 30), { code: 'NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN' });
	assert.equal(journeyReportExportError(hostile).sessionExpired, false);
});
