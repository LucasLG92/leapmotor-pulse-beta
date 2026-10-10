import { ApiError, requestApi } from './api';
import type { PushCapabilities, PushDeviceList } from './types';

async function readJson<T>(path: string): Promise<T> {
	const response = await requestApi(path, {
		method: 'GET',
		credentials: 'same-origin',
		cache: 'no-store',
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) throw new ApiError('No se pudo consultar el estado de las notificaciones.', response.status);
	const contentType = response.headers.get('content-type') ?? '';
	if (!contentType.toLowerCase().includes('application/json')) {
		throw new ApiError('El servidor no devolvió una respuesta válida.', response.status || 502);
	}
	try {
		return await response.json() as T;
	} catch {
		throw new ApiError('No se pudo leer la respuesta del servidor.', response.status || 502);
	}
}

export function getPushCapabilities(): Promise<PushCapabilities> {
	return readJson<PushCapabilities>('/api/push/capabilities');
}

export function getPushDevices(): Promise<PushDeviceList> {
	return readJson<PushDeviceList>('/api/push/subscriptions');
}
