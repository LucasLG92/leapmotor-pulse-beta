<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { ApiError } from '../lib/api';
	import Glyph from '../lib/Glyph.svelte';
	import MapCanvas from '../lib/MapCanvas.svelte';
	import NavigationFavoritesPanel from '../lib/NavigationFavoritesPanel.svelte';
	import {
		connectorLabels,
		distanceMeters,
		isNavigationPlace,
		isNavigationStation,
		isPlainProviderText,
		isPublicPoint,
		makeManualPlace,
		mapLinks,
		parsePlaceSearchQuery,
		parsePublicCoordinate,
		RequestGeneration,
		sameMapArea,
		type PublicPoint
	} from '../lib/navigation';
	import type { NavigationChargers, NavigationPlace, NavigationSearch, NavigationStation, NavigationStatus, PageProps } from '../lib/types';

	let { online, navigation, theme, busy, revision, drafts, mutate, confirmAction, onSessionExpired }: Pick<PageProps, 'online' | 'navigation' | 'theme' | 'busy' | 'revision' | 'drafts' | 'mutate' | 'confirmAction' | 'onSessionExpired'> = $props();

	const statusGate = new RequestGeneration();
	const searchGate = new RequestGeneration();
	const chargersGate = new RequestGeneration();
	let status = $state<NavigationStatus | null>(null);
	let statusBusy = $state(false);
	let statusError = $state('');
	let authExpired = $state(false);
	let query = $state('');
	let places = $state<NavigationPlace[]>([]);
	let submittedQuery = $state('');
	let searchFetchedAt = $state('');
	let searchCached = $state(false);
	let searchBusy = $state(false);
	let searchError = $state('');
	let selectedPlace = $state<NavigationPlace | null>(null);
	let manualLatitude = $state('');
	let manualLongitude = $state('');
	let latitudeError = $state('');
	let longitudeError = $state('');
	let latitudeInput: HTMLInputElement | undefined;
	let longitudeInput: HTMLInputElement | undefined;
	let mapOpen = $state(false);
	let mapError = $state('');
	let currentCenter = $state<PublicPoint | null>(null);
	let stations = $state<NavigationStation[]>([]);
	let stationCenter = $state<PublicPoint | null>(null);
	let chargersFetchedAt = $state('');
	let chargersCached = $state(false);
	let chargersTruncated = $state(false);
	let chargersBusy = $state(false);
	let chargersError = $state('');
	let centerChangedNotice = $state('');
	let selectedStation = $state<NavigationStation | null>(null);
	let observedOnline: boolean | undefined;

	const googleLink = $derived(selectedPlace ? mapLinks(selectedPlace)?.google ?? '' : '');
	const appleLink = $derived(selectedPlace ? mapLinks(selectedPlace)?.apple ?? '' : '');
	const selectedStationLinks = $derived(selectedStation ? mapLinks(selectedStation) : null);
	const canUseProviders = $derived(online && status?.enabled === true && !statusBusy);
	const canSearchPlaces = $derived(canUseProviders && !searchBusy);
	const centerLabel = $derived(stationCenter ? coordinateLabel(stationCenter) : currentCenter ? coordinateLabel(currentCenter) : 'Selecciona un destino');

	onMount(() => {
		if (online) void loadStatus();
	});

	$effect(() => {
		const connected = online;
		if (observedOnline === undefined) {
			observedOnline = connected;
			return;
		}
		if (observedOnline === connected) return;
		observedOnline = connected;
		if (!connected) clearOfflineState();
		else {
			authExpired = false;
			void loadStatus();
		}
	});

	onDestroy(() => {
		clearSensitiveState();
		statusGate.dispose();
		searchGate.dispose();
		chargersGate.dispose();
	});

	async function loadStatus() {
		if (!online) return;
		const operation = statusGate.begin();
		statusBusy = true;
		statusError = '';
		try {
			const result = await navigation.status(operation.signal);
			if (!statusGate.isCurrent(operation.generation) || !online) return;
			if (!isValidStatus(result)) throw new Error('invalid-status');
			status = result;
			if (!result.enabled) {
				searchGate.invalidate();
				chargersGate.invalidate();
				searchBusy = false;
				places = [];
				submittedQuery = '';
				searchFetchedAt = '';
				searchCached = false;
				query = '';
				stations = [];
				selectedPlace = null;
				selectedStation = null;
				currentCenter = null;
				stationCenter = null;
				chargersBusy = false;
				mapOpen = false;
			}
		} catch (error) {
			if (!statusGate.isCurrent(operation.generation) || !online || isAbort(error)) return;
			if (isUnauthorized(error)) {
				clearSensitiveState();
				authExpired = true;
				statusError = 'La sesión ha caducado. Vuelve a iniciar sesión para continuar.';
			} else statusError = 'No se pudo consultar la disponibilidad de los servicios de navegación.';
			status = null;
		} finally {
			if (statusGate.isCurrent(operation.generation)) statusBusy = false;
		}
	}

	function isValidStatus(value: NavigationStatus): boolean {
		return Boolean(value && typeof value.enabled === 'boolean'
			&& value.source === 'OpenStreetMap'
			&& (value.reason === 'ready' || value.reason === 'disabled')
			&& value.attribution === '© OpenStreetMap contributors');
	}

	function setQuery(value: string) {
		if (value === query) return;
		query = value;
		searchGate.invalidate();
		searchBusy = false;
		places = [];
		submittedQuery = '';
		searchFetchedAt = '';
		searchCached = false;
		searchError = '';
		selectedPlace = null;
		currentCenter = null;
		mapOpen = false;
		mapError = '';
		invalidateChargers('El destino ha cambiado. Vuelve a consultar los cargadores para este lugar.');
	}

	async function searchPlaces(event: SubmitEvent) {
		event.preventDefault();
		const cleanQuery = parsePlaceSearchQuery(query);
		if (!cleanQuery) {
			searchError = 'Escribe una búsqueda de 3 a 120 caracteres.';
			return;
		}
		if (!canUseProviders) {
			searchError = status?.enabled === false
				? 'La búsqueda de lugares no está habilitada en Pulse. Puedes introducir las coordenadas públicas del lugar.'
				: 'Conéctate y comprueba el estado de navegación antes de buscar.';
			return;
		}
		searchGate.invalidate();
		chargersGate.invalidate();
		const operation = searchGate.begin();
		searchBusy = true;
		searchError = '';
		places = [];
		submittedQuery = cleanQuery;
		searchFetchedAt = '';
		searchCached = false;
		selectedPlace = null;
		currentCenter = null;
		selectedStation = null;
		mapOpen = false;
		mapError = '';
		clearChargers();
		try {
			const result = await navigation.search(cleanQuery, operation.signal);
			if (!searchGate.isCurrent(operation.generation) || !online || query.trim() !== cleanQuery) return;
			if (!isValidSearch(result)) throw new Error('invalid-search');
			places = result.places.filter(isNavigationPlace).slice(0, 5);
			if (result.places.length > 0 && places.length === 0) {
				searchError = 'El servicio devolvió resultados que Pulse no pudo validar. Prueba con otra búsqueda.';
				return;
			}
			searchFetchedAt = result.fetched_at;
			searchCached = result.cached;
		} catch (error) {
			if (!searchGate.isCurrent(operation.generation) || !online || isAbort(error)) return;
			const message = handleRequestFailure(error, 'No se pudo buscar este lugar. Vuelve a intentarlo.');
			if (!authExpired) searchError = message;
		} finally {
			if (searchGate.isCurrent(operation.generation)) searchBusy = false;
		}
	}

	function isValidSearch(value: NavigationSearch): boolean {
		return Boolean(value && value.source === 'OpenStreetMap Nominatim'
			&& isValidTimestamp(value.fetched_at)
			&& typeof value.cached === 'boolean'
			&& Array.isArray(value.places)
			&& value.places.length <= 5);
	}

	function choosePlace(place: NavigationPlace) {
		if (!isNavigationPlace(place)) return;
		searchGate.invalidate();
		searchBusy = false;
		selectedPlace = place;
		selectedStation = null;
		mapOpen = false;
		mapError = '';
		currentCenter = { latitude: place.latitude, longitude: place.longitude };
		invalidateChargers('El destino ha cambiado. Vuelve a consultar los cargadores para este lugar.');
	}

	async function chooseManualPlace(event: SubmitEvent) {
		event.preventDefault();
		latitudeError = parsePublicCoordinate(manualLatitude, -90, 90) === null
			? 'Introduce una latitud entre −90 y 90, con punto o coma decimal.' : '';
		longitudeError = parsePublicCoordinate(manualLongitude, -180, 180) === null
			? 'Introduce una longitud entre −180 y 180, con punto o coma decimal.' : '';
		const place = makeManualPlace(manualLatitude, manualLongitude);
		if (!place) {
			await tick();
			(latitudeError ? latitudeInput : longitudeInput)?.focus();
			return;
		}
		searchGate.invalidate();
		searchBusy = false;
		places = [];
		submittedQuery = '';
		searchFetchedAt = '';
		searchCached = false;
		searchError = '';
		choosePlace(place);
	}

	function clearDestination() {
		searchGate.invalidate();
		searchBusy = false;
		places = [];
		submittedQuery = '';
		searchFetchedAt = '';
		searchCached = false;
		selectedPlace = null;
		currentCenter = null;
		selectedStation = null;
		mapOpen = false;
		mapError = '';
		clearChargers();
	}

	function toggleMap() {
		if (!selectedPlace || !canUseProviders) return;
		if (mapOpen) mapOpen = false;
		else {
			mapError = '';
			mapOpen = true;
		}
	}

	function onMapCenter(latitude: number, longitude: number) {
		const point = { latitude, longitude };
		if (!isPublicPoint(point)) return;
		if (sameMapArea(currentCenter, point)) return;
		currentCenter = point;
		if (stationCenter || chargersBusy) invalidateChargers('El centro del mapa ha cambiado. Vuelve a consultar los cargadores.');
	}

	async function searchChargers() {
		if (!canUseProviders || !currentCenter || chargersBusy) return;
		const searchCenter = { ...currentCenter };
		chargersGate.invalidate();
		const operation = chargersGate.begin();
		chargersBusy = true;
		chargersError = '';
		centerChangedNotice = '';
		stations = [];
		stationCenter = null;
		chargersFetchedAt = '';
		chargersCached = false;
		chargersTruncated = false;
		selectedStation = null;
		try {
			const result = await navigation.chargers(searchCenter.latitude, searchCenter.longitude, operation.signal);
			if (!chargersGate.isCurrent(operation.generation) || !online || !sameMapArea(currentCenter, searchCenter)) return;
			if (!isValidChargers(result)) throw new Error('invalid-chargers');
			stations = result.stations.filter(isNavigationStation).slice(0, 60);
			if (result.stations.length > 0 && stations.length === 0) {
				chargersError = 'El servicio devolvió datos que Pulse no pudo validar. Vuelve a intentarlo más tarde.';
				return;
			}
			stationCenter = searchCenter;
			chargersFetchedAt = result.fetched_at;
			chargersCached = result.cached;
			chargersTruncated = result.truncated;
		} catch (error) {
			if (!chargersGate.isCurrent(operation.generation) || !online || isAbort(error)) return;
			const message = handleRequestFailure(error, 'No se pudieron consultar los cargadores de la zona. Vuelve a intentarlo.');
			if (!authExpired) chargersError = message;
		} finally {
			if (chargersGate.isCurrent(operation.generation)) chargersBusy = false;
		}
	}

	function isValidChargers(value: NavigationChargers): boolean {
		return Boolean(value && value.source === 'OpenStreetMap Overpass'
			&& isValidTimestamp(value.fetched_at)
			&& typeof value.cached === 'boolean'
			&& typeof value.truncated === 'boolean'
			&& Array.isArray(value.stations)
			&& value.stations.length <= 60);
	}

	function invalidateChargers(notice = '') {
		chargersGate.invalidate();
		chargersBusy = false;
		clearChargers();
		centerChangedNotice = notice;
	}

	function clearChargers() {
		chargersBusy = false;
		stations = [];
		stationCenter = null;
		chargersFetchedAt = '';
		chargersCached = false;
		chargersTruncated = false;
		chargersError = '';
		selectedStation = null;
	}

	function clearSensitiveState() {
		statusGate.invalidate();
		searchGate.invalidate();
		chargersGate.invalidate();
		status = null;
		statusBusy = false;
		statusError = '';
		searchBusy = false;
		searchError = '';
		places = [];
		submittedQuery = '';
		searchFetchedAt = '';
		searchCached = false;
		query = '';
		selectedPlace = null;
		currentCenter = null;
		manualLatitude = '';
		manualLongitude = '';
		latitudeError = '';
		longitudeError = '';
		mapOpen = false;
		mapError = '';
		chargersBusy = false;
		clearChargers();
		centerChangedNotice = '';
	}

	function clearOfflineState() {
		clearSensitiveState();
		authExpired = false;
		statusError = 'Sin conexión. Las búsquedas y los datos de esta vista se han borrado.';
	}

	function handleRequestFailure(error: unknown, fallback: string): string {
		if (isUnauthorized(error)) {
			clearSensitiveState();
			authExpired = true;
			const message = 'La sesión ha caducado. Vuelve a iniciar sesión para continuar.';
			statusError = message;
			return message;
		}
		if (!(error instanceof ApiError)) return fallback;
		if (error.status === 0) return 'Pulse no responde. Comprueba la conexión e inténtalo de nuevo.';
		if (error.status === 429) return 'El servicio está ocupado. Espera un momento antes de volver a intentarlo.';
		if (error.status === 503) return 'El servicio público no está habilitado en Pulse.';
		if (error.status === 504) return 'La consulta ha tardado demasiado. Puedes volver a intentarlo.';
		return fallback;
	}

	function isUnauthorized(error: unknown): boolean {
		return error instanceof ApiError && error.status === 401;
	}

	function isAbort(error: unknown): boolean {
		return error instanceof DOMException && error.name === 'AbortError';
	}

	function isValidTimestamp(value: unknown): value is string {
		return typeof value === 'string' && value.length <= 80 && Number.isFinite(Date.parse(value));
	}

	function coordinateLabel(point: PublicPoint): string {
		return `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`;
	}

	function formatDistance(from: PublicPoint, station: NavigationStation): string {
		const meters = distanceMeters(from, station);
		if (!Number.isFinite(meters)) return '—';
		return meters < 1000 ? `${meters} m` : `${(meters / 1000).toLocaleString('es-ES', { maximumFractionDigits: 1 })} km`;
	}

	function formatFetchedAt(value: string): string {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? 'Hora de consulta no disponible' : date.toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' });
	}

	function safeOptionalText(value: string | null | undefined): string {
		return value && isPlainProviderText(value) ? value : '';
	}

	function powerLabel(value: number | null | undefined): string {
		return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toLocaleString('es-ES', { maximumFractionDigits: 1 })} kW` : 'Potencia no indicada';
	}
</script>

<section class="page-intro premium-heading navigation-intro">
	<div class="navigation-intro-copy">
		<h2>Mapa</h2>
		<p>Destinos y coordenadas públicas.</p>
	</div>
</section>

{#if statusError}
	<div class="notice" class:notice-danger={authExpired} role={authExpired ? 'alert' : 'status'}>
		<span>{statusError}</span>
		{#if online && !authExpired}<button class="text-button" type="button" onclick={loadStatus}>Reintentar estado</button>{/if}
	</div>
{/if}

{#if statusBusy}
	<div class="status-loading" role="status">Consultando la disponibilidad de navegación…</div>
{/if}

<div class="navigation-layout" class:map-visible={mapOpen}>
	<div class="navigation-main-column">
		{#if status && !status.enabled}
			<section class="navigation-service-state" aria-labelledby="navigation-service-heading" role="status">
				<div class="service-state-art" aria-hidden="true">
					<svg viewBox="0 0 152 120" fill="none" focusable="false">
						<path d="M17 31 55 18l40 13 40-13v72l-40 13-40-13-38 13V31Z" />
						<path d="M55 18v72m40-59v72" />
					</svg>
				</div>
				<div class="service-state-copy">
					<p class="service-state-label">Lugares y mapas</p>
					<h3 id="navigation-service-heading">Búsqueda no disponible</h3>
					<p>Pulse tiene desactivados los servicios públicos de lugares, cargadores y mapas. Aún puedes preparar un destino con coordenadas públicas.</p>
					<a class="button button-secondary" href="/settings#preparation">Revisar conexiones</a>
				</div>
			</section>
		{:else}
			<section class="search-card card card-pad" aria-labelledby="places-heading">
			<div class="section-heading search-heading">
				<h3 id="places-heading">Buscar un destino</h3>
				<span class="source-chip">OpenStreetMap</span>
			</div>
			<form class="place-search-form" onsubmit={searchPlaces}>
				<div class="field search-field">
					<label for="navigation-query">Lugar que quieres buscar</label>
					<input id="navigation-query" type="search" maxlength="120" autocomplete="off" spellcheck="false" value={query} oninput={(event) => setQuery(event.currentTarget.value)} placeholder="Por ejemplo, museo del Prado" aria-describedby="search-help" disabled={!online || authExpired} />
					<p id="search-help" class="field-hint">Al pulsar Buscar, compartirás esta consulta con OpenStreetMap. Usa solo lugares públicos.</p>
				</div>
				<button class="button button-primary search-button" type="submit" disabled={!canSearchPlaces || query.trim().length < 3 || query.trim().length > 120}>{searchBusy ? 'Buscando…' : 'Buscar lugar'}</button>
			</form>
			{#if searchError}<p class="inline-error" role="alert">{searchError}</p>{/if}
			{#if submittedQuery && searchFetchedAt}
				<p class="result-provenance">Resultados de OpenStreetMap Nominatim para «{submittedQuery}». {searchCached ? 'Respuesta guardada en la caché del servicio.' : 'Consulta reciente.'} {formatFetchedAt(searchFetchedAt)}.</p>
			{/if}
			{#if places.length}
				<ul class="place-results" aria-label="Lugares encontrados">
					{#each places as place (place.id)}
						<li><button class="place-result" type="button" aria-pressed={selectedPlace?.id === place.id} onclick={() => choosePlace(place)}><span class="place-result-name">{place.name}</span><span class="place-result-coordinates">{coordinateLabel(place)}</span></button></li>
					{/each}
				</ul>
			{:else if submittedQuery && searchFetchedAt && !searchBusy}
				<div class="empty-search"><span class="empty-search-mark" aria-hidden="true"><Glyph name="search" size={17} /></span><div><h4>No se encontraron lugares</h4><p>Prueba con una ciudad o una referencia más concreta.</p></div></div>
			{/if}
		</section>
		{/if}
		{#if selectedPlace}
			<section class="destination-card card card-pad" aria-labelledby="destination-heading">
				<div class="section-heading destination-heading">
					<div><h3 id="destination-heading">{selectedPlace.name}</h3></div>
				<button class="text-button clear-destination" type="button" onclick={clearDestination}>Quitar</button>
				</div>
				<dl class="destination-coordinates"><div><dt>Latitud</dt><dd>{selectedPlace.latitude.toFixed(6)}</dd></div><div><dt>Longitud</dt><dd>{selectedPlace.longitude.toFixed(6)}</dd></div></dl>
				<div class="destination-actions">
					{#if status?.enabled !== false}<button class="button button-primary button-block" type="button" disabled={!canUseProviders} aria-expanded={mapOpen} aria-controls="navigation-map" onclick={toggleMap}>{mapOpen ? 'Ocultar mapa' : 'Mostrar mapa'}</button>{/if}
					<div class="external-map-links">
						<a class="button button-secondary" href={googleLink} target="_blank" rel="noopener noreferrer">Abrir en Google Maps</a>
						<a class="button button-secondary" href={appleLink} target="_blank" rel="noopener noreferrer">Abrir en Apple Maps</a>
					</div>
				</div>
				<p class="destination-sharing">Al abrir un mapa externo, compartirás las coordenadas de este destino con ese servicio.</p>
				{#if status?.enabled !== false}
					<div class="charger-center">
						<div><h4>Cargadores públicos</h4><p>Consulta puntos en un radio de 5 km del centro actual.</p><small>Centro: {centerLabel}</small></div>
						<button class="button button-secondary charger-search-button" type="button" disabled={!canUseProviders || !currentCenter || chargersBusy} onclick={searchChargers}>{chargersBusy ? 'Consultando…' : 'Buscar cargadores a 5 km'}</button>
					</div>
					{#if centerChangedNotice}<p class="center-notice" role="status">{centerChangedNotice}</p>{/if}
					{#if chargersError}<p class="inline-error" role="alert">{chargersError}</p>{/if}
					{#if chargersFetchedAt}
						<p class="result-provenance">OpenStreetMap Overpass · {chargersCached ? 'caché del servicio' : 'consulta reciente'} · {formatFetchedAt(chargersFetchedAt)}.</p>
					{/if}
					{#if chargersTruncated}<p class="center-notice">La consulta alcanzó su límite de resultados. La lista puede estar incompleta.</p>{/if}
					{#if stations.length}
						<ul class="station-list" aria-label="Cargadores públicos encontrados">
							{#each stations as station (station.id)}
								<li><article class="station-card" class:station-selected={selectedStation?.id === station.id}>
									<div class="station-heading"><h5>{station.name}</h5><span>{stationCenter ? formatDistance(stationCenter, station) : '—'}</span></div>
									{#if safeOptionalText(station.operator)}<p class="station-operator">{safeOptionalText(station.operator)}</p>{/if}
									<dl class="station-facts"><div><dt>Potencia</dt><dd>{powerLabel(station.power_kw)}</dd></div><div><dt>Conectores</dt><dd>{connectorLabels(station.connectors)}</dd></div></dl>
									{#if safeOptionalText(station.address)}<p class="station-address">{safeOptionalText(station.address)}</p>{/if}
									<button class="text-button station-select" type="button" aria-pressed={selectedStation?.id === station.id} onclick={() => selectedStation = station}>{selectedStation?.id === station.id ? 'Punto seleccionado' : 'Seleccionar punto'}</button>
								</article></li>
							{/each}
						</ul>
					{:else if chargersFetchedAt && !chargersBusy}
						<div class="empty-search"><h4>No hay cargadores en la lista</h4><p>La consulta no devolvió puntos en el radio indicado.</p></div>
					{/if}
					<p class="fine-print station-limit">La distancia es en línea recta desde el centro consultado; no indica distancia por carretera ni disponibilidad u ocupación en tiempo real.</p>
				{/if}
				{#if selectedStation && selectedStationLinks}
					<div class="selected-station-note">
						<p role="status"><strong>Cargador seleccionado:</strong> {selectedStation.name}. El destino elegido en Pulse no cambia.</p>
						<div class="external-map-links station-map-links">
							<a class="button button-secondary" href={selectedStationLinks.google} target="_blank" rel="noopener noreferrer">Abrir este cargador en Google Maps</a>
							<a class="button button-secondary" href={selectedStationLinks.apple} target="_blank" rel="noopener noreferrer">Abrir este cargador en Apple Maps</a>
						</div>
						<p class="destination-sharing">Al abrir un enlace, compartirás con ese servicio las coordenadas de este cargador.</p>
					</div>
				{/if}
			</section>
		{:else if status?.enabled !== false}
			<section class="destination-empty card" aria-labelledby="destination-empty-heading">
				<h3 id="destination-empty-heading">Tu destino aparecerá aquí</h3>
				<p>Elige un resultado o introduce coordenadas públicas para preparar enlaces y consultar cargadores.</p>
			</section>
		{/if}
		<section class="manual-card card card-pad" aria-labelledby="manual-heading">
			<div class="section-heading manual-heading">
				<div><h3 id="manual-heading" class="navigation-section-title">Usar coordenadas</h3><p>{status?.enabled === false ? 'Introduce las coordenadas públicas de un lugar.' : 'Alternativa manual para encontrar un lugar público.'}</p></div>
			</div>
			<form class="manual-form" onsubmit={chooseManualPlace}>
				<div class="field">
					<label for="public-latitude">Latitud</label>
					<input id="public-latitude" name="latitude" bind:this={latitudeInput} inputmode="decimal" autocomplete="off" value={manualLatitude} oninput={(event) => { manualLatitude = event.currentTarget.value; latitudeError = ''; }} placeholder="40.4168" aria-invalid={latitudeError ? 'true' : undefined} aria-describedby={latitudeError ? 'coordinates-help latitude-error' : 'coordinates-help'} disabled={!online || authExpired} />
					{#if latitudeError}<p id="latitude-error" class="inline-error" role="alert">{latitudeError}</p>{/if}
				</div>
				<div class="field">
					<label for="public-longitude">Longitud</label>
					<input id="public-longitude" name="longitude" bind:this={longitudeInput} inputmode="decimal" autocomplete="off" value={manualLongitude} oninput={(event) => { manualLongitude = event.currentTarget.value; longitudeError = ''; }} placeholder="-3.7038" aria-invalid={longitudeError ? 'true' : undefined} aria-describedby={longitudeError ? 'coordinates-help longitude-error' : 'coordinates-help'} disabled={!online || authExpired} />
					{#if longitudeError}<p id="longitude-error" class="inline-error" role="alert">{longitudeError}</p>{/if}
				</div>
				<button class="button button-secondary manual-submit" type="submit" disabled={!online || authExpired}>Usar estas coordenadas</button>
			</form>
			<p id="coordinates-help" class="field-hint coordinates-hint">Puedes usar punto o coma decimal. Estas coordenadas no representan la posición del vehículo.</p>
		</section>
	</div>

	<aside class="navigation-side-column" aria-label="Mapa y lugares guardados">
				{#if mapOpen && selectedPlace}
	<section class="map-card card" id="navigation-map" aria-labelledby="map-heading">
				<div class="map-card-heading">
					<div><h3 id="map-heading" class="navigation-section-title"><Glyph name="navigation" size={16} />Mapa</h3><p>{selectedPlace.name}</p></div>
					<button class="button button-secondary button-small" type="button" onclick={() => mapOpen = false}>Ocultar mapa</button>
				</div>
				<MapCanvas point={selectedPlace} {stations} tile={navigation.tile} onCenter={onMapCenter} onSelectStation={(station) => selectedStation = station} onError={(message) => mapError = message} {theme} />
				{#if mapError}<p class="map-feedback" role="status">{mapError}</p>{/if}
				<p class="fine-print map-guidance">Usa los botones del mapa para acercar o alejar. La rueda del ratón sigue desplazando la página; mover el mapa no consulta cargadores.</p>
			</section>
		{/if}
				<NavigationFavoritesPanel {busy} {online} {revision} {drafts} {mutate} {confirmAction} {onSessionExpired} />
				<div class="notice navigation-limit-note"><span>Los datos de lugares y cargadores vienen de OpenStreetMap. No se consulta la ubicación del vehículo y no se presenta una ruta ni disponibilidad en tiempo real.</span></div>
	</aside>
</div>
<style>
	.navigation-intro { align-items: flex-start; margin-bottom: 18px; }
	.navigation-intro-copy { display: grid; justify-items: start; gap: 5px; }
	.navigation-intro h2 { margin: 0; font-size: clamp(1.375rem, 2.6vw, 1.85rem); }
	.navigation-intro p { max-width: 70ch; }
	.status-loading { display: flex; min-height: 44px; align-items: center; gap: 9px; margin: 0 0 14px; color: var(--muted-strong); font-size: .875rem; }
	.status-loading { padding-left: 2px; }
	.navigation-layout { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(18rem, .9fr); align-items: start; gap: 16px; }
	.navigation-main-column, .navigation-side-column { display: grid; min-width: 0; gap: 16px; }
	.navigation-layout.map-visible { grid-template-columns: minmax(0, 1.38fr) minmax(19rem, .82fr); }
	.search-card, .manual-card, .destination-card, .map-card { min-width: 0; }
	.search-heading, .manual-heading { align-items: flex-start; margin: 0 0 15px; }
	.section-heading h3 { margin: 0; font-size: 1.125rem; font-weight: 650; letter-spacing: -.025em; }
	.navigation-section-title { display: flex; align-items: center; gap: 8px; }
	.navigation-section-title :global(svg) { flex: 0 0 auto; color: var(--forest); }
	.section-heading p { max-width: 65ch; font-size: .875rem; line-height: 1.5; }
	.source-chip { display: inline-flex; min-height: 30px; align-items: center; padding: 0 9px; border: 1px solid var(--line); border-radius: 99px; background: var(--surface-soft); color: var(--muted-strong); font-size: .75rem; font-weight: 650; white-space: nowrap; }
	.place-search-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 10px; }
	.search-field { min-width: 0; }
	.search-button { min-width: 135px; }
	.place-results, .station-list { display: grid; gap: 7px; margin: 15px 0 0; padding: 0; list-style: none; }
	.place-result { display: grid; width: 100%; min-height: 58px; gap: 3px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); cursor: pointer; text-align: left; }
	.place-result:hover, .place-result[aria-pressed="true"] { border-color: var(--forest); background: var(--forest-soft); }
	.place-result-name { font-size: .875rem; font-weight: 650; }
	.place-result-coordinates { color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.result-provenance { margin: 10px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.inline-error { margin: 10px 0 0; color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.empty-search { display: flex; align-items: flex-start; gap: 10px; margin-top: 14px; padding: 14px; border: 1px dashed var(--line-strong); border-radius: 10px; background: var(--surface-soft); }
	.empty-search-mark { display: grid; width: 34px; height: 34px; flex: 0 0 34px; place-items: center; border-radius: 10px; background: var(--surface); color: var(--forest); }
	.empty-search-mark :global(svg) { display: block; }
	.empty-search h4 { margin: 0; font-size: .875rem; }
	.empty-search p { margin: 5px 0 0; color: var(--muted); font-size: .875rem; }
	.manual-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 10px; }
	.manual-submit { grid-column: 1 / -1; justify-self: start; }
	.destination-empty { display: grid; justify-items: start; gap: 11px; min-height: 240px; align-content: center; padding: 22px; }
	.destination-empty h3 { margin: 0; font-size: 1rem; }
	.destination-empty p { max-width: 50ch; margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.6; }
	.destination-heading { align-items: flex-start; margin: 0 0 12px; }
	.destination-heading h3 { max-width: 35ch; margin-top: 5px; font-size: 1.125rem; overflow-wrap: anywhere; }
	.clear-destination { flex: 0 0 auto; }
	.destination-coordinates, .station-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0; }
	.destination-coordinates div, .station-facts div { min-width: 0; padding: 9px 10px; border-radius: 9px; background: var(--surface-soft); }
	.destination-coordinates dt, .station-facts dt { color: var(--muted); font-size: .75rem; }
	.destination-coordinates dd, .station-facts dd { margin: 3px 0 0; color: var(--ink); font-size: .875rem; font-weight: 620; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
	.destination-actions { display: grid; gap: 8px; margin-top: 13px; }
	.external-map-links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
	.external-map-links .button { min-width: 0; padding: 8px 10px; }
	.destination-sharing { margin: 9px 0 0; color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.charger-center { display: grid; gap: 10px; margin-top: 17px; padding-top: 15px; border-top: 1px solid var(--line); }
	.charger-center h4 { margin: 0; font-size: .9375rem; }
	.charger-center p { margin: 4px 0 2px; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.charger-center small { color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.charger-search-button { justify-self: start; }
	.center-notice, .selected-station-note { margin: 10px 0 0; padding: 9px 10px; border-radius: 9px; background: var(--accent-soft); color: var(--muted-strong); font-size: .75rem; line-height: 1.5; }
	.selected-station-note > p { margin: 0; }
	.selected-station-note .station-map-links { grid-template-columns: minmax(0, 1fr); margin-top: 8px; }
	.selected-station-note .station-map-links .button { min-height: 48px; white-space: normal; }
	.selected-station-note .destination-sharing { margin-top: 7px; }
	.station-card { min-width: 0; padding: 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-raised); }
	.station-card.station-selected { border-color: var(--forest); background: var(--forest-soft); }
	.station-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
	.station-heading h5 { min-width: 0; margin: 0; font-size: .875rem; font-weight: 650; }
	.station-heading > span { flex: 0 0 auto; color: var(--forest); font-size: .875rem; font-weight: 700; font-variant-numeric: tabular-nums; }
	.station-operator, .station-address { margin: 5px 0 9px; color: var(--muted); font-size: .75rem; line-height: 1.5; }
	.station-facts { margin-top: 9px; }
	.station-facts div { padding: 7px 8px; }
	.station-facts dd { font-size: .75rem; }
	.station-select { margin-top: 3px; }
	.station-limit { margin: 12px 0 0; }
	.map-card { padding: 14px; }
	.map-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
	.map-card-heading h3 { margin: 0; font-size: 1rem; }
	.map-card-heading p { margin: 4px 0 0; color: var(--muted); font-size: .875rem; }
	.map-guidance { margin: 9px 2px 0; }
	.map-feedback { margin: 9px 0 0; color: var(--muted-strong); font-size: .875rem; }
	.navigation-limit-note { font-size: .875rem; }
	.destination-card { display: grid; gap: 18px; }
	.destination-heading h3 { font-size: clamp(1.25rem, 2vw, 1.65rem); letter-spacing: -.025em; }
	.destination-coordinates { padding-top: 11px; border-top: 1px solid var(--line); }
	:global(.navigation-side-column > .favorites-panel) { margin-top: 3px; padding-top: 22px; border-top: 1px solid var(--line); }
	@media (max-width: 1000px) {
		.navigation-layout, .navigation-layout.map-visible { grid-template-columns: minmax(0, 1fr); }
		.navigation-side-column { grid-template-columns: minmax(0, 1fr); }
	}
	@media (max-width: 560px) {
		.place-search-form { grid-template-columns: minmax(0, 1fr); }
		.search-button { width: 100%; }
		.manual-form { grid-template-columns: minmax(0, 1fr); }
		.manual-submit { width: 100%; }
		.external-map-links { grid-template-columns: minmax(0, 1fr); }
		.external-map-links .button { width: 100%; }
		.destination-coordinates, .station-facts { grid-template-columns: minmax(0, 1fr); }
		.map-card { padding: 9px; }
		.map-card-heading { align-items: flex-start; flex-direction: column; }
		.map-card-heading .button { width: 100%; }
		.navigation-intro { margin-bottom: 15px; }
	}

	.navigation-layout,.navigation-layout.map-visible{grid-template-columns:minmax(280px,.85fr) minmax(0,1.15fr);gap:32px}.navigation-main-column,.navigation-side-column{gap:24px}.search-card,.manual-card,.destination-card{padding:0 0 24px;border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent}.place-results,.station-list{gap:0}.place-result,.station-card{border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent;padding:16px 0}.place-result:hover,.place-result[aria-pressed="true"]{background:var(--forest-soft)}.station-facts div,.destination-coordinates div{border-radius:0;background:transparent;padding:8px 0}.destination-empty{padding:24px 0;min-height:280px;border:0;border-radius:0;background:transparent}.destination-empty h3{font-size:1.25rem}.empty-search{border:0;padding:16px 0;border-radius:0;background:transparent}.map-card{border-radius:18px}.source-chip{background:var(--forest-soft);color:var(--forest);border:0}.manual-heading,.search-heading{margin-bottom:16px}.station-address,.station-operator{font-size:.8125rem}
	@media(max-width:1000px){.navigation-layout,.navigation-layout.map-visible{grid-template-columns:minmax(0,1fr)}.navigation-main-column,.navigation-side-column{gap:24px}}
	@media(max-width:560px){.destination-empty{min-height:200px}.station-facts{grid-template-columns:repeat(2,minmax(0,1fr))}.navigation-layout{gap:24px}}

	.navigation-intro { align-items: flex-start; }
	.navigation-intro h2 { font-size: clamp(1.6rem, 3vw, 2.2rem); }
	.navigation-layout, .navigation-layout.map-visible { grid-template-columns: minmax(0, 1.3fr) minmax(18rem, .7fr); align-items: start; gap: clamp(24px, 4vw, 42px); }
	.navigation-main-column, .navigation-side-column { gap: 26px; min-width: 0; }
	.navigation-main-column > .search-card, .navigation-main-column > .manual-card, .navigation-main-column > .destination-card, .navigation-main-column > .destination-empty {
		min-width: 0; padding: 0 0 24px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none;
	}
	.navigation-main-column > .search-card { order: 0; }
	.navigation-main-column > .destination-card, .navigation-main-column > .destination-empty { order: 1; }
	.navigation-main-column > .manual-card { order: 2; }
	.navigation-main-column > .destination-empty { min-height: 0; align-content: start; }
	.manual-heading, .search-heading { margin-bottom: 14px; }
	.navigation-side-column { align-content: start; }
	.navigation-side-column > .map-card { margin: 0; padding: 12px; }
	.coordinates-hint { max-width: 65ch; }
	.charger-search-button { justify-self: stretch; }
	@media (max-width: 1000px) {
		.navigation-layout, .navigation-layout.map-visible { grid-template-columns: minmax(0, 1fr); }
		.navigation-side-column { grid-template-columns: minmax(0, 1fr); }
	}
	@media (max-width: 560px) {
		.navigation-layout, .navigation-layout.map-visible { gap: 24px; }
		.navigation-main-column > .search-card, .navigation-main-column > .manual-card, .navigation-main-column > .destination-card, .navigation-main-column > .destination-empty { padding-bottom: 19px; }
		.charger-search-button { width: 100%; }
	}

	/* La búsqueda dirige la vista; el servicio desactivado ofrece un estado honesto y la alternativa manual. */
	.navigation-intro { margin-bottom: 18px; }
	.navigation-intro-copy { gap: 2px; }
	.navigation-intro p { margin-top: 3px; font-size: .875rem; }
	.navigation-layout { gap: clamp(24px, 3.4vw, 44px); }
	.navigation-main-column, .navigation-side-column { gap: 24px; }
	.navigation-main-column > .search-card {
		order: 0; min-width: 0; padding: clamp(20px, 2.8vw, 30px); border: 1px solid var(--line); border-radius: 28px; background: var(--surface); box-shadow: var(--shadow);
	}
	.search-heading { align-items: center; justify-content: space-between; margin-bottom: 19px; }
	.search-heading h3 { font-size: clamp(1.2rem, 2vw, 1.55rem); font-weight: 700; letter-spacing: -.04em; }
	.source-chip { min-height: 32px; background: var(--surface-soft); color: var(--muted-strong); }
	.place-search-form { gap: 12px; }
	.search-field { gap: 8px; }
	.search-field label { font-size: .9375rem; }
	.search-field input { min-height: 56px; border-radius: 14px; }
	.search-field .field-hint { margin-top: 0; font-size: .8125rem; }
	.search-button { min-height: 56px; padding-inline: 20px; border-radius: 14px; }
	.navigation-service-state {
		display: grid; grid-template-columns: minmax(120px, 168px) minmax(0, 1fr); align-items: center; gap: clamp(20px, 3vw, 34px); min-width: 0; padding: clamp(22px, 3vw, 34px); border: 1px solid var(--line); border-radius: 28px; background: var(--surface); box-shadow: var(--shadow);
	}
	.service-state-art { display: grid; width: 100%; min-height: 132px; place-items: center; border-radius: 24px; background: var(--surface-soft); color: var(--forest); }
	.service-state-art svg { display: block; width: min(100%, 150px); height: auto; }
	.service-state-art path { stroke: currentColor; stroke-width: 2.4; stroke-linecap: round; stroke-linejoin: round; }
	.service-state-copy { display: grid; justify-items: start; gap: 7px; min-width: 0; }
	.service-state-label { margin: 0; color: var(--forest); font-size: .75rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
	.service-state-copy h3 { margin: 0; font-size: clamp(1.25rem, 2.2vw, 1.75rem); letter-spacing: -.035em; line-height: 1.15; }
	.service-state-copy > p:not(.service-state-label) { max-width: 54ch; margin: 0 0 6px; color: var(--muted); font-size: .9375rem; line-height: 1.55; }
	.service-state-copy .button { min-height: 48px; border-radius: 14px; }
	.navigation-main-column > .destination-card {
		order: 1; min-width: 0; padding: clamp(20px, 2.8vw, 30px); border: 1px solid var(--line); border-radius: 28px; background: var(--surface); box-shadow: var(--shadow);
	}
	.navigation-main-column > .destination-empty { order: 1; min-height: 0; align-content: start; padding: 14px 2px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.destination-empty h3 { font-size: 1rem; }
	.destination-empty p { font-size: .875rem; }
	.navigation-main-column > .manual-card { order: 2; min-width: 0; padding: 0 0 20px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.manual-heading { margin-bottom: 12px; }
	.manual-heading .navigation-section-title { font-size: 1rem; }
	.manual-heading p { margin-top: 3px; font-size: .8125rem; }
	.manual-form { gap: 12px; }
	.manual-form .field { gap: 6px; }
	.manual-form .field input { min-height: 50px; border-radius: 12px; }
	.manual-submit { min-height: 48px; border-radius: 13px; }
	.coordinates-hint { margin-top: 8px; font-size: .75rem; }
	.destination-coordinates div, .station-facts div { padding-inline: 0; }
	.station-card { border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; }
	.station-card.station-selected { border-color: var(--forest); background: var(--forest-soft); }
	.navigation-side-column > .map-card { padding: 16px; border: 1px solid var(--line); border-radius: 26px; background: var(--surface); }
	:global(.navigation-side-column > .favorites-panel) { margin-top: 0; padding-top: 22px; }
	.navigation-limit-note { padding: 13px 15px; border-radius: 20px; }
	@media (max-width: 620px) {
		.navigation-main-column > .search-card { padding: 21px 18px; }
		.place-search-form { grid-template-columns: minmax(0, 1fr); }
		.search-button { width: 100%; }
		.navigation-service-state { grid-template-columns: minmax(0, 1fr); gap: 18px; padding: 20px; }
		.service-state-art { width: 100%; min-height: 112px; }
		.service-state-art svg { width: min(100%, 128px); max-height: 104px; }
		.service-state-copy .button { width: 100%; }
		.navigation-main-column > .destination-card { padding: 20px 18px; }
		.navigation-main-column > .destination-empty, .navigation-main-column > .manual-card { padding-bottom: 18px; }
		.manual-form { grid-template-columns: minmax(0, 1fr); }
		.manual-submit { width: 100%; }
	}
</style>
