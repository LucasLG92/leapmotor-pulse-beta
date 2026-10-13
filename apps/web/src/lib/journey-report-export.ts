import type { NativeBridge, NativeJourneyReportExportResult } from './native';

const knownCodes = new Set([
	'NATIVE_REPORT_EXPORT_UNAUTHENTICATED', 'NATIVE_REPORT_EXPORT_INVALID',
	'NATIVE_REPORT_EXPORT_BUSY', 'NATIVE_REPORT_EXPORT_UNAVAILABLE',
	'NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN'
]);

function failure(code: string): Error & { code: string } {
	return Object.assign(new Error('No se pudo confirmar la exportación.'), { code });
}

function errorCode(error: unknown): unknown {
	try { return error && typeof error === 'object' && 'code' in error ? error.code : null; }
	catch { return null; }
}

/** Sólo el periodo cruza el puente; el cliente nativo obtiene su propio informe. */
export async function exportNativeJourneyReport(
	bridge: Pick<NativeBridge, 'exportJourneyReport'> | null,
	days: 30 | 90 | 365
): Promise<NativeJourneyReportExportResult> {
	if (![30, 90, 365].includes(days)) throw failure('NATIVE_REPORT_EXPORT_INVALID');
	if (!bridge?.exportJourneyReport) throw failure('NATIVE_REPORT_EXPORT_UNAVAILABLE');
	try {
		const result = await bridge.exportJourneyReport({ days });
		if (!result || typeof result !== 'object' || Object.keys(result).length !== 1
			|| !Object.hasOwn(result, 'status') || !['completed', 'cancelled'].includes(result.status)) {
			throw failure('NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN');
		}
		return { status: result.status };
	} catch (error) {
		let code = 'NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN';
		const nativeCode = errorCode(error);
		if (typeof nativeCode === 'string') {
			if (knownCodes.has(nativeCode)) code = nativeCode;
			else if (['UNIMPLEMENTED', 'UNAVAILABLE'].includes(nativeCode)) code = 'NATIVE_REPORT_EXPORT_UNAVAILABLE';
		}
		throw failure(code);
	}
}

export function journeyReportExportError(error: unknown): { message: string; sessionExpired: boolean } {
	const code = errorCode(error);
	if (code === 'NATIVE_REPORT_EXPORT_UNAUTHENTICATED') return {
		message: 'La sesión ha cambiado o caducado. Vuelve a iniciar sesión y carga el informe antes de guardarlo.', sessionExpired: true
	};
	if (code === 'NATIVE_REPORT_EXPORT_INVALID') return {
		message: 'No se pudo preparar un informe válido. Actualiza el diario antes de volver a intentarlo.', sessionExpired: false
	};
	if (code === 'NATIVE_REPORT_EXPORT_BUSY') return {
		message: 'Hay otro selector de archivos abierto. Termina o cancela ese guardado antes de continuar.', sessionExpired: false
	};
	if (code === 'NATIVE_REPORT_EXPORT_UNAVAILABLE') return {
		message: 'No se pudo abrir el selector de archivos en esta app. Actualiza la app o abre Pulse en un navegador.', sessionExpired: false
	};
	return {
		message: 'No se pudo confirmar el archivo. Revisa la ubicación elegida antes de volver a guardar; puede existir una copia vacía o parcial.', sessionExpired: false
	};
}
