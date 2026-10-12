import { number } from './format';

// Units from the pinned provider's normalized telemetry contract.
export const telemetrySensorFields = [
	{ key: 'interior_temperature_c', label: 'Temperatura interior', unit: '°C' },
	{ key: 'battery_min_temperature_c', label: 'Temperatura mínima de batería', unit: '°C' },
	{ key: 'front_left_pressure_kpa', label: 'Neumático delantero izquierdo', unit: 'kPa' },
	{ key: 'front_right_pressure_kpa', label: 'Neumático delantero derecho', unit: 'kPa' },
	{ key: 'rear_left_pressure_kpa', label: 'Neumático trasero izquierdo', unit: 'kPa' },
	{ key: 'rear_right_pressure_kpa', label: 'Neumático trasero derecho', unit: 'kPa' }
] as const;

export function sensorValue(value: number | null | undefined, unit: string): string {
	return value == null || !Number.isFinite(value) ? 'Sin dato' : `${number(value)} ${unit}`;
}
