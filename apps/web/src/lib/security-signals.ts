import type { Mode, TelemetrySnapshot, TemporalQuality } from './types';

export const securitySignalFields = [
	{ key: 'vehicle_security_code', label: 'Seguridad · código sin interpretar' },
	{ key: 'sentry_code', label: 'Centinela · código sin interpretar' }
] as const;

export type SecuritySignalKey = typeof securitySignalFields[number]['key'];

export interface SecuritySignalReading {
	key: SecuritySignalKey;
	label: string;
	code: number;
	source: string;
}

export function validSecurityCode(value: unknown): number | null {
	return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 255
		? value
		: null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function currentSecuritySignals(
	snapshot: TelemetrySnapshot | null,
	quality: TemporalQuality | null,
	mode: Mode,
	online: boolean
): SecuritySignalReading[] {
	if (!online || mode === 'demo' || !snapshot || quality?.temporal_status !== 'fresh') return [];
	if (!isRecord(snapshot.vehicle) || !isRecord(snapshot.field_sources)) return [];

	return securitySignalFields.flatMap(({ key, label }) => {
		const code = validSecurityCode(snapshot.vehicle[key]);
		const source = snapshot.field_sources[key];
		if (code === null || source !== key) return [];
		return [{ key, label, code, source }];
	});
}
