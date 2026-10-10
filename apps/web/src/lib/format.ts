const numberFormat = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 1 });
const decimalFormat = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const countFormat = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 });
const euroFormat = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });

export function number(value: number | null | undefined, digits = false): string {
	if (value === null || value === undefined || !Number.isFinite(value)) return '—';
	return (digits ? decimalFormat : numberFormat).format(value);
}

export function count(value: number | null | undefined): string {
	if (value === null || value === undefined || !Number.isSafeInteger(value) || value < 0) return '—';
	return countFormat.format(value);
}

export function euros(value: number | null | undefined): string {
	if (value === null || value === undefined || !Number.isFinite(value)) return '—';
	return euroFormat.format(value);
}

export function dateTime(value: string | null | undefined): string {
	if (!value) return 'Sin fecha';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return 'Sin fecha';
	return new Intl.DateTimeFormat('es-ES', {
		day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
	}).format(date);
}

export function dateOnly(value: string | null | undefined): string {
	if (!value) return 'Sin fecha';
	const date = new Date(`${value.slice(0, 10)}T12:00:00`);
	if (Number.isNaN(date.getTime())) return 'Sin fecha';
	return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
}

export function relativeAge(value: string | null | undefined): string {
	if (!value) return 'Sin lectura disponible';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return 'Sin lectura disponible';
	const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000));
	if (minutes < 1) return 'hace menos de un minuto';
	if (minutes < 60) return `hace ${minutes} min`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `hace ${hours} h`;
	const days = Math.floor(hours / 24);
	return `hace ${days} ${days === 1 ? 'día' : 'días'}`;
}

export function localDateTimeValue(value?: string): string {
	const date = value ? new Date(value) : new Date();
	const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
	return local.toISOString().slice(0, 16);
}

export function isoFromLocalDateTime(value: string): string {
	return new Date(value).toISOString();
}
