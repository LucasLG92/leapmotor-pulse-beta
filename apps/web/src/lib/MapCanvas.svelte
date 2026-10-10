<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { Coords, DoneCallback, Map as LeafletMap, TileEvent } from 'leaflet';
	import { cancelTileJob, isSafeTileDataUrl, takeNextCurrentTileJob, type PublicPoint } from './navigation';
	import type { NavigationPlace, NavigationStation, NavigationTile, PageProps } from './types';

	let {
		point,
		stations,
		tile,
		onCenter,
		onSelectStation,
		onError,
		theme
	}: {
		point: NavigationPlace | null;
		stations: NavigationStation[];
		tile: PageProps['navigation']['tile'];
		onCenter: (latitude: number, longitude: number) => void;
		onSelectStation: (station: NavigationStation) => void;
		onError: (message: string) => void;
		theme: PageProps['theme'];
	} = $props();

	let mapHost: HTMLDivElement;
	let map = $state<LeafletMap | null>(null);
	let mapBusy = $state(true);
	let mapReady = $state(false);
	let loadError = $state('');
	let disposed = false;
	let suppressTilePump = false;
	let leaflet: typeof import('leaflet') | null = null;
	let stationLayer: import('leaflet').LayerGroup | null = null;
	type LifecycleGridLayer = import('leaflet').GridLayer & {
		invalidateLoadingTiles(): void;
		isTileCurrent(coords: Coords): boolean;
	};
	let gridLayer: LifecycleGridLayer | null = null;
	let markersSignature = '';
	interface TileJob {
		image: HTMLImageElement;
		coords: Coords;
		done: DoneCallback;
		controller: AbortController | null;
		cancelled: boolean;
	}
	const maxActiveTiles = 2;
	const maxQueuedTiles = 64;
	const tileJobs = new WeakMap<HTMLElement, TileJob>();
	const pendingTileJobs: TileJob[] = [];
	const activeTileJobs = new Set<TileJob>();

	onMount(() => {
		void initializeMap();
	});

	$effect(() => {
		const currentMap = map;
		const currentPoint = point;
		const currentStations = stations;
		if (!currentMap || !leaflet || !currentPoint) return;
		const signature = `${currentPoint.id}:${currentPoint.latitude},${currentPoint.longitude}|${currentStations.map((station) => `${station.id}:${station.latitude},${station.longitude}:${station.name}`).join('|')}`;
		if (signature === markersSignature) return;
		markersSignature = signature;
		updateMarkers(currentMap, currentPoint, currentStations);
	});

	onDestroy(() => {
		disposed = true;
		for (const job of pendingTileJobs) cancelTileJob(job);
		pendingTileJobs.length = 0;
		for (const job of activeTileJobs) {
			cancelTileJob(job);
		}
		activeTileJobs.clear();
		map?.remove();
		map = null;
		gridLayer = null;
		stationLayer = null;
		leaflet = null;
	});

	async function initializeMap() {
		mapBusy = true;
		loadError = '';
		try {
			const [leafletModule] = await Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')]);
			if (disposed || !mapHost || !point) return;
			leaflet = leafletModule;
			const instance = leafletModule.map(mapHost, {
				center: [point.latitude, point.longitude],
				zoom: 13,
				minZoom: 0,
				maxZoom: 18,
				keyboard: true,
				zoomControl: false,
				attributionControl: false,
				scrollWheelZoom: false,
				preferCanvas: true
			});
			map = instance;
			const grid = createNavigationGridLayer(leafletModule);
			gridLayer = grid;
			grid.on('tileunload', (event: TileEvent) => {
				const tileElement = event.tile;
				const job = tileJobs.get(tileElement);
				if (job) {
					cancelTileJob(job);
					const pendingIndex = pendingTileJobs.indexOf(job);
					if (pendingIndex >= 0) pendingTileJobs.splice(pendingIndex, 1);
					tileJobs.delete(tileElement);
				}
				const image = tileElement as HTMLImageElement;
				image.removeAttribute('src');
				if (!suppressTilePump) pumpTileQueue();
			});
			grid.addTo(instance);
			stationLayer = leafletModule.layerGroup().addTo(instance);
			instance.on('movestart', () => {
				suppressTilePump = true;
				try {
					grid.invalidateLoadingTiles();
				} finally {
					suppressTilePump = false;
				}
			});
			instance.on('moveend', reportCenter);
			instance.whenReady(() => {
				if (disposed) return;
				instance.invalidateSize({ pan: false });
				updateMarkers(instance, point!, stations);
				markersSignature = `${point!.id}:${point!.latitude},${point!.longitude}|${stations.map((station) => `${station.id}:${station.latitude},${station.longitude}:${station.name}`).join('|')}`;
				mapReady = true;
				mapBusy = false;
				reportCenter();
			});
		} catch {
			if (disposed) return;
			loadError = 'No se pudo abrir el mapa. Puedes seguir usando la búsqueda y las listas.';
			mapBusy = false;
			onError(loadError);
		}
	}

	function createTileElement(coords: Coords, done: DoneCallback): HTMLElement {
		const image = document.createElement('img');
		image.className = 'pulse-map-tile';
		image.alt = '';
		image.setAttribute('aria-hidden', 'true');
		image.decoding = 'async';
		image.draggable = false;
		const job: TileJob = { image, coords, done, controller: null, cancelled: false };
		tileJobs.set(image, job);
		if (pendingTileJobs.length >= maxQueuedTiles && activeTileJobs.size >= maxActiveTiles) {
			job.cancelled = true;
			queueMicrotask(() => done(new Error('tile-queue-full'), image));
			onError('El mapa ha alcanzado el límite de consultas visibles. Acerca o aleja el mapa para volver a intentarlo.');
			return image;
		}
		pendingTileJobs.push(job);
		queueMicrotask(pumpTileQueue);
		return image;
	}

	function createNavigationGridLayer(L: typeof import('leaflet')): LifecycleGridLayer {
		const PulseGridLayer = L.GridLayer.extend({
			createTile(coords: Coords, done: DoneCallback): HTMLElement {
				return createTileElement(coords, done);
			},
			invalidateLoadingTiles(this: {
				_tiles: Record<string, { loaded?: unknown }>;
				_removeTile(key: string): void;
			}) {
				for (const [key, tile] of Object.entries(this._tiles)) {
					if (!tile.loaded) this._removeTile(key);
				}
			},
			isTileCurrent(this: {
				_tiles: Record<string, { current?: boolean }>;
				_tileCoordsToKey(coords: Coords): string;
			}, coords: Coords): boolean {
				return this._tiles[this._tileCoordsToKey(coords)]?.current === true;
			}
		}) as unknown as new (options: import('leaflet').GridLayerOptions) => LifecycleGridLayer;
		return new PulseGridLayer({
			tileSize: 256,
			keepBuffer: 0,
			updateWhenIdle: true,
			updateWhenZooming: false,
			noWrap: true,
			minZoom: 0,
			maxZoom: 18,
			className: 'pulse-navigation-tiles'
		});
	}

	function pumpTileQueue() {
		while (!disposed && !suppressTilePump && activeTileJobs.size < maxActiveTiles && pendingTileJobs.length) {
			const job = takeNextCurrentTileJob(
				pendingTileJobs,
				(candidate) => !candidate.cancelled && gridLayer?.isTileCurrent(candidate.coords) === true,
				cancelTileJob
			);
			if (!job) break;
			startTileJob(job);
		}
	}

	function startTileJob(job: TileJob) {
		const controller = new AbortController();
		job.controller = controller;
		activeTileJobs.add(job);
		void tile(job.coords.z, job.coords.x, job.coords.y, controller.signal).then((result: NavigationTile) => {
			if (disposed || job.cancelled || controller.signal.aborted || !map || !job.image.isConnected) return;
			if (!isSafeTileDataUrl(result.data_url)) throw new Error('invalid-tile');
			job.image.addEventListener('load', () => job.done(undefined, job.image), { once: true });
			job.image.addEventListener('error', () => job.done(new Error('tile-load'), job.image), { once: true });
			job.image.src = result.data_url;
		}).catch(() => {
			if (disposed || job.cancelled || controller.signal.aborted || !job.image.isConnected) return;
			onError('No se pudo cargar una parte del mapa. Vuelve a abrir el mapa para intentarlo de nuevo.');
			job.done(new Error('tile-unavailable'), job.image);
		}).finally(() => {
			activeTileJobs.delete(job);
			job.controller = null;
			pumpTileQueue();
		});
	}

	function updateMarkers(instance: LeafletMap, destination: NavigationPlace, chargerStations: NavigationStation[]) {
		if (!leaflet || !stationLayer) return;
		stationLayer.clearLayers();
		const destinationMarker = leaflet.marker([destination.latitude, destination.longitude], {
			icon: markerIcon('destination'),
			title: `Destino: ${destination.name}`,
			keyboard: true,
			alt: `Destino: ${destination.name}`,
			zIndexOffset: 1000
		});
		destinationMarker.addTo(stationLayer);
		for (const station of chargerStations) {
			const marker = leaflet.marker([station.latitude, station.longitude], {
				icon: markerIcon('station'),
				title: station.name,
				alt: station.name,
				keyboard: true
			});
			marker.on('click', () => onSelectStation(station));
			marker.addTo(stationLayer);
		}
		void instance;
	}

	function markerIcon(kind: 'destination' | 'station') {
		if (!leaflet) throw new Error('map-not-ready');
		const pin = document.createElement('span');
		pin.className = `pulse-map-marker pulse-map-marker-${kind}`;
		pin.setAttribute('aria-hidden', 'true');
		const center = document.createElement('span');
		center.className = 'pulse-map-marker-core';
		pin.append(center);
		return leaflet.divIcon({ className: 'pulse-map-marker-host', html: pin, iconSize: [44, 44], iconAnchor: [22, 37] });
	}

	function reportCenter() {
		if (!map) return;
		const current = map.getCenter();
		onCenter(current.lat, current.lng);
	}

	function setCenterToDestination() {
		if (!map || !point) return;
		map.setView([point.latitude, point.longitude], Math.max(map.getZoom(), 13), { animate: false });
	}

	function zoomIn() { map?.zoomIn(); }
	function zoomOut() { map?.zoomOut(); }
