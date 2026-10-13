<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { ApiError, getCharges, getEnergyAnalytics } from '../lib/api';
	import ChargerPanel from '../lib/ChargerPanel.svelte';
	import EnergyPlanningPanel from '../lib/EnergyPlanningPanel.svelte';
	import Glyph from '../lib/Glyph.svelte';
	import { count, dateTime, euros, localDateTimeValue, number } from '../lib/format';
	import type { ChargeCreationRequest, ChargeQuote, ChargeQuotePayload, ChargeSession, EnergyAnalytics, PageProps, PricingMethod } from '../lib/types';
	type AnalyticsDays = 30 | 90 | 365;
	type EnergySection = 'summary' | 'records' | 'planning';
	const analyticsRanges: AnalyticsDays[] = [30, 90, 365];
	const analyticsSources = [
		{ key: 'manual' as const, label: 'Manual' },
		{ key: 'demo' as const, label: 'Demostración' }
	];
	type LocalCandidate = { instant: string; offsetMinutes: number };
	type OriginalLocalInstant = { localValue: string; occurrence: number; instant: string };
	type ChargeErrorField = 'startedAt' | 'endedAt' | 'kwh' | 'tariff';

	let { drafts, pulseState, busy, online, revision, mutate, quoteCharge, reserveChargeCreation, confirmAction, toast, onSessionExpired }: PageProps = $props();
	const pageSize = 20;
	let charges = $state<ChargeSession[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loadingCharges = $state(false);
	let chargeError = $state('');
	let analyticsDays = $state<AnalyticsDays>(90);
	let analytics = $state<EnergyAnalytics | null>(null);
	let analyticsLoading = $state(false);
	let analyticsError = $state('');
	let analyticsRequest = 0;
	let analyticsRequestKey = '';
	let chargeRequestKey = '';
	let activeSection = $state<EnergySection>('summary');
	let personalDataReady = $state(false);
	let personalDataOnline: boolean | undefined;
	let personalExpanded = $state(untrack(() => pulseState.mode === 'demo'));
	let observedMode = untrack(() => pulseState.mode);
	let editing = $state<ChargeSession | null>(null);
	let startedAt = $state(localDateTimeValue());
	let startOccurrence = $state(0);
	let originalStartedAt = $state<OriginalLocalInstant | null>(null);
	let endedAt = $state('');
	let endOccurrence = $state(0);
	let originalEndedAt = $state<OriginalLocalInstant | null>(null);
	let kwh = $state('');
	let tariff = $state('');
	let chargePricingMethod = $state<PricingMethod>('manual');
	let formError = $state('');
	let formErrorField = $state<ChargeErrorField | null>(null);
	let saveError = $state('');
	let quoteError = $state('');
	let pvpcQuote = $state<ChargeQuote | null>(null);
	let quoteFingerprint = $state('');
	let quoteRequestKey = $state('');
	let quoteBusy = $state(false);
	let savingCharge = $state(false);
	let creationRequest: ChargeCreationRequest | null = $state(null);
	let creationAttempt = '';
	let creationSent = $state(false);
	let quoteRequest = 0;
	let loadPageRequest = 0;
	let editSwitchGeneration = 0;
	let mounted = false;
	let chargeBaseline = $state('');
	let startCandidates = $derived(resolveLocalCandidates(startedAt));
	let endCandidates = $derived(resolveLocalCandidates(endedAt));
	let quoteInputKey = $derived.by(() => currentQuoteInputKey());
	let previousQuoteInput = '';
	let chartSessions = $derived(charges.slice(0, 20).slice().reverse());
	let chartMax = $derived(Math.max(1, ...chartSessions.map((item) => item.kwh)));
	let analyticsMaxMonthlyKwh = $derived(analytics ? maximumMonthlyKwh(analytics.months) : 1);
	let totalPages = $derived(Math.max(1, Math.ceil(total / pageSize)));

	chargeBaseline = chargeFormFingerprint();

	$effect(() => {
		const settingsTariff = String(pulseState.settings.tariff_eur_kwh);
		const settingsMethod = pulseState.settings.pricing_method ?? 'manual';
		if (editing || isChargeDirty()) return;
		tariff = settingsTariff;
		chargePricingMethod = settingsMethod;
		const baseline = chargeFormFingerprint();
		if (chargeBaseline !== baseline) chargeBaseline = baseline;
	});

	$effect(() => {
		const currentMode = pulseState.mode;
		if (currentMode === 'demo' && observedMode !== 'demo') personalExpanded = true;
		observedMode = currentMode;
	});

	$effect(() => {
		const inputKey = quoteInputKey;
		if (previousQuoteInput !== inputKey) {
			previousQuoteInput = inputKey;
			quoteError = '';
		}
		if (quoteRequestKey && quoteRequestKey !== inputKey) {
			quoteRequest += 1;
			quoteRequestKey = '';
			quoteBusy = false;
			quoteError = '';
		}
		if (quoteFingerprint && quoteFingerprint !== inputKey) {
			pvpcQuote = null;
			quoteFingerprint = '';
			quoteError = '';
		}
	});

	$effect(() => {
		const ready = personalDataReady;
		const visible = personalExpanded;
		const summaryVisible = activeSection === 'summary';
		const currentRevision = revision;
		const currentDays = analyticsDays;
		const currentlyOnline = online;
		if (!currentlyOnline) {
			if (personalDataOnline !== false) {
				loadPageRequest += 1;
				charges = [];
				total = 0;
				offset = 0;
				loadingCharges = false;
				chargeError = 'Sin conexión. El historial se carga desde Pulse y no se guarda en este dispositivo.';
				analyticsRequest += 1;
				analytics = null;
				analyticsLoading = false;
				analyticsError = 'Sin conexión. La analítica se carga desde el servidor y no se guarda en este dispositivo.';
			}
			personalDataOnline = false;
			chargeRequestKey = '';
			analyticsRequestKey = '';
			return;
		}
		if (personalDataOnline === false) {
			chargeRequestKey = '';
			analyticsRequestKey = '';
		}
		personalDataOnline = true;
		if (!ready || (!visible && !summaryVisible)) return;
		if (visible) {
			const chargeKey = String(currentRevision);
			if (chargeRequestKey !== chargeKey) {
				chargeRequestKey = chargeKey;
				void loadPage(offset, true);
			}
		}
		const analyticsKey = `${currentRevision}:${currentDays}`;
		if (analyticsRequestKey !== analyticsKey) {
			analyticsRequestKey = analyticsKey;
			void loadAnalytics(currentDays, currentRevision);
		}
	});
	let currentPage = $derived(Math.floor(offset / pageSize) + 1);

	async function loadPage(nextOffset = offset, force = false) {
		if (!mounted || !personalExpanded || !online || (loadingCharges && !force)) return;
		const safeOffset = Math.max(0, Math.floor(nextOffset / pageSize) * pageSize);
		if (!force && safeOffset === offset) return;
		const request = ++loadPageRequest;
		const requestRevision = revision;
		loadingCharges = true;
		chargeError = '';
		try {
			const page = await getCharges(pageSize, safeOffset);
			if (!mounted || request !== loadPageRequest || requestRevision !== revision || !online) return;
			charges = page.items;
			total = page.total;
			offset = page.offset;
		} catch (error) {
			if (!mounted || request !== loadPageRequest || requestRevision !== revision || !online) return;
			if (error instanceof ApiError && error.status === 401) {
				onSessionExpired();
				return;
			}
			chargeError = error instanceof ApiError ? error.message : !navigator.onLine
				? 'No hay conexión con el servidor. Los registros no se guardan en este dispositivo.'
				: 'No se pudo cargar el historial de cargas.';
		} finally {
			if (mounted && request === loadPageRequest) loadingCharges = false;
		}
	}

	async function loadAnalytics(days: AnalyticsDays, requestRevision: number) {
		const request = ++analyticsRequest;
		if (!mounted || (!personalExpanded && activeSection !== 'summary')) return;
		if (!online) {
			analytics = null;
			analyticsLoading = false;
			analyticsError = 'Sin conexión. La analítica se carga desde el servidor y no se guarda en este dispositivo.';
			return;
		}
		analyticsLoading = true;
		analyticsError = '';
		analytics = null;
		try {
			const result = await getEnergyAnalytics(days);
			if (!mounted || request !== analyticsRequest || requestRevision !== revision || days !== analyticsDays || !online) return;
			analytics = result;
		} catch (error) {
			if (!mounted || request !== analyticsRequest || requestRevision !== revision || days !== analyticsDays || !online) return;
			if (error instanceof ApiError && error.status === 401) {
				onSessionExpired();
				return;
			}
			analyticsError = error instanceof ApiError ? error.message : 'No se pudo cargar la analítica de energía.';
		} finally {
			if (mounted && request === analyticsRequest) analyticsLoading = false;
		}
	}

	function chooseAnalyticsDays(days: AnalyticsDays) {
		if (analyticsDays === days) return;
		analyticsRequest += 1;
		analyticsDays = days;
	}

	function retryAnalytics() {
		if (online && !analyticsLoading) void loadAnalytics(analyticsDays, revision);
	}

	function togglePersonalRecords() {
		if (personalExpanded && (busy || savingCharge || quoteBusy)) return;
		personalExpanded = !personalExpanded;
	}

	function activateSection(section: EnergySection) {
		activeSection = section;
		if (section === 'records') personalExpanded = true;
	}

	async function focusChargeForm() {
		activeSection = 'records';
		personalExpanded = true;
		await tick();
		if (mounted) document.getElementById('charge-date')?.focus();
	}

	onMount(() => {
		mounted = true;
		const unregisterDraft = drafts.register('charge', isChargeDirty, () => creationSent);
		personalDataReady = true;
		return () => {
			mounted = false;
			unregisterDraft();
			loadPageRequest += 1;
			analyticsRequest += 1;
			quoteRequest += 1;
			editSwitchGeneration += 1;
		};
	});

	function chargeFormFingerprint(): string {
		return JSON.stringify([
			startedAt,
			startOccurrence,
			originalStartedAt?.localValue ?? null,
			originalStartedAt?.occurrence ?? null,
			originalStartedAt?.instant ?? null,
			endedAt,
			endOccurrence,
			originalEndedAt?.localValue ?? null,
			originalEndedAt?.occurrence ?? null,
			originalEndedAt?.instant ?? null,
			kwh,
			chargePricingMethod,
			editing && chargePricingMethod === 'manual' ? tariff : null
		]);
	}

	function isChargeDirty(): boolean {
		return creationSent || chargeFormFingerprint() !== chargeBaseline;
	}

	function setChargeFieldError(field: ChargeErrorField, message: string, focus = true): null {
		formErrorField = field;
		formError = message;
		saveError = '';
		if (focus) {
			const fieldId = { startedAt: 'charge-date', endedAt: 'charge-end', kwh: 'charge-kwh', tariff: 'charge-tariff' }[field];
			window.setTimeout(() => {
				if (mounted && formErrorField === field && formError === message) document.getElementById(fieldId)?.focus();
			}, 0);
		}
		return null;
	}

	function clearChargeError() {
		formErrorField = null;
		formError = '';
	}

	function onChargeInput() {
		clearChargeError();
		saveError = '';
	}

	function resolveLocalCandidates(value: string): LocalCandidate[] {
		if (!value) return [];
		const base = new Date(value).getTime();
		if (!Number.isFinite(base)) return [];
		const candidates = new Map<number, LocalCandidate>();
		for (let minutes = -120; minutes <= 120; minutes += 30) {
			const date = new Date(base + minutes * 60_000);
			if (localDateTimeValue(date.toISOString()) === value) {
				candidates.set(date.getTime(), { instant: date.toISOString(), offsetMinutes: date.getTimezoneOffset() });
			}
		}
		return [...candidates.values()].sort((left, right) => Date.parse(left.instant) - Date.parse(right.instant));
	}

	function utcOffsetLabel(offsetMinutes: number): string {
		const eastMinutes = -offsetMinutes;
		const sign = eastMinutes >= 0 ? '+' : '-';
		const absolute = Math.abs(eastMinutes);
		const hours = String(Math.floor(absolute / 60)).padStart(2, '0');
		const minutes = String(absolute % 60).padStart(2, '0');
		return `UTC${sign}${hours}:${minutes}`;
	}

	function occurrenceLabel(value: string, candidate: LocalCandidate, index: number, count: number): string {
		const occurrence = count === 2 ? (index === 0 ? 'Primera' : 'Segunda') : `Ocurrencia ${index + 1}`;
		return `${occurrence} · ${value.slice(11, 16)} · ${utcOffsetLabel(candidate.offsetMinutes)}`;
	}

	function occurrenceForInstant(value: string, instant: string): number {
		const targetOffset = new Date(instant).getTimezoneOffset();
		const candidates = resolveLocalCandidates(value);
		const match = candidates.findIndex((candidate) => candidate.offsetMinutes === targetOffset);
		return match >= 0 ? match : 0;
	}

	function resolveInputInstant(value: string, occurrence: number, original: OriginalLocalInstant | null): string | null {
		if (!value) return null;
		if (original && value === original.localValue && occurrence === original.occurrence) return original.instant;
		return resolveLocalCandidates(value)[occurrence]?.instant ?? null;
	}

	function currentQuoteInputKey(): string {
		const startInstant = resolveInputInstant(startedAt, startOccurrence, originalStartedAt);
		const endInstant = endedAt.trim() ? resolveInputInstant(endedAt, endOccurrence, originalEndedAt) : null;
		return [
			chargePricingMethod,
			startInstant ?? `invalid-start:${startedAt}`,
			startOccurrence,
			endInstant ?? `no-end-or-invalid:${endedAt}`,
			endOccurrence,
			String(kwh ?? '')
		].join('|');
	}

	function updateStartAt(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		if (value === startedAt) return;
		startedAt = value;
		startOccurrence = 0;
		originalStartedAt = null;
	}

	function updateEndAt(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		if (value === endedAt) return;
		endedAt = value;
		endOccurrence = 0;
		originalEndedAt = null;
	}

	async function startEdit(item: ChargeSession) {
		const generation = ++editSwitchGeneration;
		if (item.source !== 'manual' || busy || savingCharge) return;
		if (editing?.id === item.id) return;
		if (isChargeDirty()) {
			const draftAtConfirmation = chargeFormFingerprint();
			const accepted = await confirmAction(
				'Descartar cambios de carga',
				creationSent
					? 'El guardado anterior podría haberse completado. Revisa el historial antes de continuar. Se descartará este formulario para editar otra carga.'
					: 'Se descartarán los cambios sin guardar del formulario actual para editar otra carga.',
				'Descartar y editar'
			);
			if (!accepted || !mounted || generation !== editSwitchGeneration || busy || savingCharge || draftAtConfirmation !== chargeFormFingerprint()) return;
		}
		if (!mounted || generation !== editSwitchGeneration || busy || savingCharge) return;
		clearCreationAttempt();
		editing = item;
		startedAt = localDateTimeValue(item.started_at);
		startOccurrence = occurrenceForInstant(startedAt, item.started_at);
		originalStartedAt = { localValue: startedAt, occurrence: startOccurrence, instant: item.started_at };
		endedAt = item.ended_at ? localDateTimeValue(item.ended_at) : '';
		endOccurrence = item.ended_at ? occurrenceForInstant(endedAt, item.ended_at) : 0;
		originalEndedAt = item.ended_at ? { localValue: endedAt, occurrence: endOccurrence, instant: item.ended_at } : null;
		kwh = String(item.kwh);
		chargePricingMethod = item.pricing_method === 'pvpc' || item.pricing?.method === 'pvpc' ? 'pvpc' : 'manual';
		tariff = String(chargePricingMethod === 'manual' ? item.tariff_eur_kwh ?? pulseState.settings.tariff_eur_kwh : pulseState.settings.tariff_eur_kwh);
		clearChargeError();
		saveError = '';
		clearQuote();
		chargeBaseline = chargeFormFingerprint();
		activeSection = 'records';
		personalExpanded = true;
		await tick();
		if (mounted) document.getElementById('charge-date')?.focus();
	}

	function cancelEdit() {
		clearCreationAttempt();
		editing = null;
		startedAt = localDateTimeValue();
		startOccurrence = 0;
		originalStartedAt = null;
		endedAt = '';
		endOccurrence = 0;
		originalEndedAt = null;
		kwh = '';
		tariff = String(pulseState.settings.tariff_eur_kwh);
		chargePricingMethod = pulseState.settings.pricing_method ?? 'manual';
		clearChargeError();
		saveError = '';
		clearQuote();
		chargeBaseline = chargeFormFingerprint();
	}

	function clearCreationAttempt() {
		creationRequest = null;
		creationAttempt = '';
		creationSent = false;
	}

	async function discardUnconfirmedCharge() {
		if (busy || savingCharge || !creationSent) return;
		const requestId = creationRequest?.id;
		const draft = chargeFormFingerprint();
		const accepted = await confirmAction('Descartar formulario de carga',
			'El guardado anterior podría haberse completado. Revisa el historial antes de crear otra carga. Se borrarán los datos del formulario.',
			'Descartar formulario');
		if (!accepted || !mounted || busy || savingCharge || requestId !== creationRequest?.id
			|| draft !== chargeFormFingerprint()) return;
		cancelEdit();
	}

	function clearQuote() {
		quoteRequest += 1;
		quoteRequestKey = '';
		quoteFingerprint = '';
		pvpcQuote = null;
		quoteBusy = false;
		quoteError = '';
	}

	type ValidatedCharge = { startedIso: string; endedIso: string | null; energy: number; fingerprint: string };

	function hasNumericInput(value: unknown): boolean {
		return String(value ?? '').trim() !== '';
	}

	function validateCharge(focusInvalid = true): ValidatedCharge | null {
		clearChargeError();
		if (!startedAt || Number.isNaN(new Date(startedAt).getTime())) {
			return setChargeFieldError('startedAt', 'Indica una fecha y una hora de inicio válidas.', focusInvalid);
		}
		const startedIso = resolveInputInstant(startedAt, startOccurrence, originalStartedAt);
		if (!startedIso) {
			return setChargeFieldError('startedAt', 'Esa hora de inicio no existe en tu zona por el cambio al horario de verano. Elige otra hora.', focusInvalid);
		}
		const energy = Number(kwh);
		if (!hasNumericInput(kwh) || !Number.isFinite(energy) || energy <= 0 || energy > 500) {
			return setChargeFieldError('kwh', 'La energía debe ser mayor que 0 y no superar 500 kWh.', focusInvalid);
		}
		const hasEnd = endedAt.trim() !== '';
		if (chargePricingMethod === 'pvpc' && !hasEnd) {
			return setChargeFieldError('endedAt', 'Indica la hora de fin para calcular el coste PVPC.', focusInvalid);
		}
		let endedIso: string | null = null;
		if (hasEnd) {
			if (Number.isNaN(new Date(endedAt).getTime())) {
				return setChargeFieldError('endedAt', 'Indica una fecha y una hora de fin válidas.', focusInvalid);
			}
			endedIso = resolveInputInstant(endedAt, endOccurrence, originalEndedAt);
			if (!endedIso) {
				return setChargeFieldError('endedAt', 'Esa hora de fin no existe en tu zona por el cambio al horario de verano. Elige otra hora.', focusInvalid);
			}
			const startMs = Date.parse(startedIso);
			const endMs = Date.parse(endedIso);
			if (endMs <= startMs) {
				return setChargeFieldError('endedAt', 'La hora de fin debe ser posterior al inicio.', focusInvalid);
			}
			if (endMs - startMs > 72 * 60 * 60 * 1000) {
				return setChargeFieldError('endedAt', 'La sesión no puede superar las 72 horas.', focusInvalid);
			}
		}
		return { startedIso, endedIso, energy, fingerprint: quoteInputKey };
	}

	async function requestQuote(values = validateCharge(true)): Promise<ChargeQuote | null> {
		if (!mounted || !values) return null;
		if (chargePricingMethod !== 'pvpc' || !values.endedIso) return null;
		if (!online) {
			quoteError = 'Conéctate para consultar el precio oficial y calcular esta carga.';
			return null;
		}
		const request = ++quoteRequest;
		quoteRequestKey = values.fingerprint;
		quoteFingerprint = '';
		pvpcQuote = null;
		quoteBusy = true;
		quoteError = '';
		const payload: ChargeQuotePayload = {
			started_at: values.startedIso,
			ended_at: values.endedIso,
			kwh: values.energy,
			pricing_method: 'pvpc'
		};
		try {
			const result = await quoteCharge(payload);
			if (!mounted || request !== quoteRequest || values.fingerprint !== quoteInputKey) return null;
			if (result.pricing.intervals.length === 0) {
				quoteError = 'Red Eléctrica no devolvió tramos oficiales para este período. El formulario sigue intacto; inténtalo de nuevo más tarde.';
				return null;
			}
			pvpcQuote = result;
			quoteFingerprint = values.fingerprint;
			return result;
		} catch (error) {
			if (mounted && request === quoteRequest && values.fingerprint === quoteInputKey) {
				quoteError = error instanceof ApiError ? error.message : 'No se pudo calcular el coste. El formulario sigue intacto; puedes volver a intentarlo.';
			}
			return null;
		} finally {
			if (mounted && request === quoteRequest) {
				quoteBusy = false;
				quoteRequestKey = '';
			}
		}
	}

	async function saveCharge(event: SubmitEvent) {
		event.preventDefault();
		const form = event.currentTarget as HTMLFormElement;
		const previousFocus = document.activeElement instanceof HTMLElement && form.contains(document.activeElement)
			? document.activeElement : null;
		const values = validateCharge(true);
		if (!values) return;
		const appliedTariff = Number(tariff);
		if (chargePricingMethod === 'manual' && (!hasNumericInput(tariff) || !Number.isFinite(appliedTariff) || appliedTariff < 0 || appliedTariff > 5)) {
			setChargeFieldError('tariff', 'La tarifa manual debe estar entre 0 y 5 €/kWh.');
			return;
		}
		if (!mounted || busy || savingCharge) return;
		const payload = {
			started_at: values.startedIso,
			kwh: values.energy,
			pricing_method: chargePricingMethod,
			...(editing ? { ended_at: values.endedIso } : values.endedIso ? { ended_at: values.endedIso } : {}),
			...(editing && chargePricingMethod === 'manual' ? { tariff_eur_kwh: appliedTariff } : {})
		};
		const attempt = JSON.stringify(payload);
		if (!editing && creationSent && attempt !== creationAttempt) {
			saveError = 'No está confirmado el guardado anterior. Revisa el historial antes de iniciar otra carga o vuelve a los datos anteriores para reintentarla.';
			return;
		}
		const draft = chargeFormFingerprint();
		const editingId = editing?.id;
		const path = editing ? `/api/charges/${encodeURIComponent(editing.id)}` : '/api/charges';
		const previousOffset = offset;
		savingCharge = true;
		quoteError = '';
		saveError = '';
		try {
			// A retry of an uncertain creation must reach the server's stored result,
			// even if obtaining a new PVPC preview is currently unavailable.
			if (chargePricingMethod === 'pvpc' && !creationSent && (!pvpcQuote || quoteFingerprint !== values.fingerprint)) {
				const quote = await requestQuote(values);
				if (!mounted || !quote) return;
			}
			if (!editingId && !creationRequest) {
				const reservation = await reserveChargeCreation();
				if (!mounted) return;
				if (draft !== chargeFormFingerprint()) {
					saveError = 'Los datos cambiaron mientras se preparaba el guardado. Revisa el formulario y vuelve a guardarlo.';
					return;
				}
				creationRequest = reservation;
				creationAttempt = attempt;
			}
			if (!mounted) return;
			if (draft !== chargeFormFingerprint() || editingId !== editing?.id) {
				saveError = 'Los datos cambiaron mientras se preparaba el guardado. Revisa el formulario y vuelve a guardarlo.';
				return;
			}
			if (!editingId) {
				creationSent = true;
				offset = 0;
			}
			const data = editingId ? payload : { ...payload, request_id: creationRequest!.id };
			const saved = await mutate(path, editingId ? 'PUT' : 'POST', data, editingId ? 'La carga se ha actualizado.' : 'La carga se ha guardado.');
			if (!mounted) return;
			if (saved) cancelEdit();
			else {
				saveError = 'No se ha podido confirmar el guardado. El formulario sigue intacto. Revisa el historial antes de reintentarlo.';
				offset = previousOffset;
			}
		} catch {
			if (!mounted) return;
			saveError = !editingId && !creationSent
				? 'No se pudo preparar el guardado. No se envió la carga. El formulario sigue intacto; vuelve a intentarlo cuando haya conexión.'
				: 'No se ha podido confirmar el guardado. El formulario sigue intacto. Revisa el historial antes de reintentarlo.';
			offset = previousOffset;
		} finally {
			if (mounted) {
				savingCharge = false;
				await tick();
				// Restore only focus lost when the submitting control was disabled.
				if (mounted && saveError && document.hasFocus() && document.activeElement === document.body && previousFocus?.isConnected) {
					previousFocus.focus({ preventScroll: true });
				}
			}
		}
	}

	function formatInstantWithOffset(value: string): string {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('es-ES', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'shortOffset' }).format(date);
	}

	function durationLabel(started: string, ended: string): string {
		const minutes = Math.max(0, Math.round((new Date(ended).getTime() - new Date(started).getTime()) / 60_000));
		const hours = Math.floor(minutes / 60);
		const remainder = minutes % 60;
		if (hours && remainder) return `${hours} h ${remainder} min`;
		if (hours) return `${hours} h`;
		return `${remainder} min`;
	}

	function quoteDurationLabel(quote: ChargeQuote): string {
		const intervals = quote.pricing.intervals;
		if (!intervals.length) return '—';
		return durationLabel(intervals[0].started_at, intervals[intervals.length - 1].ended_at);
	}

	function isPvpcCharge(item: ChargeSession): boolean {
		return item.pricing_method === 'pvpc' || item.pricing?.method === 'pvpc';
	}

	async function deleteCharge(item: ChargeSession) {
		if (item.source !== 'manual') return;
		const accepted = await confirmAction(
			'Eliminar carga manual',
			`Se eliminará el registro del ${dateTime(item.started_at)}. Esta operación no se puede deshacer.`,
			'Eliminar carga'
		);
		if (!accepted || !mounted) return;
		const previousOffset = offset;
		offset = Math.max(0, Math.min(offset, Math.floor(Math.max(0, total - 2) / pageSize) * pageSize));
		const removed = await mutate(`/api/charges/${encodeURIComponent(item.id)}`, 'DELETE', undefined, 'La carga se ha eliminado.');
		if (!mounted) return;
		if (removed) {
			if (editing?.id === item.id) cancelEdit();
		} else offset = previousOffset;
	}

	function chartHeight(item: ChargeSession): number {
		return Math.max(5, Math.round((item.kwh / chartMax) * 88));
	}

	function analyticsWindowDate(value: string): string {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date);
	}

	function monthLabel(value: string): string {
		if (!/^\d{4}-\d{2}$/.test(value)) return value;
		const date = new Date(`${value}-01T00:00:00.000Z`);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat('es-ES', { month: 'short', year: '2-digit', timeZone: 'UTC' }).format(date);
	}

	function observedAtUtc(value: string | null): string {
		if (!value) return '—';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return '—';
		return `${new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date)} UTC`;
	}

	function maximumMonthlyKwh(months: EnergyAnalytics['months']): number {
		return Math.max(1, ...months.flatMap((month) => [month.manual.kwh, month.demo.kwh]));
	}

	function effectiveTariff(value: number | null): string {
		return value === null ? '—' : `${euros(value)} / kWh`;
	}

	function gapDuration(seconds: number | null): string {
		if (seconds === null) return '—';
		if (seconds < 60) return `${number(seconds)} s`;
		if (seconds < 3600) return `${number(seconds / 60)} min`;
		return `${number(seconds / 3600)} h`;
	}
