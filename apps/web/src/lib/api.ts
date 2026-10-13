import type { MaintenancePlanPage, ChargingPlanInput, ChargingPlan, ChargingComparison, NavigationFavoritePage } from './types';
import { getNativeBridge, nativeRequestFailureMessage } from './native';
import { waitForRead } from './startup-read';
import type { ChargePage, ChargerPricingHistoryPage, ChargerSessionPage, ChargerStatus, CommandHistoryEntry, CommandHistoryResponse, EnergyAnalytics, HttpMethod, ManualJourneyPage, ManualJourneyReport, NativeCapabilities, ObservedEventPage, PulseState, SessionState, TelemetryPage, WidgetGrantList, WidgetProfileList } from './types';

export class ApiError extends Error {
	status: number;

	constructor(message: string, status: number) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
	}
}

export async function getObservedEvents(limit = 20, offset = 0, signal?: AbortSignal): Promise<ObservedEventPage> {
	const response = await requestApi(`/api/protection/observations?limit=${limit}&offset=${offset}`, {
		method: 'GET', credentials: 'same-origin', cache: 'no-store',
		headers: { Accept: 'application/json' }, signal
	});
	return responseJson<ObservedEventPage>(response);
}

export async function getManualJourneys(limit = 20, offset = 0, signal?: AbortSignal, filters?: { tag?: string; search?: string }): Promise<ManualJourneyPage> {
	const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	if (filters?.tag) query.set('tag', filters.tag);
	if (filters?.search) query.set('search', filters.search);
	const response = await requestApi(`/api/journeys?${query.toString()}`, {
		method: 'GET', credentials: 'same-origin', cache: 'no-store',
		headers: { Accept: 'application/json' }, signal
	});
	return responseJson<ManualJourneyPage>(response);
}

export async function getManualJourneyReport(days: 30 | 90 | 365, signal?: AbortSignal): Promise<ManualJourneyReport> {
	const response = await requestApi(`/api/journeys/report?days=${days}`, {
		method: 'GET', credentials: 'same-origin', cache: 'no-store',
		headers: { Accept: 'application/json' }, signal
	});
	return responseJson<ManualJourneyReport>(response);
}

export async function getTelemetryHistory(limit = 20, offset = 0, signal?: AbortSignal): Promise<TelemetryPage> {
	const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	const response = await requestApi(`/api/telemetry/history?${query.toString()}`, {
		method: 'GET', credentials: 'same-origin', cache: 'no-store',
		headers: { Accept: 'application/json' }, signal
	});
	return responseJson<TelemetryPage>(response);
}

export async function requestApi(path: string, init: RequestInit): Promise<Response> {
	const bridge = getNativeBridge();
	if (bridge) {
		if (!path.startsWith('/api/') || path.startsWith('//')) {
			throw new ApiError('La solicitud nativa no tiene una ruta local válida.', 400);
		}
		const method = (init.method ?? 'GET').toUpperCase();
		const body = typeof init.body === 'string' ? init.body : undefined;
		const signal = init.signal;
		try {
			if (signal?.aborted) throw new DOMException('La solicitud se canceló.', 'AbortError');
			const request = bridge.request({ path, method, ...(body === undefined ? {} : { body }) });
			let result;
			const cancellableRead = method === 'GET' || (method === 'POST' &&
				['/api/navigation/places', '/api/navigation/chargers', '/api/navigation/tiles'].includes(path));
			if (signal && cancellableRead) {
				result = await new Promise<Awaited<typeof request>>((resolve, reject) => {
					const abort = () => reject(new DOMException('La solicitud se canceló.', 'AbortError'));
					signal.addEventListener('abort', abort, { once: true });
					if (signal.aborted) abort();
					request.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
				});
			} else {
				result = await request;
			}
			return new Response(result.body, {
				status: result.status,
				headers: result.contentType ? { 'Content-Type': result.contentType } : undefined
			});
		} catch (error) {
			if (error instanceof DOMException && error.name === 'AbortError') throw error;
			throw new ApiError(nativeRequestFailureMessage(error), 0);
		}
	}
	try {
		return await fetch(path, init);
	} catch (error) {
		if (error instanceof DOMException && error.name === 'AbortError') throw error;
		throw new ApiError('No se pudo contactar con el servidor. Comprueba la conexión e inténtalo de nuevo.', 0);
	}
}

async function responseJson<T>(response: Response): Promise<T> {
	const contentType = response.headers.get('content-type') ?? '';
	if (!contentType.toLowerCase().includes('application/json')) {
		throw new ApiError('El servidor no devolvió una respuesta JSON válida.', response.status || 502);
	}
	let value: unknown;
	try {
		value = await response.json();
	} catch {
		throw new ApiError('No se pudo leer la respuesta del servidor.', response.status || 502);
	}
	if (!response.ok) {
		const detail = value && typeof value === 'object' && 'detail' in value ? value.detail : null;
		const message = typeof detail === 'string' ? detail : 'No se pudo completar la solicitud.';
		throw new ApiError(message, response.status);
	}
	return value as T;
}

export async function queryNavigation<T>(
	path: '/api/navigation/status' | '/api/navigation/places' | '/api/navigation/chargers' | '/api/navigation/tiles',
	csrf: string,
	payload?: object,
	signal?: AbortSignal
): Promise<T> {
	const response = await requestApi(path, {
		method: payload === undefined ? 'GET' : 'POST',
		credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'origin', signal,
		headers: { Accept: 'application/json', ...(payload === undefined ? {} : {
			'Content-Type': 'application/json', 'X-Pulse-CSRF': csrf
		}) },
		...(payload === undefined ? {} : { body: JSON.stringify(payload) })
	});
	return responseJson<T>(response);
}