</script>

<section class="map-canvas" data-theme={theme} aria-label="Mapa de lugares públicos">
	<div class="map-controls" aria-label="Controles del mapa">
		<button class="map-control" type="button" aria-label="Acercar mapa" title="Acercar" disabled={!mapReady} onclick={zoomIn}>+</button>
		<button class="map-control" type="button" aria-label="Alejar mapa" title="Alejar" disabled={!mapReady} onclick={zoomOut}>−</button>
		<button class="map-control map-center-control" type="button" disabled={!mapReady || !point} onclick={setCenterToDestination}>Centrar en el destino</button>
	</div>
	<div id="navigation-map" bind:this={mapHost} class="map-surface" role="application" aria-label={point ? `Mapa interactivo centrado en ${point.name}` : 'Mapa interactivo'}></div>
	{#if mapBusy}<p class="map-status" role="status">Preparando el mapa…</p>{/if}
	{#if loadError}<p class="map-error" role="alert">{loadError}</p>{/if}
	<div class="map-attribution">
		<span>© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a></span>
		<span aria-hidden="true">·</span>
		<a href="https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE" target="_blank" rel="noopener noreferrer">Leaflet 1.9.4 · BSD-2-Clause</a>
	</div>
</section>

<style>
	.map-canvas { position: relative; overflow: hidden; min-width: 0; height: clamp(25rem, 58svh, 42rem); border: 1px solid var(--line-strong); border-radius: 14px; background: var(--surface-soft); isolation: isolate; }
	.map-surface { position: absolute; inset: 0; z-index: 1; background: var(--surface-soft); color: var(--ink); }
	:global(.pulse-navigation-tiles) { z-index: 200; }
	:global(.pulse-map-tile) { width: 256px; height: 256px; border: 0; }
	:global(.pulse-map-marker-host) { display: grid; place-items: center; border: 0; background: transparent; }
	:global(.pulse-map-marker) { position: relative; display: grid; width: 30px; height: 38px; place-items: start center; filter: drop-shadow(0 2px 3px rgb(15 30 20 / 35%)); }
	:global(.pulse-map-marker::before) { position: absolute; inset: 2px 1px 6px; content: ""; transform: rotate(-45deg); border: 2px solid var(--surface); border-radius: 14px 14px 14px 2px; background: var(--forest); }
	:global(.pulse-map-marker-station::before) { background: var(--accent); }
	:global(.pulse-map-marker-core) { position: relative; z-index: 1; width: 9px; height: 9px; margin-top: 8px; border-radius: 50%; background: var(--surface); }
	.map-controls { position: absolute; z-index: 1000; top: 12px; right: 12px; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; max-width: calc(100% - 24px); }
	.map-control { display: inline-flex; min-width: 44px; min-height: 44px; align-items: center; justify-content: center; padding: 0 12px; border: 1px solid var(--line-strong); border-radius: 9px; background: var(--surface); color: var(--ink); box-shadow: 0 2px 8px rgb(24 53 43 / 12%); cursor: pointer; font-size: 1.125rem; font-weight: 650; }
	.map-control:disabled { opacity: .55; cursor: not-allowed; }
	.map-center-control { font-size: .875rem; }
	.map-status, .map-error { position: absolute; z-index: 1000; left: 12px; bottom: 44px; max-width: min(34rem, calc(100% - 24px)); margin: 0; padding: 8px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--muted-strong); font-size: .875rem; }
	.map-error { border-color: var(--danger); background: var(--danger-soft); color: var(--ink); }
	.map-attribution { position: absolute; z-index: 1000; right: 0; bottom: 0; display: flex; flex-wrap: wrap; gap: 4px; max-width: 100%; justify-content: flex-end; padding: 4px 7px; background: color-mix(in srgb, var(--surface) 94%, transparent); color: var(--muted-strong); font-size: .75rem; line-height: 1.35; }
	.map-attribution a { color: var(--forest); text-decoration: underline; text-underline-offset: 2px; }
	:global(.leaflet-container) { font: inherit; }
	:global(.leaflet-pane) { z-index: 200; }
	:global(.leaflet-overlay-pane) { z-index: 400; }
	:global(.leaflet-marker-pane) { z-index: 600; }
	:global(.leaflet-tooltip-pane) { z-index: 650; }
	:global(.leaflet-popup-pane) { z-index: 700; }
	@media (max-width: 480px) {
		.map-canvas { height: 26rem; }
		.map-controls { top: 8px; right: 8px; max-width: calc(100% - 16px); }
		.map-control { min-width: 44px; }
		.map-center-control { flex: 1 1 auto; }
		.map-attribution { left: 0; justify-content: flex-start; }
	}
</style>
