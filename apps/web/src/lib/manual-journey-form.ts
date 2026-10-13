/** Manual entries only; no vehicle signals, storage or inferred energy. */
export type JourneyForm = {
	timeBasis: 'local' | 'utc';
	started: string; ended: string; from: string; to: string;
	distance: string; energy: string; notes: string; tags: string[];
	stops: Array<{ started: string; ended: string }>;
};
export type JourneyValues = {
	started_at: string; ended_at: string; from_label: string | null; to_label: string | null;
	distance_km: number | null; energy_kwh: number | null; notes: string | null; tags: string[];
	stops: Array<{ started_at: string; ended_at: string }>;
};

export function emptyJourneyForm(): JourneyForm {
	return { timeBasis: 'local', started: '', ended: '', from: '', to: '', distance: '', energy: '', notes: '', tags: [], stops: [] };
}

function hasControlCharacters(value: string): boolean {
	return /[\u0000-\u001f\u007f-\u009f]/u.test(value);
}

function hasUnpairedSurrogate(value: string): boolean {
	for (let index = 0; index < value.length; index += 1) {
		const code = value.charCodeAt(index);
		if (code >= 0xd800 && code <= 0xdbff) {
			const next = value.charCodeAt(index + 1);
			if (index + 1 >= value.length || next < 0xdc00 || next > 0xdfff) return true;
			index += 1;
		} else if (code >= 0xdc00 && code <= 0xdfff) return true;
	}
	return false;
}

/** JavaScript has lowercase, not Python's full Unicode casefold; cover the common expansions too. */
function journeyTagKey(tag: string): string {
	return tag.normalize('NFC').trim().toLowerCase().replace(/ß/g, 'ss').replace(/ς/g, 'σ');
}

function codePointLength(value: string): number {
	return [...value].length;
}

/** Expand a finite number's shortest representation without rounding its value. */
export function journeyQuantityInput(value: number | null): string {
	if (value === null) return '';
	const text = String(value);
	const [coefficient, power] = text.toLowerCase().split('e');
	if (power === undefined) return text;
	const digits = coefficient.replace('.', '');
	const point = (coefficient.includes('.') ? coefficient.indexOf('.') : coefficient.length) + Number(power);
	if (point <= 0) return `0.${'0'.repeat(-point)}${digits}`;
	if (point >= digits.length) return digits + '0'.repeat(point - digits.length);
	return `${digits.slice(0, point)}.${digits.slice(point)}`;
}

export function localJourneyTime(iso: string, basis: 'local' | 'utc' = 'local'): string {
	const date = new Date(iso);
	if (!Number.isFinite(date.getTime())) return '';
	if (basis === 'utc') return date.toISOString().replace(/Z$/, '');
	return `${date.getFullYear().toString().padStart(4, '0')}-${(date.getMonth() + 1).toString().padStart(2, '0')}-${date.getDate().toString().padStart(2, '0')}T${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}:${date.getSeconds().toString().padStart(2, '0')}.${date.getMilliseconds().toString().padStart(3, '0')}`;
}

function parseTime(text: string, basis: 'local' | 'utc'): Date | null {
	const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(text);
	if (!match) return null;
	const date = new Date(text + (basis === 'utc' ? 'Z' : ''));
	if (!Number.isFinite(date.getTime())) return null;
	const parts = (candidate: Date) => basis === 'utc'
		? [candidate.getUTCFullYear(), candidate.getUTCMonth() + 1, candidate.getUTCDate(), candidate.getUTCHours(), candidate.getUTCMinutes(), candidate.getUTCSeconds(), candidate.getUTCMilliseconds()]
		: [candidate.getFullYear(), candidate.getMonth() + 1, candidate.getDate(), candidate.getHours(), candidate.getMinutes(), candidate.getSeconds(), candidate.getMilliseconds()];
	const expected = match.slice(1, 7).map(part => Number(part ?? 0)); expected.push(Number((match[7] ?? '').padEnd(3, '0')));
	if (parts(date).some((part, index) => part !== expected[index])) return null;
	// Ambiguous local wall times need the explicit UTC mode, rather than guessing a fold.
	if (basis === 'local') {
		for (const direction of [-1, 1]) {
			const otherOffset = new Date(date.getTime() + direction * 86400000).getTimezoneOffset();
			const delta = otherOffset - date.getTimezoneOffset();
			if (delta && parts(new Date(date.getTime() + delta * 60000)).every((part, index) => part === expected[index])) return null;
		}
	}
	return date;
}

export function convertJourneyBasis(form: JourneyForm, basis: 'local' | 'utc'): JourneyForm | null {
	const convert = (text: string) => {
		if (!text) return '';
		const date = parseTime(text, form.timeBasis);
		return date ? localJourneyTime(date.toISOString(), basis) : null;
	};
	const started = convert(form.started), ended = convert(form.ended);
	const stops = form.stops.map(stop => ({ started: convert(stop.started), ended: convert(stop.ended) }));
	if (started === null || ended === null || stops.some(stop => stop.started === null || stop.ended === null)) return null;
	return { ...form, timeBasis: basis, started, ended, stops: stops as JourneyForm['stops'] };
}

