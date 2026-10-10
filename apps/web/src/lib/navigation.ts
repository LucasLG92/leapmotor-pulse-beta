import type { NavigationPlace, NavigationStation } from './types';

export interface PublicPoint {
	latitude: number;
	longitude: number;
}

const EARTH_RADIUS_METERS = 6_371_000;
const MAX_TILE_DATA_URL_LENGTH = 180_000;
const MAX_PROVIDER_ID_LENGTH = 80;
const MAX_STATION_CONNECTORS = 12;
const MAX_STATION_POWER_KW = 2_000;
const MAX_STATION_DISTANCE_METERS = 5_100;

export function isPublicPoint(value: unknown): value is PublicPoint {
	if (!value || typeof value !== 'object') return false;
	const point = value as Partial<PublicPoint>;
	return Number.isFinite(point.latitude)
		&& Number.isFinite(point.longitude)
		&& (point.latitude as number) >= -90
		&& (point.latitude as number) <= 90
		&& (point.longitude as number) >= -180
		&& (point.longitude as number) <= 180;
}

export function parsePublicCoordinate(value: string, minimum: number, maximum: number): number | null {
	const clean = value.trim();
	if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(clean)) return null;
	const number = Number(clean);
	return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null;
}

export function parsePlaceSearchQuery(value: string): string | null {
	const clean = value.trim();
	return clean.length >= 3 && clean.length <= 120 ? clean : null;
}

export function isPlainProviderText(value: unknown, maxLength = 160): value is string {
	if (typeof value !== 'string') return false;
	const clean = value.trim();
	if (!clean || clean.length > maxLength || /[\u0000-\u001f\u007f]/.test(clean)) return false;
	if (/<\/?[a-z!][^>]*>/i.test(clean)) return false;
	if (/(?:https?:\/\/|www\.)\S/i.test(clean)) return false;
	return true;
}

export function isNavigationPlace(value: unknown): value is NavigationPlace {
	if (!value || typeof value !== 'object') return false;
	const place = value as Partial<NavigationPlace>;
	return typeof place.id === 'string'
		&& place.id.length > 0
		&& place.id.length <= MAX_PROVIDER_ID_LENGTH
		&& !/[\u0000-\u001f\u007f]/.test(place.id)
		&& isPlainProviderText(place.name)
		&& isPublicPoint(place);
}

export function isNavigationStation(value: unknown): value is NavigationStation {
	if (!value || typeof value !== 'object') return false;
	const station = value as Partial<NavigationStation>;
	if (typeof station.id !== 'string' || !station.id || station.id.length > MAX_PROVIDER_ID_LENGTH || /[\u0000-\u001f\u007f]/.test(station.id)) return false;
	if (!isPlainProviderText(station.name) || !isPublicPoint(value)) return false;
	if (station.operator !== undefined && station.operator !== null && !isPlainProviderText(station.operator)) return false;
	if (station.address !== undefined && station.address !== null && !isPlainProviderText(station.address)) return false;
	if (!Array.isArray(station.connectors) || station.connectors.length > MAX_STATION_CONNECTORS || !station.connectors.every((item: unknown) => isPlainProviderText(item, 80))) return false;
	if (station.power_kw !== undefined && station.power_kw !== null && (typeof station.power_kw !== 'number' || !Number.isFinite(station.power_kw) || station.power_kw < 0 || station.power_kw > MAX_STATION_POWER_KW)) return false;
	return Number.isSafeInteger(station.distance_m) && (station.distance_m as number) >= 0 && (station.distance_m as number) <= MAX_STATION_DISTANCE_METERS;
}

export function makeManualPlace(latitudeText: string, longitudeText: string): NavigationPlace | null {
	const latitude = parsePublicCoordinate(latitudeText, -90, 90);
	const longitude = parsePublicCoordinate(longitudeText, -180, 180);
	if (latitude === null || longitude === null) return null;
	return {
		id: `manual:${latitude.toFixed(6)},${longitude.toFixed(6)}`,
		name: 'Punto público',
		latitude,
		longitude
	};
}

export function distanceMeters(from: PublicPoint, to: PublicPoint): number {
	if (!isPublicPoint(from) || !isPublicPoint(to)) return Number.NaN;
	const radians = (degrees: number) => degrees * Math.PI / 180;
	const latitudeDelta = radians(to.latitude - from.latitude);
	const longitudeDelta = radians(to.longitude - from.longitude);
	const firstLatitude = radians(from.latitude);
	const secondLatitude = radians(to.latitude);
	const haversine = Math.sin(latitudeDelta / 2) ** 2
		+ Math.cos(firstLatitude) * Math.cos(secondLatitude) * Math.sin(longitudeDelta / 2) ** 2;
	return Math.round(2 * EARTH_RADIUS_METERS * Math.asin(Math.sqrt(Math.min(1, haversine))));
}

export function connectorLabels(connectors: string[]): string {
	const labels: Record<string, string> = {
		type2: 'Tipo 2', type2_combo: 'CCS', ccs: 'CCS', chademo: 'CHAdeMO',
		tesla_supercharger: 'Tesla Supercharger', tesla_destination: 'Tesla Destination',
		schuko: 'Schuko', cee_blue: 'CEE azul', cee_red: 'CEE rojo'
	};
	return [...new Set(connectors.map((connector) => labels[connector] ?? 'Otro conector'))].join(', ') || 'No indicados';
}

export function sameMapArea(first: PublicPoint | null, second: PublicPoint): boolean {
	// Pixel rounding when opening/resizing the map should preserve its area.
	return Boolean(first && distanceMeters(first, second) <= 10);
}

export function mapLinks(place: NavigationPlace): { apple: string; google: string } | null {
	if (!isNavigationPlace(place)) return null;
	const coordinates = `${place.latitude},${place.longitude}`;
	const apple = new URL('https://maps.apple.com/');
	apple.search = new URLSearchParams({ daddr: coordinates, dirflg: 'd' }).toString();
	const google = new URL('https://www.google.com/maps/dir/');
	google.search = new URLSearchParams({ api: '1', destination: coordinates, travelmode: 'driving' }).toString();
	return { apple: apple.href, google: google.href };
}

export function isSafeTileDataUrl(value: unknown): value is string {
	return typeof value === 'string'
		&& value.length <= MAX_TILE_DATA_URL_LENGTH
		&& /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value);
}

export function takeNextCurrentTileJob<T>(
	jobs: T[],
	isCurrent: (job: T) => boolean,
	discard: (job: T) => void
): T | null {
	while (jobs.length) {
		const job = jobs.shift();
		if (job === undefined) return null;
		if (isCurrent(job)) return job;
		discard(job);
	}
	return null;
}

export interface CancellableTileJob {
	cancelled: boolean;
	controller: AbortController | null;
}

export function cancelTileJob(job: CancellableTileJob): void {
	job.cancelled = true;
	job.controller?.abort();
}

export class RequestGeneration {
	private generation = 0;
	private controller: AbortController | null = null;
	private disposed = false;

	begin(): { generation: number; signal: AbortSignal } {
		this.controller?.abort();
		this.controller = new AbortController();
		this.generation += 1;
		if (this.disposed) this.controller.abort();
		return { generation: this.generation, signal: this.controller.signal };
	}

	isCurrent(generation: number): boolean {
		return !this.disposed && generation === this.generation && !this.controller?.signal.aborted;
	}

	invalidate(): void {
		this.generation += 1;
		this.controller?.abort();
		this.controller = null;
	}

	dispose(): void {
		this.invalidate();
		this.disposed = true;
	}
}
