export const VEHICLE_ARTWORK_CACHE = 'pulse-public-vehicle-artwork-v1';

export function vehicleArtworkRequestUrl(
	request: Request,
	allowedUrls: ReadonlySet<string>,
	origin: string
): string | null {
	if (request.method !== 'GET') return null;
	let url: URL;
	try {
		url = new URL(request.url);
	} catch {
		return null;
	}
	if (url.origin !== origin || url.username || url.password || url.search || url.hash) return null;
	return allowedUrls.has(url.href) ? url.href : null;
}

export function isCacheableVehicleArtworkResponse(response: Response | undefined, expectedUrl: string): boolean {
	if (!response || response.status !== 200 || response.redirected || response.url !== expectedUrl) return false;
	const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
	return contentType === 'image/webp';
}

export function createVehicleArtworkRequest(url: string): Request {
	return new Request(url, {
		method: 'GET',
		mode: 'same-origin',
		credentials: 'omit',
		redirect: 'error',
		cache: 'default'
	});
}

export interface VehicleArtworkTask<T> {
	response: Promise<T>;
	done: Promise<unknown>;
}

export function singleFlightVehicleArtwork<T>(
	pending: Map<string, VehicleArtworkTask<T>>,
	url: string,
	work: () => VehicleArtworkTask<T>
): Promise<T> {
	const existing = pending.get(url);
	if (existing) return existing.response;
	const current = work();
	pending.set(url, current);
	const clear = () => {
		if (pending.get(url) === current) pending.delete(url);
	};
	void current.done.then(clear, clear);
	return current.response;
}