export async function getSession(signal?: AbortSignal): Promise<SessionState> {
	if (signal?.aborted) throw signal.reason ?? new DOMException('La lectura se canceló.', 'AbortError');
	const bridge = getNativeBridge();
	if (bridge) {
		let status;
		try {
			status = await (signal ? waitForRead(bridge.status(), signal) : bridge.status());
		} catch (error) {
			if (signal?.aborted) throw signal.reason ?? error;
			throw new ApiError('No se pudo consultar el acceso de la app. Inténtalo de nuevo.', 0);
		}
		if (signal?.aborted) throw signal.reason ?? new DOMException('La lectura se canceló.', 'AbortError');
		if (!status.configured || !status.authenticated) {
			return { authenticated: false, csrf_token: '', user: null, login_url: null };
		}
	}
	const response = await requestApi('/api/session', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' },
		signal
	});
	return responseJson<SessionState>(response);
}

export async function getState(signal?: AbortSignal): Promise<PulseState> {
	const response = await requestApi('/api/state', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' },
		signal
	});
	return responseJson<PulseState>(response);
}

export async function getCharges(limit: number, offset: number): Promise<ChargePage> {
	const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	const response = await requestApi(`/api/charges?${query.toString()}`, {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	return responseJson<ChargePage>(response);
}

export async function getEnergyAnalytics(days: number, signal?: AbortSignal): Promise<EnergyAnalytics> {
	const query = new URLSearchParams({ days: String(days) });
	const response = await requestApi(`/api/analytics/energy?${query.toString()}`, {
		method: 'GET',
		signal,
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	return responseJson<EnergyAnalytics>(response);
}

export async function getChargerStatus(): Promise<ChargerStatus> {
	const response = await requestApi('/api/charger', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	return responseJson<ChargerStatus>(response);
}

export async function getChargerSessions(limit: number, offset: number): Promise<ChargerSessionPage> {
	const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	const response = await requestApi(`/api/charger/sessions?${query.toString()}`, {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	return responseJson<ChargerSessionPage>(response);
}

export async function getChargerPricingHistory(sessionId: string, limit = 10, offset = 0): Promise<ChargerPricingHistoryPage> {
	const query = new URLSearchParams({ session_id: sessionId, limit: String(limit), offset: String(offset) });
	const response = await requestApi(`/api/charger/pricing-history?${query.toString()}`, {
		method: 'GET', credentials: 'same-origin', cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	return responseJson<ChargerPricingHistoryPage>(response);
}

export async function getCommandHistory(): Promise<CommandHistoryEntry[]> {
	const response = await requestApi('/api/commands/history', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	const value = await responseJson<CommandHistoryResponse>(response);
	return value.items;
}

export async function getWidgetProfiles(): Promise<WidgetProfileList['items']> {
	const response = await requestApi('/api/widget-profiles', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	const value = await responseJson<WidgetProfileList>(response);
	return value.items;
}

export async function getNativeCapabilities(signal?: AbortSignal): Promise<NativeCapabilities> {
	const response = await requestApi('/api/native-capabilities', {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' },
		signal
	});
	return responseJson<NativeCapabilities>(response);
}

export async function getWidgetGrants(profileId: string, signal?: AbortSignal): Promise<WidgetGrantList> {
	const response = await requestApi(`/api/widget-profiles/${encodeURIComponent(profileId)}/native-grants`, {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' },
		signal
	});
	return responseJson<WidgetGrantList>(response);
}

export async function mutateApi<T = { ok: boolean }>(
	path: string,
	method: HttpMethod,
	csrfToken: string,
	data?: unknown
): Promise<T> {
	if (!csrfToken) throw new ApiError('La sesión ha caducado. Vuelve a iniciar sesión.', 403);
	const headers = new Headers({ Accept: 'application/json', 'X-Pulse-CSRF': csrfToken });
	headers.set('Content-Type', 'application/json');
	const init: RequestInit = {
		method,
		credentials: 'same-origin',
		cache: 'no-store',
		headers,
		body: JSON.stringify(data ?? {})
	};
	const response = await requestApi(path, init);
	return responseJson<T>(response);
}


export async function getMaintenancePlans(odometer?: number, limit = 50, offset = 0, signal?: AbortSignal): Promise<MaintenancePlanPage> {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (odometer !== undefined) query.set('odometer_km', String(odometer));
    const response = await requestApi(`/api/maintenance-plans?${query}`, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal
    });
    return responseJson<MaintenancePlanPage>(response);
}

export async function getChargingComparison(days: 30 | 90 | 365, signal?: AbortSignal): Promise<ChargingComparison> {
    const response = await requestApi(`/api/energy/comparison?days=${days}`, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal
    });
    return responseJson<ChargingComparison>(response);
}

export async function simulateChargingPlan(payload: ChargingPlanInput): Promise<ChargingPlan> {
    const session = await getSession();
    if (!session.authenticated) throw new ApiError('Inicia sesión antes de calcular el plan.', 401);
    return mutateApi<ChargingPlan>('/api/energy/plan', 'POST', session.csrf_token, payload);
}

export async function getNavigationFavorites(signal?: AbortSignal): Promise<NavigationFavoritePage> {
    const response = await requestApi('/api/navigation/favorites', {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal
    });
    return responseJson<NavigationFavoritePage>(response);
}