</script>

<header class="premium-heading energy-intro">
	<div>
		<h2>Energía</h2>
		<p>Cargas y costes registrados.</p>
	</div>
</header>

<nav class="section-switcher energy-switcher" aria-label="Secciones de energía">
	<button type="button" aria-pressed={activeSection === 'summary'} aria-controls="energy-summary-view" onclick={() => activateSection('summary')}>Resumen</button>
	<button type="button" aria-pressed={activeSection === 'records'} aria-controls="energy-records-view" onclick={() => activateSection('records')}>Registros</button>
	<button type="button" aria-pressed={activeSection === 'planning'} aria-controls="energy-planning-view" onclick={() => activateSection('planning')}>Planificar</button>
</nav>

<section id="energy-summary-view" class="energy-view energy-summary-view" aria-label="Resumen de energía" hidden={activeSection !== 'summary'}>
	<section class="energy-totals" aria-label="Totales acumulados de cargas manuales">
	{#if online}
		<article class="energy-total-group energy-total-group-manual" aria-labelledby="manual-totals-title">
			<header class="energy-summary-heading">
				<div class="energy-total-group-heading"><span class="source-dot source-manual" aria-hidden="true"></span><h3 id="manual-totals-title">Manual</h3></div>
				<button class="button button-primary" type="button" onclick={focusChargeForm}>Añadir carga</button>
			</header>
			<dl class="energy-total-metrics">
				<div class="energy-metric-primary"><dt>Energía registrada</dt><dd>{#if pulseState.energy.totals_by_source?.manual?.kwh == null}Sin datos{:else}{number(pulseState.energy.totals_by_source.manual.kwh)} <small>kWh</small>{/if}</dd></div>
				<div class="energy-metric-cost"><dt>Coste guardado</dt><dd>{pulseState.energy.totals_by_source?.manual?.cost_eur == null ? 'Sin datos' : euros(pulseState.energy.totals_by_source.manual.cost_eur)}</dd></div>
			</dl>
			<p class="energy-total-context">Acumulado de todos tus registros manuales.</p>
			{#if pulseState.mode === 'demo'}
				{@const demoTotals = pulseState.energy.totals_by_source?.demo}
				<details class="demo-total-disclosure">
					<summary><span>Datos de demostración</span><small>Ejemplos ficticios, separados de tus cargas</small></summary>
					<div class="demo-total-content">
						<h3 id="demo-totals-title"><span class="source-dot source-demo" aria-hidden="true"></span>Demostración</h3>
						<dl class="energy-total-metrics energy-demo-metrics">
							<div><dt>Energía registrada</dt><dd>{demoTotals?.kwh == null ? 'Sin datos' : `${number(demoTotals.kwh)} kWh`}</dd></div>
							<div><dt>Coste guardado</dt><dd>{demoTotals?.cost_eur == null ? 'Sin datos' : euros(demoTotals.cost_eur)}</dd></div>
							<div><dt>Distancia simulada</dt><dd>{#if pulseState.journeys.length > 0 && typeof demoTotals?.distance_km === 'number' && Number.isFinite(demoTotals.distance_km)}{number(demoTotals.distance_km)} km{:else}Sin datos{/if}</dd></div>
							<div><dt>Consumo calculado de trayectos demo</dt><dd>{#if pulseState.journeys.length > 0 && typeof demoTotals?.distance_km === 'number' && demoTotals.distance_km > 0 && typeof demoTotals.consumption_kwh_100km === 'number' && Number.isFinite(demoTotals.consumption_kwh_100km)}{number(demoTotals.consumption_kwh_100km)} kWh/100 km{:else}Sin datos{/if}</dd></div>
						</dl>
						<p class="demo-trip-note">Estos valores proceden del diario ficticio de Pulse; no representan el consumo real del vehículo.</p>
					</div>
				</details>
			{/if}
		</article>
	{:else}
		<p class="energy-totals-offline" role="status">Conéctate para consultar los totales guardados en Pulse.</p>
	{/if}
</section>

<section class="analytics-panel" aria-labelledby="analytics-title">
	<div class="analytics-heading">
		<div class="analytics-title-copy"><h2 id="analytics-title">Energía y coste por período</h2><p>Manual y demostración; las lecturas del vehículo se muestran aparte.</p></div>
		<div class="analytics-range-control" role="group" aria-label="Período de analítica">
			{#each analyticsRanges as days (days)}
				<button class="analytics-range-button" class:analytics-range-selected={analyticsDays === days} type="button" aria-pressed={analyticsDays === days} disabled={!online} onclick={() => chooseAnalyticsDays(days)}>{days} días</button>
			{/each}
		</div>
	</div>

	{#if analyticsLoading && !analytics}
		<div class="analytics-loading" role="status"><span class="loading-dot"></span> Cargando analítica de {analyticsDays} días…</div>
	{:else if analyticsError && !analytics}
		<div class="analytics-error" role="alert"><div><strong>{online ? 'No se pudo cargar la analítica' : 'Analítica no disponible sin conexión'}</strong><p>{analyticsError}</p></div><button class="button button-secondary" type="button" disabled={!online || analyticsLoading} onclick={retryAnalytics}>{analyticsLoading ? 'Cargando…' : online ? 'Reintentar' : 'Sin conexión'}</button></div>
	{:else if analytics}
		<div class="analytics-window"><span>Período UTC: {analyticsWindowDate(analytics.window_start)} – {analyticsWindowDate(analytics.window_end)}</span><span>Agrupación mensual en UTC</span>{#if analyticsLoading}<span role="status">Actualizando…</span>{/if}</div>
		<div class="analytics-source-grid" role="group" aria-label="Sesiones de carga por origen">
			{#each analyticsSources as source (source.key)}
				{@const aggregate = analytics[source.key]}
				<article class="analytics-source-card" class:analytics-source-demo={source.key === 'demo'} aria-labelledby={`analytics-${source.key}-title`}>
					<div class="analytics-source-heading"><h3 id={`analytics-${source.key}-title`}><i class:source-demo={source.key === 'demo'} class="source-dot"></i>{source.label}</h3><span class="badge" class:badge-demo={source.key === 'demo'}>{count(aggregate.sessions)} sesiones</span></div>
					{#if aggregate.sessions > 0}
						<div class="analytics-metrics">
							<div><span>Energía cargada</span><strong>{number(aggregate.kwh)} <small>kWh</small></strong></div>
							<div><span>Coste</span><strong>{euros(aggregate.cost_eur)}</strong></div>
							<div><span>Tarifa efectiva</span><strong>{effectiveTariff(aggregate.effective_tariff_eur_kwh)}</strong></div>
						</div>
					{:else}
						<p class="analytics-empty-source">Sin registros {source.key === 'manual' ? 'manuales' : 'de demostración'} en este período.</p>
					{/if}
				</article>
			{/each}
		</div>

		<section class="analytics-months" aria-labelledby="monthly-title">
			<div class="analytics-subheading"><div><h3 id="monthly-title">Energía por mes</h3></div><span class="badge">Meses UTC</span></div>
			<p class="analytics-explainer">Cada barra suma sesiones del origen indicado. No se rellenan meses ni huecos con estimaciones.</p>
			{#if analytics.months.length === 0 || analytics.months.every((month) => month.manual.sessions === 0 && month.demo.sessions === 0)}
				<div class="analytics-empty-chart"><span class="analytics-empty-mark"><Glyph name="energy" size={20} /></span><strong>Sin registros en esta ventana</strong><span>No hay sesiones manuales o de demostración para representar.</span></div>
			{:else}
				<ol class="month-chart" aria-label="Kilovatios hora por mes UTC, separados por origen">
					{#each analytics.months as month (month.month)}
						<li class="month-chart-row">
							<strong class="month-chart-label">{monthLabel(month.month)}</strong>
							<div class="month-origin-group">
								<div class="month-origin-row"><span>Manual</span><progress class="month-bar month-bar-manual" value={month.manual.kwh} max={analyticsMaxMonthlyKwh} aria-label={`Manual, ${monthLabel(month.month)}: ${number(month.manual.kwh)} kWh`}></progress><strong>{number(month.manual.kwh)} kWh</strong></div>
								<dl class="month-origin-metrics" aria-label={`Agregados mensuales de Manual, ${monthLabel(month.month)}`}>
									<div><dt>Sesiones</dt><dd>{count(month.manual.sessions)}</dd></div>
									<div><dt>Coste</dt><dd>{euros(month.manual.cost_eur)}</dd></div>
									<div><dt>Tarifa efectiva</dt><dd>{effectiveTariff(month.manual.effective_tariff_eur_kwh)}</dd></div>
								</dl>
							</div>
							<div class="month-origin-group">
								<div class="month-origin-row"><span>Demo</span><progress class="month-bar month-bar-demo" value={month.demo.kwh} max={analyticsMaxMonthlyKwh} aria-label={`Demo, ${monthLabel(month.month)}: ${number(month.demo.kwh)} kWh`}></progress><strong>{number(month.demo.kwh)} kWh</strong></div>
								<dl class="month-origin-metrics" aria-label={`Agregados mensuales de Demo, ${monthLabel(month.month)}`}>
									<div><dt>Sesiones</dt><dd>{count(month.demo.sessions)}</dd></div>
									<div><dt>Coste</dt><dd>{euros(month.demo.cost_eur)}</dd></div>
									<div><dt>Tarifa efectiva</dt><dd>{effectiveTariff(month.demo.effective_tariff_eur_kwh)}</dd></div>
								</dl>
							</div>
						</li>
					{/each}
				</ol>
			{/if}
		</section>

		<section class="analytics-coverage" aria-labelledby="coverage-title">
			<div class="analytics-coverage-heading"><h3 id="coverage-title">Lecturas del vehículo</h3><span class="badge">Sin estimación</span></div>
			<p class="analytics-explainer">La cobertura describe muestras recibidas. No identifica conducción, trayectos ni consumo del vehículo.</p>
			{#if analytics.observations.observed_count === 0}
				<div class="analytics-observations-empty">{analytics.observations.sample_count === 0 ? 'No hay muestras retenidas.' : 'No hay observaciones válidas en esta ventana; las muestras retenidas pueden quedar fuera del período o no tener una hora válida.'}</div>
			{/if}
			<details class="analytics-coverage-details">
				<summary>Ver detalles de las lecturas</summary>
				<div class="analytics-coverage-content">
					<p class="analytics-retention-note">Retención limitada a las últimas {count(analytics.observations.retention_limit)} muestras como máximo. Muestras, ausencias de observación y diagnósticos de timestamps abarcan esa retención; observaciones válidas y cobertura corresponden al período seleccionado.</p>
					<div class="coverage-metrics">
						<div><span>Muestras retenidas</span><strong>{count(analytics.observations.sample_count)}</strong></div>
						<div><span>Observadas</span><strong>{count(analytics.observations.observed_count)}</strong></div>
						<div><span>Antes del período</span><strong>{count(analytics.observations.outside_window_count)}</strong></div>
						<div><span>Muestras sin observación</span><strong>{count(analytics.observations.missing_observation_count)}</strong></div>
						<div><span>Huecos detectados</span><strong>{count(analytics.observations.gap_count)}</strong></div>
						<div><span>Cobertura temporal</span><strong>{analytics.observations.coverage_percent === null ? '—' : `${number(analytics.observations.coverage_percent)}%`}</strong></div>
						<div><span>Hueco máximo</span><strong>{gapDuration(analytics.observations.max_gap_seconds)}</strong></div>
						<div class="coverage-last-observation"><span>Última observación</span><strong>{observedAtUtc(analytics.observations.last_observed_at)}</strong></div>
					</div>
					<div class="analytics-technical-details" aria-label="Análisis técnico no cualificado">
						<strong>Análisis técnico no cualificado</strong>
						<dl>
							<div><dt>Tiempo cubierto por lecturas</dt><dd>{gapDuration(analytics.observations.covered_seconds)}</dd></div>
							<div><dt>Primera observación</dt><dd>{observedAtUtc(analytics.observations.first_observed_at)}</dd></div>
							<div><dt>Variación de odómetro comunicada</dt><dd>{analytics.observations.odometer_delta_km === null ? 'No disponible' : `${number(analytics.observations.odometer_delta_km)} km`}</dd></div>
							<div><dt>Intervalos candidatos elegibles</dt><dd>{count(analytics.observations.eligible_interval_count)}</dd></div>
							<div><dt>Validación</dt><dd>No cualificado</dd></div>
						</dl>
						<p>Estos valores describen lecturas y candidatos de intervalos. No acreditan un viaje, consumo del vehículo ni una sesión de carga validada.</p>
					</div>
					<div class="analytics-quality-details">Diagnósticos en la retención — Posteriores al período: {count(analytics.observations.future_timestamp_count)} <span aria-hidden="true">·</span> No monótonos: {count(analytics.observations.non_monotonic_count)} <span aria-hidden="true">·</span> Duplicados: {count(analytics.observations.duplicate_time_count)}</div>
				</div>
			</details>
			<div class="analytics-consumption"><strong>—</strong><span><b>Consumo del vehículo</b><small>Energía del vehículo aún no observada.</small></span></div>
		</section>
	{/if}
</section>
</section>
<section id="energy-records-view" class="energy-view records-view" aria-labelledby="personal-records-title" hidden={activeSection !== 'records'}>
<section class="energy-charger-disclosure" aria-label="Sesiones importadas del cargador">
	<details class="charger-disclosure workspace-panel">
		<summary><span><strong>Estado del cargador y sesiones evcc</strong><small>Datos comunicados por la integración del servidor</small></span><span class="charger-disclosure-action">Consultar</span></summary>
		<div class="energy-session-layout">
			<div class="energy-charger-column">
				<ChargerPanel {online} {busy} {revision} {quoteCharge} {onSessionExpired} pricingMethod={pulseState.settings.pricing_method ?? 'manual'} />
			</div>
			<aside class="energy-cost-profile" aria-labelledby="energy-cost-profile-title">
				<h3 id="energy-cost-profile-title">Coste y procedencia</h3>
				<p>Las sesiones importadas vienen de evcc. Las cargas personales mantienen su origen y método de precio.</p>
				<dl class="energy-cost-facts">
					<div><dt>Precio para nuevas cargas manuales</dt><dd>{pulseState.settings.pricing_method === 'pvpc' ? 'PVPC · Península' : 'Precio fijo'}</dd></div>
					{#if pulseState.settings.pricing_method !== 'pvpc'}<div><dt>Tarifa configurada</dt><dd>{euros(pulseState.settings.tariff_eur_kwh)} / kWh</dd></div>{/if}
				</dl>
				<a class="energy-settings-link" href="/settings">Revisar tarifa en Ajustes</a>
			</aside>
		</div>
	</details>
</section>

<section class="personal-records" aria-labelledby="personal-records-title">
	<div class="personal-records-heading">
		<div>
			<p class="personal-records-kicker"><Glyph name="charge" size={14} /> HISTORIAL PERSONAL</p>
			<h2 id="personal-records-title">Registros personales</h2>
			<p>Historial manual y de demostración, separado de las sesiones importadas del cargador.</p>
		</div>
		<div class="personal-records-actions">
			<button class="button button-secondary personal-toggle" type="button" aria-expanded={personalExpanded} aria-controls="energy-personal-content" disabled={personalExpanded && (busy || savingCharge || quoteBusy)} onclick={togglePersonalRecords}>{personalExpanded ? 'Ocultar registros' : 'Mostrar registros'}</button>
			{#if !editing}<button class="button button-primary personal-register" type="button" onclick={focusChargeForm}>Registrar carga</button>{/if}
		</div>
		</div>
		<div id="energy-personal-content" class="energy-personal-content" hidden={!personalExpanded}>
	<section class="energy-main-grid">
	<div class="energy-history-column">
		<div class="section-heading"><div><h2>Cargas manuales y de demostración</h2><p class="energy-section-copy">Cada registro conserva su fecha, energía, coste y fuente.</p></div></div>
		{#if loadingCharges}
			<div class="card loading-card" role="status">Cargando cargas del servidor…</div>
		{:else if chargeError}
			<div class="empty-state"><span class="empty-mark"><Glyph name="energy" /></span><h3>No se pudo abrir el historial</h3><p>{chargeError}</p><button class="button button-secondary" type="button" disabled={!online || loadingCharges} onclick={() => loadPage(offset, true)}>Reintentar</button></div>
		{:else if charges.length === 0}
			<div class="empty-state"><span class="empty-mark"><Glyph name="energy" /></span><h3>Todavía no hay cargas registradas</h3><p>Puedes añadir una carga manual en cualquier momento. No es necesario conectar un vehículo.</p></div>
		{:else}
			<article class="card chart-card evolution-chart">
				<div class="chart-heading"><div><h3>Comparación por sesión</h3><p>kWh por carga en esta página del historial</p></div><div class="chart-legend" role="group" aria-label="Leyenda del gráfico"><span><i class="source-dot"></i>Manual</span><span><i class="source-dot source-demo"></i>Demo</span></div></div>
				<svg class="charge-chart" viewBox="0 0 640 136" role="img" aria-label={`Gráfico de ${count(chartSessions.length)} sesiones de carga, comparadas por kilovatios hora`}>
					<line class="chart-axis" x1="10" y1="130" x2="630" y2="130" />
					{#each chartSessions as item, index (item.id)}
						<rect class="chart-bar" class:chart-bar-demo={item.source !== 'manual'} x={19 + index * 30} y={130 - chartHeight(item)} width="15" height={chartHeight(item)} rx="4"><title>{item.source === 'manual' ? 'Manual' : 'Demostración'} · {dateTime(item.started_at)} · {number(item.kwh)} kWh</title></rect>
					{/each}
				</svg>
				<div class="chart-indices" aria-hidden="true">
					{#each chartSessions as item, index (item.id)}
						{#if index % 5 === 0 || index === chartSessions.length - 1}<span style:left={`${(26.5 + index * 30) / 6.4}%`}>{count(index + 1)}</span>{/if}
					{/each}
				</div>
			</article>

			<div class="charge-list card">
				{#each charges as item (item.id)}
					<article class="charge-row">
						<div class="charge-mark"><Glyph name="charge" size={17} /></div>
						<div class="charge-row-main"><strong>{dateTime(item.started_at)}</strong><span>{number(item.kwh)} kWh <i aria-hidden="true">·</i> {euros(item.cost_eur)}</span></div>
						<span class="badge" class:badge-demo={item.source !== 'manual'}>{item.source !== 'manual' ? 'Demo' : isPvpcCharge(item) ? 'PVPC estimado' : 'Manual'}</span>
						<details class="saved-charge-details">
							<summary>Detalles del registro</summary>
							<dl>
								<div><dt>Origen</dt><dd>{item.source === 'manual' ? 'Registro manual' : 'Demostración'}</dd></div>
								<div><dt>Fin registrado</dt><dd>{item.ended_at ? formatInstantWithOffset(item.ended_at) : 'No indicado'}</dd></div>
								<div><dt>Método de precio guardado</dt><dd>{item.pricing_method === 'pvpc' ? 'PVPC estimado' : item.pricing_method === 'manual' ? 'Precio fijo manual' : 'No indicado en el registro'}</dd></div>
								{#if !isPvpcCharge(item)}<div><dt>Tarifa histórica aplicada</dt><dd>{item.tariff_eur_kwh === null ? 'No incluida en este registro' : `${new Intl.NumberFormat('es-ES', { maximumFractionDigits: 6 }).format(item.tariff_eur_kwh)} €/kWh`}</dd></div>{/if}
							</dl>
						</details>
						{#if isPvpcCharge(item)}
							<details class="saved-pricing-details">
								<summary>Ver desglose PVPC estimado</summary>
								{#if item.pricing?.method === 'pvpc'}
									<p>Precios de {item.pricing.source} · Península · sin impuestos ni potencia fija. Reparto uniforme estimado de energía.</p>
									<ul class="pricing-intervals">
										{#each item.pricing.intervals as interval, intervalIndex (`${item.id}-${intervalIndex}`)}
											<li><span><strong>Tramo {intervalIndex + 1}</strong><small>{formatInstantWithOffset(interval.started_at)} → {formatInstantWithOffset(interval.ended_at)}</small><small>{number(interval.kwh)} kWh · {euros(interval.price_eur_kwh)} / kWh · Precio consultado: {formatInstantWithOffset(interval.price_fetched_at)}</small></span><b>{euros(interval.cost_eur)}</b></li>
										{/each}
									</ul>
								{:else}
									<p>El servidor no incluyó el desglose por tramos para esta carga.</p>
								{/if}
							</details>
						{/if}
						{#if item.source === 'manual'}
							<div class="charge-actions"><button class="text-button" type="button" disabled={busy} onclick={() => startEdit(item)} aria-label={`Editar carga del ${dateTime(item.started_at)}`}>Editar</button><button class="text-button danger-text" type="button" disabled={busy} onclick={() => deleteCharge(item)} aria-label={`Eliminar carga del ${dateTime(item.started_at)}`}>Eliminar</button></div>
						{/if}
					</article>
				{/each}
				{#if total > pageSize}
					<div class="pagination">
						<span>Página {currentPage} de {totalPages}</span>
						<div><button class="button button-small button-quiet" type="button" disabled={offset === 0 || loadingCharges} onclick={() => loadPage(Math.max(0, offset - pageSize))}>Anterior</button><button class="button button-small button-quiet" type="button" disabled={offset + charges.length >= total || loadingCharges} onclick={() => loadPage(offset + pageSize)}>Siguiente</button></div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<aside class="charge-form-card card" aria-labelledby="charge-form-title">
		<div class="section-heading"><div><h2 id="charge-form-title">{editing ? 'Editar carga' : 'Añadir una carga'}</h2><p class="energy-section-copy">Guarda energía y coste con el método que corresponda.</p></div><span class="form-icon"><Glyph name={editing ? 'settings' : 'plus'} size={18} /></span></div>
		<p class="muted-copy">Elige el precio fijo o una estimación PVPC por hora. Las cargas manuales permanecen identificadas como tales.</p>
		{#if !online}<p class="offline-charge-note" role="status">Sin conexión. La cotización y el guardado requieren Pulse; el formulario no conserva datos en este dispositivo.</p>{/if}
		<form class="charge-form" onsubmit={saveCharge} oninput={onChargeInput} onchange={onChargeInput}>
			<div class="field"><label for="charge-method">Método de precio</label><select id="charge-method" bind:value={chargePricingMethod} disabled={busy || savingCharge}><option value="manual">Precio fijo manual</option><option value="pvpc">PVPC automático · Península</option></select></div>
			<div class="field"><label for="charge-date">Inicio</label><input id="charge-date" type="datetime-local" value={startedAt} oninput={updateStartAt} required disabled={busy || savingCharge} aria-invalid={formErrorField === 'startedAt' ? 'true' : undefined} aria-describedby={formErrorField === 'startedAt' ? 'charge-date-error' : undefined} />{#if formErrorField === 'startedAt'}<p id="charge-date-error" class="form-error" role="alert">{formError}</p>{/if}</div>
			{#if startCandidates.length > 1}<div class="field occurrence-field"><label for="charge-start-occurrence">Ocurrencia del inicio</label><select id="charge-start-occurrence" bind:value={startOccurrence} disabled={busy || savingCharge}>{#each startCandidates as candidate, index}<option value={index}>{occurrenceLabel(startedAt, candidate, index, startCandidates.length)}</option>{/each}</select></div>{/if}
			<div class="field"><label for="charge-end">Fin{chargePricingMethod === 'pvpc' ? '' : ' (opcional)'}</label><input id="charge-end" type="datetime-local" value={endedAt} oninput={updateEndAt} required={chargePricingMethod === 'pvpc'} disabled={busy || savingCharge} aria-invalid={formErrorField === 'endedAt' ? 'true' : undefined} aria-describedby={formErrorField === 'endedAt' ? 'charge-end-error' : undefined} /><p class="field-hint">{chargePricingMethod === 'pvpc' ? 'Necesario para repartir la energía entre los precios horarios.' : 'Puedes añadirlo para conservar la duración de la sesión.'}</p>{#if formErrorField === 'endedAt'}<p id="charge-end-error" class="form-error" role="alert">{formError}</p>{/if}</div>
			{#if endCandidates.length > 1}<div class="field occurrence-field"><label for="charge-end-occurrence">Ocurrencia del fin</label><select id="charge-end-occurrence" bind:value={endOccurrence} disabled={busy || savingCharge}>{#each endCandidates as candidate, index}<option value={index}>{occurrenceLabel(endedAt, candidate, index, endCandidates.length)}</option>{/each}</select></div>{/if}
			<div class="field"><label for="charge-kwh">Energía cargada</label><div class="input-with-unit"><input id="charge-kwh" type="number" min="0" max="500" step="any" inputmode="decimal" bind:value={kwh} required disabled={busy || savingCharge} aria-invalid={formErrorField === 'kwh' ? 'true' : undefined} aria-describedby={formErrorField === 'kwh' ? 'charge-kwh-error' : undefined} /><span>kWh</span></div>{#if formErrorField === 'kwh'}<p id="charge-kwh-error" class="form-error" role="alert">{formError}</p>{/if}</div>
			{#if chargePricingMethod === 'manual'}
				{#if editing}
					<div class="field"><label for="charge-tariff">Tarifa manual aplicada</label><div class="input-with-unit"><input id="charge-tariff" type="number" min="0" max="5" step="0.001" inputmode="decimal" bind:value={tariff} required disabled={busy || savingCharge} aria-invalid={formErrorField === 'tariff' ? 'true' : undefined} aria-describedby={formErrorField === 'tariff' ? 'charge-tariff-error' : undefined} /><span>€/kWh</span></div><p class="field-hint">El coste guardado se recalculará con esta tarifa fija.</p>{#if formErrorField === 'tariff'}<p id="charge-tariff-error" class="form-error" role="alert">{formError}</p>{/if}</div>
				{:else}
					<div class="rate-note"><span>Tarifa fija configurada</span><strong>{euros(pulseState.settings.tariff_eur_kwh)} / kWh</strong><a href="/settings">Ajustes</a></div>
				{/if}
			{:else}
				<div class="pvpc-form-note"><strong>PVPC por hora · Península</strong><span>Precios oficiales de Red Eléctrica. Energía sin impuestos ni potencia fija.</span><span>El reparto por tramo es una estimación uniforme, no una lectura del cargador.</span></div>
			{/if}
			{#if saveError}<p class="form-error" role="alert">{saveError}</p>{/if}
			{#if chargePricingMethod === 'pvpc'}
				<button class="button button-secondary button-block quote-button" type="button" disabled={!online || busy || savingCharge || quoteBusy} onclick={() => void requestQuote()}>{quoteBusy ? 'Consultando Red Eléctrica…' : 'Calcular coste'}</button>
				{#if quoteError}<div class="quote-error" role="alert"><p>{quoteError}</p><button class="text-button" type="button" disabled={!online || busy || savingCharge || quoteBusy} onclick={() => void requestQuote()}>Reintentar</button></div>{/if}
				{#if pvpcQuote && quoteFingerprint === quoteInputKey}
					<section class="quote-preview" aria-label="Estimación de coste PVPC">
						<div class="quote-total"><span>Coste estimado</span><strong>{euros(pvpcQuote.cost_eur)}</strong><small>Tarifa media del tramo: {euros(pvpcQuote.tariff_eur_kwh)} / kWh</small></div>
						<p>Estimación uniforme · {quoteDurationLabel(pvpcQuote)} · {pvpcQuote.pricing.region === 'peninsula' ? 'Península' : 'Zona indicada por el servidor'} · sin impuestos ni potencia fija.</p>
						<ul class="pricing-intervals">
							{#each pvpcQuote.pricing.intervals as interval, intervalIndex (`quote-${intervalIndex}`)}
								<li><span><strong>Tramo {intervalIndex + 1}</strong><small>{formatInstantWithOffset(interval.started_at)} → {formatInstantWithOffset(interval.ended_at)}</small><small>{number(interval.kwh)} kWh · {euros(interval.price_eur_kwh)} / kWh · Precio consultado: {formatInstantWithOffset(interval.price_fetched_at)}</small></span><b>{euros(interval.cost_eur)}</b></li>
							{/each}
						</ul>
						<small>Origen: {pvpcQuote.pricing.source}. El servidor volverá a calcular el coste al guardar.</small>
					</section>
				{/if}
			{/if}
			<div class="form-actions"><button class="button button-primary button-block" type="submit" disabled={busy || savingCharge || quoteBusy || !online}>{savingCharge ? 'Guardando…' : quoteBusy ? 'Calculando…' : busy ? 'Un momento…' : editing ? 'Guardar cambios' : 'Añadir carga'}</button>{#if editing}<button class="button button-quiet button-block" type="button" disabled={busy || savingCharge} onclick={cancelEdit}>Cancelar edición</button>{:else if creationSent}<button class="button button-quiet button-block" type="button" disabled={busy || savingCharge} onclick={() => void discardUnconfirmedCharge()}>Descartar formulario</button>{/if}</div>
		</form>
		<div class="notice energy-note"><Glyph name="overview" size={17} /><span>Las cifras describen lo registrado. Pulse no estima consumos ni inventa sesiones ausentes.</span></div>
	</aside>
</section>

</div>
</section>
</section>
<section id="energy-planning-view" class="energy-view planning-view" aria-labelledby="energy-planning-heading" hidden={activeSection !== 'planning'}>
	<EnergyPlanningPanel {busy} {online} {revision} {drafts} {confirmAction} {onSessionExpired} />
</section>

<style>
	.analytics-panel { display: grid; gap: 19px; margin-top: 28px; padding: 22px 0 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.analytics-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
	.analytics-title-copy { display: grid; justify-items: start; gap: 0; }
	.personal-records-kicker { display: inline-flex; align-items: center; gap: 7px; margin: 0; color: var(--forest); font-size: .75rem; font-weight: 720; letter-spacing: .075em; }
	.personal-records-kicker :global(svg) { flex: 0 0 auto; }
	.energy-intro h2 { margin: 0; }
	.analytics-heading h2 { margin: 5px 0 0; font-size: 1.125rem; font-weight: 640; letter-spacing: -.03em; }
	.analytics-title-copy > p:last-child { max-width: 580px; margin: 7px 0 0; color: var(--muted); font-size: 1rem; line-height: 1.55; }
	.analytics-range-control { display: flex; flex: 0 0 auto; gap: 3px; padding: 3px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
	.analytics-range-button { min-width: 67px; min-height: 48px; padding: 0 9px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--muted-strong); cursor: pointer; font: inherit; font-size: .875rem; font-weight: 650; }
	.analytics-range-button:hover:not(:disabled) { background: var(--surface-raised); }
	.analytics-range-button:focus-visible { outline: 2px solid var(--forest); outline-offset: 2px; }
	.analytics-range-button:disabled { cursor: not-allowed; opacity: .55; }
	.analytics-range-selected { border-color: var(--line); background: var(--surface-raised); color: var(--ink); box-shadow: 0 1px 3px rgb(20 36 29 / 8%); }
	.analytics-loading, .analytics-error, .analytics-empty-chart, .analytics-observations-empty { display: flex; min-height: 68px; align-items: center; gap: 10px; padding: 15px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); color: var(--muted-strong); font-size: .875rem; }
	.analytics-error { justify-content: space-between; gap: 14px; border-color: color-mix(in srgb, var(--danger) 24%, var(--line)); }
	.analytics-error > div { display: grid; gap: 4px; }
	.analytics-error strong, .analytics-empty-chart strong { color: var(--ink); font-size: .875rem; }
	.analytics-error p { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.analytics-window { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: -6px; color: var(--muted); font-size: .75rem; }
	.analytics-window > span + span::before { content: '·'; margin-right: 16px; }
	.analytics-source-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0; border-block: 1px solid var(--line); }
	.analytics-source-card { display: grid; align-content: start; gap: 14px; min-width: 0; padding: 14px 18px 14px 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.analytics-source-card + .analytics-source-card { padding-right: 0; padding-left: 18px; border-left: 1px solid var(--line); }
	.analytics-source-demo { background: transparent; }
	.analytics-source-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
	.analytics-source-heading h3 { display: flex; align-items: center; gap: 8px; margin: 0; font-size: .875rem; font-weight: 680; }
	.analytics-source-heading .source-dot { flex: 0 0 8px; }
	.analytics-source-heading .badge { white-space: nowrap; }
	.analytics-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
	.analytics-metrics > div { display: grid; gap: 5px; min-width: 0; }
	.analytics-metrics span { color: var(--muted); font-size: .875rem; }
	.analytics-metrics strong { overflow-wrap: anywhere; font-size: clamp(.95rem, 1.4vw, 1.125rem); font-weight: 660; font-variant-numeric: tabular-nums; }
	.analytics-metrics small { color: var(--muted); font-size: .75rem; font-weight: 520; }
	.analytics-empty-source { display: flex; min-height: 39px; align-items: center; margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.analytics-months { display: grid; gap: 10px; padding-top: 17px; border-top: 1px solid var(--line); }
	.analytics-subheading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	.analytics-subheading h3, .analytics-coverage-heading h3 { margin: 4px 0 0; font-size: 0.875rem; font-weight: 650; letter-spacing: -.02em; }
	.analytics-explainer { margin: -5px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.analytics-retention-note { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.analytics-empty-chart { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-content: center; gap: 3px 10px; min-height: 76px; }
	.analytics-empty-mark { display: grid; width: 34px; height: 34px; grid-row: span 2; place-items: center; border-radius: 10px; background: var(--forest-soft); color: var(--forest) !important; }
	.analytics-empty-mark :global(svg) { display: block; }
	.analytics-empty-chart span { color: var(--muted); font-size: .875rem; }
	.month-chart { display: grid; gap: 0; margin: 0; padding: 0; border-bottom: 1px solid var(--line); list-style: none; }
	.month-chart-row { display: grid; gap: 7px; padding: 12px 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; }
	.month-chart-label { font-size: .875rem; font-weight: 680; text-transform: capitalize; }
	.month-origin-group { display: grid; gap: 5px; min-width: 0; }
	.month-origin-row { display: grid; grid-template-columns: 48px minmax(45px, 1fr) 83px; align-items: center; gap: 8px; color: var(--muted); font-size: .875rem; }
	.month-origin-row > strong { color: var(--ink); font-size: .875rem; font-weight: 620; text-align: right; font-variant-numeric: tabular-nums; }
	.month-origin-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 10px; min-width: 0; margin: 0; padding: 0 0 2px 56px; }
	.month-origin-metrics > div { display: grid; align-content: start; gap: 2px; min-width: 0; }
	.month-origin-metrics dt { color: var(--muted); font-size: .75rem; line-height: 1.3; }
	.month-origin-metrics dd { margin: 0; color: var(--ink); font-size: .8125rem; font-weight: 620; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.month-bar { display: block; width: 100%; height: 7px; min-width: 0; appearance: none; border: 0; border-radius: 999px; background: var(--surface-soft); }
	.month-bar::-webkit-progress-bar { border-radius: inherit; background: var(--surface-soft); }
	.month-bar-manual::-webkit-progress-value { border-radius: inherit; background: var(--forest); }
	.month-bar-manual::-moz-progress-bar { border-radius: inherit; background: var(--forest); }
	.month-bar-demo::-webkit-progress-value { border-radius: inherit; background: var(--accent); }
	.month-bar-demo::-moz-progress-bar { border-radius: inherit; background: var(--accent); }
	.analytics-coverage { display: grid; gap: 12px; min-width: 0; padding-top: 19px; border-top: 1px solid var(--line); }
	.analytics-coverage-heading { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px; }
	.analytics-coverage-heading .badge { flex: 0 0 auto; max-width: 100%; white-space: normal; }
	.analytics-observations-empty { min-height: 0; padding: 0; border: 0; border-radius: 0; background: transparent; line-height: 1.5; }
	.analytics-coverage-details { min-width: 0; }
	.analytics-coverage-details > summary { min-height: 48px; align-content: center; padding: 8px 0; color: var(--forest); cursor: pointer; font-size: .875rem; font-weight: 650; line-height: 1.5; overflow-wrap: anywhere; }
	.analytics-coverage-details > summary:hover { text-decoration: underline; text-underline-offset: 3px; }
	.analytics-coverage-details > summary:focus-visible { outline: 2px solid var(--forest); outline-offset: 3px; border-radius: 4px; }
	.analytics-coverage-content { display: grid; gap: 12px; padding: 4px 0 12px; }
	.coverage-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
	.coverage-metrics > div { display: grid; align-content: start; gap: 5px; min-width: 0; min-height: 53px; padding: 8px 0; border-bottom: 1px solid var(--line); }
	.coverage-metrics span { color: var(--muted); font-size: .875rem; }
	.coverage-metrics strong { overflow-wrap: anywhere; font-size: .875rem; font-weight: 650; font-variant-numeric: tabular-nums; }
	.coverage-last-observation { grid-column: span 2; }
	.coverage-last-observation strong { font-size: .875rem; }
	.analytics-quality-details { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.analytics-consumption { display: flex; align-items: center; gap: 11px; padding: 11px 13px; border: 1px dashed var(--line-strong); border-radius: 10px; background: var(--surface-raised); }
	.analytics-consumption > strong { font-size: 1.25rem; font-weight: 630; }
	.analytics-consumption > span { display: grid; gap: 3px; }
	.analytics-consumption b { font-size: .875rem; font-weight: 650; }
	.analytics-consumption small { color: var(--muted); font-size: .75rem; }
	.personal-records { display: grid; gap: 14px; margin-top: 20px; }
	.personal-records-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
	.personal-records-heading h2 { margin: 4px 0 0; font-size: 1.25rem; font-weight: 650; letter-spacing: -.025em; overflow-wrap: anywhere; }
	.personal-records-kicker { font-size: .6875rem; }
	.personal-records-heading > div:first-child > p:last-child { max-width: 600px; margin: 6px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.personal-records-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
	.personal-records-actions .button { min-height: 48px; }
	.energy-personal-content { display: grid; gap: 16px; min-width: 0; }
	.energy-personal-content[hidden] { display: none; }
	.form-icon { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 11px; background: var(--forest-soft); color: var(--forest); }
	.energy-totals { display: grid; gap: 17px; margin: 0 0 30px; padding-top: 20px; border-top: 1px solid var(--line); }
	.energy-total-group { min-width: 0; padding: 17px 22px 18px 0; }
	.energy-total-group-heading { display: flex; align-items: flex-start; gap: 9px; min-width: 0; }
	.energy-total-group-heading .source-dot { flex: 0 0 8px; margin-top: 6px; }
	.energy-total-group-heading h3 { margin: 0; font-size: .9375rem; font-weight: 680; }
	.energy-total-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px; margin: 18px 0 0; }
	.energy-total-metrics > div { display: grid; align-content: start; gap: 5px; min-width: 0; }
	.energy-total-metrics dt { color: var(--muted); font-size: .8125rem; line-height: 1.4; }
	.energy-total-metrics dd { margin: 0; color: var(--ink); font-size: 1.125rem; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.energy-demo-metrics { margin-top: 10px; }
	.energy-demo-metrics dd { font-size: .9375rem; }
	.demo-trip-note { margin: 11px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.45; }
	.energy-totals-offline { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.source-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--forest); }
	.source-demo { background: var(--accent); }
	.energy-main-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(255px, .75fr); align-items: start; gap: 18px; margin-top: 28px; }
	.energy-history-column { min-width: 0; }
	.energy-history-column .section-heading { margin-top: 0; }
	.chart-card { padding: 18px 0 8px; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.chart-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 14px; }
	.chart-heading > div:first-child { flex: 1 1 200px; min-width: 0; }
	.chart-heading h3 { margin: 0; font-size: .875rem; }
	.chart-heading p { margin: 5px 0 0; color: var(--muted); font-size: .875rem; }
	.chart-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--muted); font-size: .875rem; }
	.chart-legend > span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
	.charge-chart { display: block; width: 100%; height: auto; margin-top: 13px; overflow: visible; }
	.chart-axis { stroke: var(--line-strong); stroke-width: 1; }
	.chart-bar { fill: var(--forest); opacity: .78; }
	.chart-bar-demo { fill: var(--accent); }
	.chart-indices { position: relative; min-height: 1.5em; color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.chart-indices span { position: absolute; transform: translateX(-50%); }
	.charge-list { overflow: visible; margin-top: 0; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.charge-row:first-child { border-top: 1px solid var(--line); }
	.charge-row { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 66px; padding: 9px 13px; border-bottom: 1px solid var(--line); }
	.charge-row:last-child { border-bottom: 0; }
	.charge-mark { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 9px; background: var(--surface-soft); color: var(--forest); }
	.charge-row-main { display: grid; min-width: 0; gap: 4px; }
	.charge-row-main strong { font-size: .875rem; font-weight: 630; }
	.charge-row-main > span { color: var(--muted); font-size: .875rem; }
	.charge-row-main i { font-style: normal; }
	.saved-pricing-details { grid-column: 2 / -1; min-width: 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-soft); }
	.saved-charge-details { grid-column: 2 / -1; min-width: 0; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-soft); }
	.saved-charge-details summary { display: flex; min-height: 48px; align-items: center; color: var(--forest); cursor: pointer; font-size: .875rem; font-weight: 650; }
	.saved-charge-details summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: 5px; }
	.saved-charge-details dl, .analytics-technical-details dl { display: grid; gap: 0; margin: 0 0 8px; }
	.saved-charge-details dl > div, .analytics-technical-details dl > div { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-top: 1px solid var(--line); }
	.saved-charge-details dt, .analytics-technical-details dt { color: var(--muted); font-size: .8125rem; }
	.saved-charge-details dd, .analytics-technical-details dd { margin: 0; color: var(--ink); font-size: .8125rem; font-weight: 620; text-align: right; font-variant-numeric: tabular-nums; }
	.analytics-technical-details { display: grid; gap: 5px; padding-top: 10px; border-top: 1px solid var(--line); }
	.analytics-technical-details > strong { font-size: .8125rem; }
	.analytics-technical-details p { margin: 0; color: var(--muted); font-size: .75rem; line-height: 1.45; }
	.saved-pricing-details summary { display: flex; min-height: 48px; align-items: center; color: var(--forest); cursor: pointer; font-size: .875rem; font-weight: 650; }
	.saved-pricing-details > p { margin: 2px 0 8px; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.pricing-intervals { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
	.pricing-intervals li { display: flex; justify-content: space-between; gap: 10px; padding-top: 7px; border-top: 1px solid var(--line); }
	.pricing-intervals li > span { display: grid; gap: 3px; min-width: 0; }
	.pricing-intervals li strong { font-size: .875rem; }
	.pricing-intervals li small { color: var(--muted); font-size: .75rem; line-height: 1.4; overflow-wrap: anywhere; }
	.pricing-intervals li b { flex: 0 0 auto; font-size: .875rem; font-variant-numeric: tabular-nums; }
	.charge-actions { grid-column: 2 / -1; display: flex; gap: 12px; margin-top: -6px; }
	.charge-actions .text-button { min-height: 48px; font-size: .875rem; }
	.danger-text { color: var(--danger); }
	.pagination { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: .875rem; }
	.pagination > div { display: flex; gap: 6px; }
	.loading-card { padding: 22px; color: var(--muted); font-size: 1rem; }
	.charge-form-card { position: sticky; top: 18px; padding: 19px; }
	.charge-form-card .section-heading { align-items: center; margin: 0 0 10px; }
	.charge-form-card .section-heading h2 { font-size: 1.0625rem; }
	.charge-form { display: grid; gap: 14px; margin-top: 18px; }
	.input-with-unit { position: relative; }
	.input-with-unit input { padding-right: 4.5rem; }
	.input-with-unit span { position: absolute; top: 50%; right: 12px; color: var(--muted); font-size: .875rem; transform: translateY(-50%); pointer-events: none; }
	.rate-note { display: grid; grid-template-columns: 1fr auto; gap: 5px; padding: 12px; border-radius: 9px; background: var(--surface-soft); }
	.rate-note span { grid-column: 1 / -1; color: var(--muted); font-size: .875rem; }
	.rate-note strong { font-size: .875rem; }
	.rate-note a { display: inline-flex; min-height: 48px; align-items: center; justify-content: center; color: var(--forest); font-size: .875rem; font-weight: 650; text-decoration: underline; text-underline-offset: 2px; }
	.pvpc-form-note { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
	.pvpc-form-note strong { font-size: .875rem; }
	.pvpc-form-note span { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.quote-button { min-height: 48px; }
	.quote-error { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--danger) 25%, var(--line)); border-radius: 9px; background: var(--surface-soft); }
	.quote-error p { margin: 0; color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.quote-error .text-button { flex: 0 0 auto; min-height: 48px; font-size: .875rem; }
	.quote-preview { display: grid; grid-column: 1 / -1; gap: 9px; padding: 13px; border: 1px solid var(--line-strong); border-radius: 11px; background: var(--surface-soft); }
	.quote-total { display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 3px 10px; }
	.quote-total > span { color: var(--muted); font-size: .875rem; }
	.quote-total > strong { grid-column: 2; grid-row: 1 / span 2; font-size: 1.25rem; font-weight: 650; font-variant-numeric: tabular-nums; }
	.quote-total > small { color: var(--muted); font-size: .875rem; }
	.quote-preview > p { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.quote-preview > small { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.offline-charge-note { margin: 10px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.form-error { margin: 0; color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.energy-note { margin-top: 17px; font-size: .875rem; }
	.energy-note :global(svg) { flex: 0 0 17px; color: var(--forest); }
	@media (max-width: 1000px) {
		.energy-main-grid { grid-template-columns: minmax(0, 1fr); }
		.charge-form-card { position: static; }
		.charge-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.rate-note { align-self: end; min-height: 60px; }
		.form-actions, .pvpc-form-note, .quote-button, .quote-error { grid-column: 1 / -1; }
		.energy-note { grid-column: 1 / -1; }
		.coverage-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
	}
	@media (max-width: 700px) {
		.personal-records-heading { align-items: stretch; flex-direction: column; }
		.personal-records-actions { justify-content: stretch; }
		.personal-records-actions .button { flex: 1 1 150px; }
		.analytics-heading { flex-direction: column; }
		.analytics-range-control { width: 100%; }
		.analytics-range-button { flex: 1 1 0; }
		.analytics-source-grid { grid-template-columns: 1fr; }
		.analytics-metrics { gap: 7px; }
		.coverage-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.charge-form { grid-template-columns: 1fr; }
		.form-actions, .energy-note { grid-column: auto; }
		.pvpc-form-note, .quote-button, .quote-error, .quote-preview { grid-column: 1; }
		.charge-row { grid-template-columns: 32px minmax(0, 1fr) auto; gap: 8px; padding-inline: 10px; }
		.charge-row > .badge { grid-column: 3; grid-row: 1; }
		.saved-pricing-details { grid-column: 2 / -1; }
	}

	@media (max-width: 360px) {
		.analytics-panel { padding-inline: 0; }
		.analytics-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.month-origin-row { grid-template-columns: 42px minmax(24px, 1fr) 78px; gap: 6px; }
		.pagination { align-items: flex-start; flex-direction: column; }
		.pagination > div { width: 100%; }
		.pagination .button { flex: 1 1 0; }
	}
	.energy-intro { margin-bottom: 24px; }
	.energy-intro h2 { font-size: clamp(1.8rem, 3vw, 2.45rem); letter-spacing: -.035em; }
	.energy-session-layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(245px, .72fr); align-items: start; gap: clamp(20px, 3vw, 42px); margin: 0 0 34px; }
	.energy-charger-column { min-width: 0; }
	:global(.charger-panel.card) { margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	:global(.charger-heading > div) { flex: 1 1 auto; min-width: 0; }
	:global(.charger-heading h2) { margin: 0; }
	:global(.charger-heading > div > p) { margin: 7px 0 0; }
	:global(.charger-state) { flex: 0 0 auto; white-space: nowrap; }
	.energy-cost-profile { min-width: 0; margin-top: 18px; padding: 18px 0 8px 22px; border-left: 1px solid var(--line); }
	.energy-cost-profile h3 { margin: 0; font-size: 1.125rem; font-weight: 650; letter-spacing: -.02em; }
	.energy-cost-profile > p { margin: 9px 0 0; color: var(--muted); font-size: .9375rem; line-height: 1.55; }
	.energy-cost-facts { display: grid; gap: 0; margin: 17px 0 8px; }
	.energy-cost-facts > div { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line); }
	.energy-cost-facts dt { color: var(--muted); font-size: .875rem; line-height: 1.45; }
	.energy-cost-facts dd { margin: 0; color: var(--ink); font-size: .875rem; font-weight: 650; text-align: right; font-variant-numeric: tabular-nums; }
	.energy-settings-link { display: inline-flex; min-height: 48px; align-items: center; color: var(--forest); font-size: .875rem; font-weight: 650; text-decoration: underline; text-underline-offset: 3px; }
	.energy-section-copy { margin: 5px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.energy-history-column > .section-heading { align-items: flex-end; }
	.energy-history-column > .section-heading h2 { margin: 0; }
	.energy-history-column > .section-heading .energy-section-copy { max-width: 54ch; }
	.charge-form-card .section-heading > div { min-width: 0; }
	.analytics-subheading h3 { margin: 0; font-size: .9375rem; font-weight: 650; }
	@media (max-width: 1000px) {
		.energy-session-layout { grid-template-columns: minmax(0, 1fr); gap: 12px; }
		.energy-cost-profile { margin-top: 0; padding: 15px 0 0; border-top: 1px solid var(--line); border-left: 0; }
	}
	@media (max-width: 700px) {
		.energy-total-metrics { gap: 12px; }
		.analytics-source-card { padding-right: 0; }
		.analytics-source-card + .analytics-source-card { padding-left: 0; border-top: 1px solid var(--line); border-left: 0; }
	}
	.energy-intro { margin-bottom: 16px; }
	.energy-intro > div > p { max-width: 42ch; }
	.energy-switcher { margin-bottom: 18px; }
	.energy-view { display: grid; gap: 20px; min-width: 0; }
	.energy-view[hidden] { display: none; }
	.energy-summary-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
	.energy-summary-heading h3 { margin: 0; font-size: clamp(1.12rem, 1.8vw, 1.4rem); font-weight: 650; letter-spacing: -.025em; }
	.energy-summary-heading > .button { min-height: 48px; flex: 0 0 auto; }
	.energy-totals { margin: 0; padding: 0; border: 0; }
	.energy-total-group { padding-block: 4px; }
	.energy-total-group-heading h3 { font-size: .8125rem; font-weight: 650; }
	.energy-total-metrics { margin-top: 12px; }
	.energy-total-metrics dt { font-size: .75rem; }
	.energy-total-metrics dd { font-size: clamp(1.15rem, 2.3vw, 1.65rem); letter-spacing: -.035em; }
	.demo-trip-note { max-width: 65ch; }
	.analytics-panel { gap: 15px; margin: 0; padding: 20px 0 0; }
	.analytics-heading { align-items: flex-end; }
	.analytics-title-copy h2 { margin: 0; font-size: 1rem; font-weight: 650; letter-spacing: -.02em; }
	.analytics-title-copy > p:last-child { margin-top: 4px; font-size: .8125rem; }
	.analytics-range-control { padding: 2px; border-radius: 10px; }
	.analytics-range-button { min-width: 60px; min-height: 42px; padding-inline: 8px; font-size: .75rem; }
	.analytics-range-selected { border-color: var(--forest); color: var(--ink); box-shadow: inset 0 -2px var(--forest); }
	.analytics-months, .analytics-coverage { padding-top: 14px; }
	.records-view { gap: 22px; }
	.charger-disclosure { overflow: clip; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
	.charger-disclosure > summary { display: flex; min-height: 62px; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 16px; color: var(--ink); cursor: pointer; list-style: none; }
	.charger-disclosure > summary::-webkit-details-marker { display: none; }
	.charger-disclosure > summary > span:first-child { display: grid; gap: 2px; }
	.charger-disclosure > summary strong { font-size: .875rem; font-weight: 650; }
	.charger-disclosure > summary small { color: var(--muted); font-size: .75rem; }
	.charger-disclosure-action { flex: 0 0 auto; color: var(--forest); font-size: .75rem; font-weight: 700; }
	.charger-disclosure[open] > summary { border-bottom: 1px solid var(--line); }
	.charger-disclosure[open] > .energy-session-layout { margin: 0; padding: 16px; }
	.personal-records { gap: 10px; margin: 0; }
	.personal-records-heading { padding-bottom: 10px; border-bottom: 1px solid var(--line); }
	.personal-records-heading h2 { margin-top: 0; font-size: 1rem; }
	.personal-records-kicker { display: none; }
	.personal-records-heading > div:first-child > p:last-child { margin-top: 3px; font-size: .8125rem; }
	.personal-records-actions .button { min-height: 44px; }
	.energy-main-grid { gap: 24px; margin-top: 0; }
	.charge-form-card.card { position: static; padding: 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.charge-form-card .section-heading { margin-top: 16px; }
	.charge-form { margin-top: 12px; }
	.analytics-coverage-details > summary { min-height: 44px; }
	@media (max-width: 680px) {
		.energy-summary-heading { align-items: stretch; flex-direction: column; }
		.energy-summary-heading > .button { width: 100%; }
		.analytics-heading { align-items: stretch; flex-direction: column; }
		.analytics-range-control { width: 100%; }
		.analytics-range-button { flex: 1 1 0; }
		.energy-total-metrics { gap: 12px; }
		.energy-main-grid { grid-template-columns: minmax(0, 1fr); }
		.personal-records-heading { align-items: stretch; flex-direction: column; }
		.personal-records-actions { justify-content: stretch; }
		.personal-records-actions .button { flex: 1 1 140px; }
	}
	@media (max-width: 380px) {
		.analytics-range-button { min-width: 0; padding-inline: 4px; font-size: .6875rem; }
		.energy-total-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
		.energy-total-metrics dt { font-size: .75rem; }
		.energy-total-metrics dd { font-size: clamp(1.45rem, 8vw, 2rem); }
		.charger-disclosure > summary { align-items: flex-start; padding-inline: 12px; }
		.charger-disclosure-action { white-space: nowrap; }
		.charger-disclosure[open] > .energy-session-layout { padding: 12px; }
	}
	.energy-intro { margin-bottom: 12px; }
	.energy-intro > div > p { margin-top: 4px; }
	.energy-summary-view { gap: 27px; }
	.energy-view.energy-summary-view { padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
	.energy-total-group-manual { display: grid; gap: 19px; min-width: 0; padding: clamp(20px, 3vw, 32px); border-radius: var(--radius-panel); background: var(--premium-solid); color: var(--premium-solid-text); box-shadow: none; }
	.energy-summary-heading { align-items: center; }
	.energy-summary-heading .button { min-height: 48px; }
	.energy-total-group-heading { display: flex; align-items: center; gap: 10px; }
	.energy-total-group-heading .source-dot { flex: 0 0 8px; margin-top: 0; background: #5c8bff; }
	.energy-total-group-heading h3 { margin: 0; color: #f5f5f7; font-size: .875rem; font-weight: 650; letter-spacing: .01em; }
	.energy-total-metrics { grid-template-columns: minmax(0, 1.2fr) minmax(105px, .8fr); align-items: end; gap: 12px 18px; margin: 0; padding-top: 17px; border-top: 1px solid rgb(245 245 247 / 17%); }
	.energy-total-metrics > div { gap: 6px; }
	.energy-total-metrics dt { color: rgb(245 245 247 / 68%); font-size: .8125rem; }
	.energy-total-metrics dd { color: #f5f5f7; font-size: clamp(1.25rem, 3vw, 1.75rem); font-weight: 560; letter-spacing: -.035em; }
	.energy-total-metrics .energy-metric-primary dd { color: #fff; font-size: clamp(2.65rem, 6vw, 4.35rem); font-weight: 650; letter-spacing: -.065em; line-height: .98; }
	.energy-metric-primary dd small { color: #8eafff; font-size: .42em; font-weight: 600; letter-spacing: -.02em; }
	.energy-total-context { margin: -7px 0 0; color: rgb(245 245 247 / 58%); font-size: .75rem; line-height: 1.45; }
	.demo-total-disclosure { padding-top: 12px; border-top: 1px solid rgb(245 245 247 / 17%); }
	.demo-total-disclosure > summary { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 10px; color: #f5f5f7; font-size: .8125rem; font-weight: 640; cursor: pointer; }
	.demo-total-disclosure > summary small { color: rgb(245 245 247 / 60%); font-size: .75rem; font-weight: 450; text-align: right; }
	.demo-total-content { display: grid; gap: 13px; padding-top: 10px; }
	.demo-total-content h3 { display: flex; align-items: center; gap: 8px; margin: 0; color: #f5f5f7; font-size: .8125rem; }
	.demo-total-content .source-dot { flex: 0 0 8px; background: var(--accent); }
	.demo-total-content .energy-total-metrics { margin: 0; padding-top: 13px; border-top: 1px solid rgb(245 245 247 / 14%); }
	.demo-total-content .energy-total-metrics dd { color: #f5f5f7; font-size: .9375rem; letter-spacing: 0; }
	.demo-trip-note { max-width: 62ch; margin: 0; color: rgb(245 245 247 / 62%); font-size: .75rem; line-height: 1.5; }
	.analytics-panel { margin-top: 0; }
	@media (max-width: 560px) {
		.energy-summary-view { gap: 24px; }
		.energy-total-group-manual { gap: 16px; padding: 20px 18px; border-radius: 24px; }
		.energy-summary-heading { gap: 10px; }
		.energy-summary-heading .button { flex: 0 0 auto; padding-inline: 13px; }
		.energy-total-metrics { grid-template-columns: minmax(0, 1.2fr) minmax(95px, .8fr); gap: 10px; }
		.energy-total-metrics .energy-metric-primary dd { font-size: clamp(2.45rem, 10vw, 3.35rem); }
		.energy-total-metrics .energy-metric-cost dd { font-size: clamp(1.1rem, 5vw, 1.45rem); }
		.demo-total-content .energy-total-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	}
	@media (max-width: 370px) {
		.energy-total-group-manual { padding: 18px 15px; }
		.energy-summary-heading .button { padding-inline: 10px; font-size: .8125rem; }
		.energy-total-metrics .energy-metric-primary dd { font-size: clamp(2.25rem, 10vw, 2.8rem); }
		.demo-total-disclosure > summary { align-items: flex-start; flex-direction: column; justify-content: center; gap: 2px; }
		.demo-total-disclosure > summary small { text-align: left; }
	}
</style>