export function validateJourneyForm(form: JourneyForm, now = Date.now(), original?: JourneyValues): {
	errors: Record<string, string>; values: JourneyValues | null;
} {
	const errors: Record<string, string> = {};
	const preserved = (text: string, iso?: string) => iso && text === localJourneyTime(iso, form.timeBasis) ? iso : null;
	const startIso = preserved(form.started, original?.started_at), endIso = preserved(form.ended, original?.ended_at);
	const start = startIso ? new Date(startIso) : parseTime(form.started, form.timeBasis);
	const end = endIso ? new Date(endIso) : parseTime(form.ended, form.timeBasis);
	if (!start) errors.started = 'Indica un inicio válido. Si la hora local se repite o no existe por el cambio horario, usa UTC.';
	if (!end) errors.ended = 'Indica un fin válido. Si la hora local se repite o no existe por el cambio horario, usa UTC.';
	else if (end.getTime() > now + 300_000) errors.ended = 'El trayecto debe haber terminado.';
	if (start && end && end <= start) errors.ended = 'El fin debe ser posterior al inicio.';
	if (form.from.trim().length > 120) errors.from = 'Usa un origen de hasta 120 caracteres.';
	if (form.to.trim().length > 120) errors.to = 'Usa un destino de hasta 120 caracteres.';
	if (codePointLength(form.notes) > 2000) errors.notes = 'La nota admite hasta 2.000 caracteres.';
	else if (hasControlCharacters(form.notes) || hasUnpairedSurrogate(form.notes)) errors.notes = 'La nota no puede contener saltos de línea, caracteres de control ni texto Unicode incompleto.';
	if (form.tags.length > 8) errors.tags = 'Puedes añadir hasta 8 etiquetas.';
	const seenTags = new Set<string>();
	form.tags.forEach((raw, index) => {
		const normalized = raw.normalize('NFC').trim();
		if (!normalized) errors.tags ??= `La etiqueta ${index + 1} no puede estar vacía.`;
		else if (codePointLength(normalized) > 32 || codePointLength(journeyTagKey(normalized)) > 32) errors.tags ??= 'Cada etiqueta admite hasta 32 caracteres.';
		else if (hasControlCharacters(normalized) || hasUnpairedSurrogate(normalized)) errors.tags ??= 'Las etiquetas no pueden contener caracteres de control ni texto Unicode incompleto.';
		else {
			const key = journeyTagKey(normalized);
			if (seenTags.has(key)) errors.tags ??= 'No repitas una etiqueta con distinta combinación de mayúsculas.';
			seenTags.add(key);
		}
	});
	function quantity(key: 'distance' | 'energy', maximum: number): number | null {
		const text = form[key].trim();
		if (!text) return null;
		const parsed = Number(text.replace(',', '.'));
		if (!/^\d+(?:[.,]\d+)?$/.test(text) || !Number.isFinite(parsed) || parsed > maximum) {
			errors[key] = `Indica un valor entre 0 y ${maximum} ${key === 'distance' ? 'km' : 'kWh'}, o déjalo vacío.`;
			return null;
		}
		return parsed;
	}
	const distance = quantity('distance', 50000), energy = quantity('energy', 5000);
	if (form.stops.length > 32) errors.stops = 'Puedes registrar hasta 32 paradas.';
	const stops: JourneyValues['stops'] = [];
	let previousEnd = start?.getTime() ?? 0;
	form.stops.forEach((stop, index) => {
		const old = original?.stops.find(item => stop.started === localJourneyTime(item.started_at, form.timeBasis) && stop.ended === localJourneyTime(item.ended_at, form.timeBasis));
		const left = old ? new Date(old.started_at) : parseTime(stop.started, form.timeBasis);
		const right = old ? new Date(old.ended_at) : parseTime(stop.ended, form.timeBasis);
		const startKey = `stop-${index}-started`, endKey = `stop-${index}-ended`;
		if (!left) errors[startKey] = 'Indica cuándo empezó la parada.';
		if (!right) errors[endKey] = 'Indica cuándo terminó la parada.';
		if (left && right) {
			if (right <= left) errors[endKey] = 'El fin de la parada debe ser posterior al inicio.';
			if (start && end && (left < start || right > end)) errors[startKey] = 'La parada debe estar dentro del trayecto.';
			if (left.getTime() < previousEnd) errors[startKey] = 'Ordena las paradas por hora y evita que se solapen.';
			previousEnd = right.getTime();
			stops.push({ started_at: old?.started_at ?? left.toISOString(), ended_at: old?.ended_at ?? right.toISOString() });
		}
	});
	if (Object.keys(errors).length || !start || !end) return { errors, values: null };
	return { errors, values: { started_at: startIso ?? start.toISOString(), ended_at: endIso ?? end.toISOString(),
		from_label: form.from.trim() || null, to_label: form.to.trim() || null,
		distance_km: distance, energy_kwh: energy, stops,
		notes: form.notes.trim() || null,
		tags: form.tags.map(tag => tag.normalize('NFC').trim()) } };
}
