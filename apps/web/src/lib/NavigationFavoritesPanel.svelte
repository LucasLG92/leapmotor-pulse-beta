<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { restoreFormFocus } from './form-focus';
	import { ApiError, getNavigationFavorites } from './api';
	import Glyph from './Glyph.svelte';
	import { isNavigationPlace, mapLinks, parsePlaceSearchQuery } from './navigation';
	import type { NavigationFavorite, NavigationFavoritePage, NavigationPlace, PageProps } from './types';

	let { busy, online, revision, drafts, mutate, confirmAction, onSessionExpired, navigation, searchAvailable = false, selectedPlace = null }:
		Pick<PageProps, 'busy' | 'online' | 'revision' | 'drafts' | 'mutate' | 'confirmAction' | 'onSessionExpired' | 'navigation'> & {
			searchAvailable?: boolean;
			selectedPlace?: NavigationPlace | null;
		} = $props();
	type FavoriteForm = { name: string; latitude: string; longitude: string };
	const emptyForm = (): FavoriteForm => ({ name: '', latitude: '', longitude: '' });
	const coordinatePattern = /^-?(?:\d+(?:\.\d+)?|\.\d+)$/;
	const coordinateFormat = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 6 });
	let page = $state<NavigationFavoritePage | null>(null);
	let loading = $state(false), readError = $state(''), actionError = $state('');
	let formOpen = $state(false), form = $state<FavoriteForm>(emptyForm()), baseline = '';
	let errors = $state<Record<string, string>>({}), saveError = $state(''), saving = $state(false);
	let lookupQuery = $state(''), lookupResults = $state<NavigationPlace[]>([]), lookupBusy = $state(false), lookupError = $state(''), lookupCompleted = $state(false);
	let manualCoordinatesElement = $state<HTMLDetailsElement | undefined>();
	let selectedLookupPlace = $state<NavigationPlace | null>(null), manualCoordinatesOpen = $state(false);
	let sent = $state(false), sentAttempt = '';
	let mounted = false, requestGeneration = 0, formGeneration = 0, observedKey = '', observedLookupAccess = '';
	let controller: AbortController | null = null, lookupController: AbortController | null = null;
	let lookupGeneration = 0;
	let errorSummary = $state<HTMLDivElement | undefined>();
	let editorElement = $state<HTMLFormElement | undefined>();
	let editorOpener: HTMLElement | null = null;

	function fingerprint(): string { return JSON.stringify(form); }
	function dirty(): boolean { return formOpen && (sent || fingerprint() !== baseline || lookupQuery.length > 0); }
	function coordinate(raw: string, min: number, max: number): number | null {
		const normalized = raw.trim().replace(',', '.');
		if (!coordinatePattern.test(normalized)) return null;
		const value = Number(normalized);
		return Number.isFinite(value) && value >= min && value <= max ? value : null;
	}

	onMount(() => {
		mounted = true;
		observedKey = `${revision}:${online}`;
		void load();
		return drafts.register('navigation-favorite', dirty, () => sent);
	});
	onDestroy(() => { mounted = false; requestGeneration++; formGeneration++; lookupGeneration++; controller?.abort(); lookupController?.abort(); });
	$effect(() => {
		const key = { revision, online };
		untrack(() => {
			if (!mounted) return;
			const next = `${key.revision}:${key.online}`;
			if (next === observedKey) return;
			observedKey = next;
			if (!key.online) {
				requestGeneration++; controller?.abort(); page = null; loading = false;
				readError = 'Sin conexión. Los favoritos se consultan en el servidor y no se guardan sin conexión.';
			} else void load();
		});
	});
	$effect(() => {
		const access = { online, searchAvailable, busy, saving };
		untrack(() => {
			const next = `${access.online}:${access.searchAvailable}:${access.busy}:${access.saving}`;
			if (next === observedLookupAccess) return;
			observedLookupAccess = next;
			if (!access.online || !access.searchAvailable || access.busy || access.saving) cancelLookupSearch();
		});
	});

	function cancelLookupSearch() {
		lookupGeneration++;
		lookupController?.abort(); lookupController = null;
		lookupBusy = false; lookupResults = []; lookupError = ''; lookupCompleted = false;
	}

	function resetLookupSearch() {
		cancelLookupSearch();
		lookupQuery = '';
	}

	function updateLookupQuery(value: string) {
		if (value === lookupQuery) return;
		lookupQuery = value;
		cancelLookupSearch();
		errors = { ...errors };
		delete errors.place;
	}

	function clearPlaceValidationError() {
		if (!errors.place) return;
		errors = { ...errors };
		delete errors.place;
	}

	function editManualCoordinate() {
		selectedLookupPlace = null;
		clearPlaceValidationError();
	}

	function isCurrentLookup(generation: number, editorGeneration: number, query: string, signal: AbortSignal): boolean {
		return mounted && formOpen && generation === lookupGeneration && editorGeneration === formGeneration
			&& !signal.aborted && lookupQuery.trim() === query && online && searchAvailable && !busy && !saving;
	}

	async function searchPlace() {
		if (!mounted || !formOpen || busy || saving || !online || !searchAvailable || lookupBusy) return;
		const query = parsePlaceSearchQuery(lookupQuery);
		if (!query) {
			lookupCompleted = false;
			lookupError = 'Escribe un nombre o una dirección de 3 a 120 caracteres.';
			return;
		}
		const editorGeneration = formGeneration;
		lookupController?.abort();
		const activeController = new AbortController();
		lookupController = activeController;
		const generation = ++lookupGeneration;
		lookupBusy = true; lookupError = ''; lookupResults = []; lookupCompleted = false;
		try {
			const result = await navigation.search(query, activeController.signal);
			if (!isCurrentLookup(generation, editorGeneration, query, activeController.signal)) return;
			if (!result || result.source !== 'OpenStreetMap Photon'
				|| typeof result.fetched_at !== 'string' || result.fetched_at.length > 80 || !Number.isFinite(Date.parse(result.fetched_at))
				|| typeof result.cached !== 'boolean' || !Array.isArray(result.places) || result.places.length > 5) throw new Error('invalid-search');
			const found = result.places.filter(isNavigationPlace).slice(0, 5);
			if (result.places.length && !found.length) throw new Error('invalid-places');
			lookupResults = found;
			lookupCompleted = true;
		} catch (error) {
			if (!isCurrentLookup(generation, editorGeneration, query, activeController.signal)) return;
			if (error instanceof ApiError && error.status === 401) {
				lookupError = 'La sesión ha caducado. Vuelve a iniciar sesión para buscar lugares.';
				onSessionExpired();
			} else lookupError = 'No se pudo buscar este lugar. Vuelve a intentarlo.';
		} finally {
			if (isCurrentLookup(generation, editorGeneration, query, activeController.signal)) {
				lookupBusy = false;
				lookupController = null;
			}
		}
	}

	function searchOnEnter(event: KeyboardEvent) {
		if (event.key !== 'Enter' || event.isComposing) return;
		event.preventDefault();
		void searchPlace();
	}

	async function chooseLookupPlace(place: NavigationPlace) {
		if (!mounted || !formOpen || busy || saving || !online || !searchAvailable || !isNavigationPlace(place) || !lookupResults.includes(place)) return;
		const candidate = { ...place };
		const editorGeneration = formGeneration, generation = lookupGeneration, query = lookupQuery.trim();
		const currentLatitude = coordinate(form.latitude, -90, 90), currentLongitude = coordinate(form.longitude, -180, 180);
		const hasCoordinates = Boolean(form.latitude.trim() || form.longitude.trim());
		const replacesCoordinates = hasCoordinates && (currentLatitude !== candidate.latitude || currentLongitude !== candidate.longitude);
		if (replacesCoordinates) {
			const accepted = await confirmAction('Sustituir coordenadas del borrador', 'Al elegir este resultado se reemplazarán las coordenadas escritas o seleccionadas. El nombre que ya escribiste se conservará.', 'Usar este lugar');
			if (!accepted) return;
		}
		if (!mounted || !formOpen || editorGeneration !== formGeneration || generation !== lookupGeneration
			|| query !== lookupQuery.trim() || !online || !searchAvailable || busy || saving || !lookupResults.includes(place)) return;
		selectedLookupPlace = candidate;
		if (!form.name.trim()) form.name = candidate.name;
		form.latitude = String(candidate.latitude); form.longitude = String(candidate.longitude);
		clearPlaceValidationError();
		errors = { ...errors };
		delete errors.latitude; delete errors.longitude;
		lookupError = '';
	}

	async function load() {
		if (!mounted || !online) return;
		const request = ++requestGeneration, currentRevision = revision;
		controller?.abort(); controller = new AbortController();
		const signal = controller.signal;
		loading = true; readError = ''; actionError = '';
		try {
			const next = await getNavigationFavorites(signal);
			if (!mounted || request !== requestGeneration || signal.aborted || currentRevision !== revision || !online) return;
			page = next;
		} catch (error) {
			if (!mounted || request !== requestGeneration || signal.aborted) return;
			if (error instanceof ApiError && error.status === 401) { onSessionExpired(); return; }
			readError = error instanceof ApiError ? error.message : 'No se pudieron cargar los favoritos.';
		} finally { if (mounted && request === requestGeneration) loading = false; }
	}

	async function openForm(event: MouseEvent) {
		const opener = event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
		if (busy || saving || !online) return;
		const generation = formGeneration, originalForm = fingerprint(), originalQuery = lookupQuery;
		if (dirty() && !await confirmDiscard()) return;
		if (!mounted || busy || saving || generation !== formGeneration || originalForm !== fingerprint() || originalQuery !== lookupQuery) return;
		editorOpener = opener;
		formGeneration++; resetLookupSearch(); form = emptyForm(); errors = {}; saveError = ''; sent = false; sentAttempt = '';
		manualCoordinatesOpen = false;
		if (isNavigationPlace(selectedPlace)) {
			selectedLookupPlace = { ...selectedPlace };
			form = { name: selectedPlace.name, latitude: String(selectedPlace.latitude), longitude: String(selectedPlace.longitude) };
		} else selectedLookupPlace = null;
		formOpen = true; baseline = fingerprint();
		const openedGeneration = formGeneration;
		requestAnimationFrame(() => {
			if (mounted && formOpen && openedGeneration === formGeneration) {
				document.getElementById(searchAvailable ? 'favorite-query' : 'favorite-name')?.focus();
			}
		});
	}

	async function confirmDiscard(): Promise<boolean> {
		if (!dirty()) return true;
		return confirmAction(sent ? 'Alta sin confirmar' : 'Cambios sin guardar', sent
			? 'El favorito pudo haberse creado. Actualiza la lista antes de descartarlo o reenviar los mismos datos.'
			: 'Se descartarán los datos escritos en este formulario.', 'Descartar formulario');
	}
	async function closeForm() {
		if (busy || saving) return;
		const generation = formGeneration, originalForm = fingerprint(), originalQuery = lookupQuery;
		if (!await confirmDiscard() || !mounted || busy || saving || generation !== formGeneration || originalForm !== fingerprint() || originalQuery !== lookupQuery) return;
		const ownedFocus = !!editorElement?.contains(document.activeElement);
		formGeneration++; resetLookupSearch(); formOpen = false; form = emptyForm(); baseline = fingerprint(); errors = {}; saveError = ''; sent = false; sentAttempt = '';
		selectedLookupPlace = null; manualCoordinatesOpen = false;
		const closedGeneration = formGeneration;
		await tick();
		if (mounted && ownedFocus && closedGeneration === formGeneration) restoreFormFocus(editorOpener, document.getElementById('favorites-heading'), editorElement);
	}

	function validate(): { name: string; latitude: number; longitude: number } | null {
		const next: Record<string, string> = {};
		const name = form.name.trim();
		if (!name) next.name = 'Escribe un nombre para este lugar.';
		else if (name.length > 160) next.name = 'El nombre admite hasta 160 caracteres.';
		const latitude = coordinate(form.latitude, -90, 90), longitude = coordinate(form.longitude, -180, 180);
		if (manualCoordinatesElement?.open || manualCoordinatesOpen || form.latitude.trim() || form.longitude.trim()) {
			if (latitude === null) next.latitude = 'Indica una latitud entre −90 y 90.';
			if (longitude === null) next.longitude = 'Indica una longitud entre −180 y 180.';
			if (next.latitude || next.longitude) manualCoordinatesOpen = true;
		} else next.place = 'Busca y elige un lugar para tu favorito.';
		errors = next;
		return Object.keys(next).length ? null : { name, latitude: latitude!, longitude: longitude! };
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!mounted || busy || saving || !online) return;
		const generation = formGeneration, originalForm = fingerprint(), originalQuery = lookupQuery;
		const values = validate();
		if (!values) { requestAnimationFrame(() => { if (mounted) errorSummary?.focus(); }); return; }
		const attempt = JSON.stringify(values);
		if (sent && sentAttempt !== attempt) {
			const checked = await confirmAction('Revisar alta anterior', 'La primera alta no tiene resultado confirmado. Actualiza la lista para comprobar si ya aparece; si continúas con otros datos, el primer favorito podría seguir existiendo.', 'Continuar con otra alta');
			if (!checked || !mounted) return;
		} else if (sent && sentAttempt === attempt) {
			const checked = await confirmAction('Reenviar alta sin confirmar', 'El favorito puede haberse creado aunque no llegara la confirmación. Actualiza la lista primero y vuelve a enviar solo si has decidido asumir el posible duplicado.', 'Reenviar mismo favorito');
			if (!checked || !mounted) return;
		}
		if (!mounted || generation !== formGeneration || originalForm !== fingerprint() || originalQuery !== lookupQuery) return;
		const ownedFocus = !!editorElement?.contains(document.activeElement);
		let closedGeneration: number | null = null;
		saving = true; saveError = '';
		try {
			sent = true; sentAttempt = attempt;
			const created = await mutate('/api/navigation/favorites', 'POST', values, 'El lugar se ha añadido a tus favoritos.');
			if (!mounted || generation !== formGeneration) return;
			if (created) { formGeneration++; closedGeneration = formGeneration; resetLookupSearch(); formOpen = false; form = emptyForm(); baseline = fingerprint(); sent = false; sentAttempt = ''; errors = {}; selectedLookupPlace = null; manualCoordinatesOpen = false; void load(); }
			else saveError = 'No se confirmó la creación. Conservamos los datos: actualiza la lista antes de decidir si reenvías esta alta.';
		} catch {
			if (mounted && generation === formGeneration) saveError = 'No se confirmó la creación. Conservamos los datos; revisa la lista antes de decidir si reenvías esta alta.';
		} finally {
			if (mounted) {
				saving = false; await tick();
				if (ownedFocus && closedGeneration === formGeneration) restoreFormFocus(document.getElementById('favorite-add'), document.getElementById('favorites-heading'), editorElement);
			}
		}
	}

	async function remove(favorite: NavigationFavorite) {
		if (busy || saving || !online) return;
		const generation = formGeneration;
		const accepted = await confirmAction('Quitar lugar favorito', `Se quitará «${favorite.name}» de tu lista guardada.`, 'Quitar favorito');
		if (!accepted || !mounted || busy || saving || !online || generation !== formGeneration) return;
		actionError = ''; saving = true;
		try {
			const removed = await mutate(`/api/navigation/favorites/${favorite.id}`, 'DELETE', {}, 'El lugar se ha quitado de tus favoritos.');
			if (!mounted) return;
			if (removed) void load();
			else actionError = 'No se confirmó la eliminación. Actualiza la lista para comprobar el resultado antes de volver a intentarlo.';
		} catch { if (mounted) actionError = 'No se confirmó la eliminación. Actualiza la lista para comprobar el resultado antes de volver a intentarlo.'; }
		finally { if (mounted) saving = false; }
	}

	function linksFor(favorite: NavigationFavorite) { return mapLinks(favorite); }
	let total = $derived(page?.items.length ?? 0);
</script>

<section class="favorites-panel" aria-labelledby="favorites-heading">
	<header class="favorites-header"><div><h2 id="favorites-heading" tabindex="-1">Lugares guardados</h2></div><button id="favorite-add" class="button button-primary" type="button" disabled={busy || saving || !online} onclick={openForm}><Glyph name="plus" size={16} /> Añadir lugar</button></header>
	{#if actionError}<div class="favorite-error" role="alert"><span>{actionError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></div>{/if}
	{#if readError}<div class="favorite-error" role="alert"><span>{readError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Reintentar</button></div>{/if}
	{#if loading}<p class="state-message" role="status">Consultando favoritos…</p>{/if}
	{#if formOpen}
		<form class="favorite-form" bind:this={editorElement} onsubmit={save} novalidate aria-labelledby="favorite-form-heading">
			<div class="form-heading"><div><p class="eyebrow">Buscar o introducir</p><h3 id="favorite-form-heading">Añadir lugar</h3></div><button class="icon-button" type="button" aria-label="Cerrar formulario" onclick={closeForm} disabled={busy || saving}>×</button></div>
			<p class="form-intro">Encuentra un lugar por su nombre o dirección y elige el resultado. Puedes cambiar el nombre antes de guardarlo.</p>
			{#if Object.keys(errors).length}<div class="form-errors" role="alert" tabindex="-1" bind:this={errorSummary}><strong>Revisa estos campos</strong><ul>{#each Object.entries(errors) as [key, message]}<li><a href={key === 'place' ? '#favorite-query' : `#favorite-${key}`}>{message}</a></li>{/each}</ul></div>{/if}
			{#if saveError}<div class="favorite-error" role="alert" aria-atomic="true"><span>{saveError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></div>{/if}
			<div class="favorite-fields">
				<div class="field full lookup-field">
					<label for="favorite-query">Nombre o dirección</label>
					<div class="lookup-controls">
						<input id="favorite-query" type="search" maxlength="120" autocomplete="off" spellcheck="false" placeholder="Por ejemplo, museo del Prado" value={lookupQuery} oninput={(event) => updateLookupQuery(event.currentTarget.value)} onkeydown={searchOnEnter} disabled={busy || saving || !online || !searchAvailable} aria-invalid={!!errors.place || !!lookupError} aria-describedby={`favorite-query-help${errors.place ? ' favorite-place-error' : ''}${lookupError ? ' favorite-query-error' : ''}`} />
						<button class="button button-secondary lookup-button" type="button" onclick={() => void searchPlace()} disabled={busy || saving || !online || !searchAvailable || lookupBusy || lookupQuery.trim().length < 3 || lookupQuery.trim().length > 120}>{lookupBusy ? 'Buscando…' : 'Buscar lugar'}</button>
					</div>
					<p id="favorite-query-help" class="field-hint">{searchAvailable ? 'La búsqueda se realiza en tu servidor, con datos de OpenStreetMap de España. El lugar solo se guarda al pulsar Guardar favorito.' : 'La búsqueda privada aún no está disponible. Puedes introducir las coordenadas si ya las conoces.'}</p>
					{#if errors.place}<small id="favorite-place-error">{errors.place}</small>{/if}
					{#if lookupError}<small id="favorite-query-error" role="alert">{lookupError}</small>{/if}
					{#if lookupBusy}<p class="lookup-status" role="status">Buscando en tu servidor…</p>
					{:else if lookupResults.length}
						<p class="lookup-status" role="status">{lookupResults.length} {lookupResults.length === 1 ? 'lugar encontrado' : 'lugares encontrados'}. Elige tu destino.</p>
						<ul class="lookup-results" aria-label="Lugares encontrados">
							{#each lookupResults as place}
								<li><button class="lookup-result" type="button" aria-pressed={selectedLookupPlace?.id === place.id} onclick={() => void chooseLookupPlace(place)} disabled={busy || saving || !online || !searchAvailable}><span>{place.name}</span><small>{place.latitude.toFixed(6)}, {place.longitude.toFixed(6)}</small></button></li>
							{/each}
						</ul>
					{:else if lookupCompleted}<p class="lookup-status" role="status">No se encontraron lugares. Añade la localidad o escribe una dirección más concreta.</p>{/if}
					{#if selectedLookupPlace}<p class="lookup-selection" role="status">Las coordenadas de «{selectedLookupPlace.name}» están en el borrador. Aún no se ha guardado el favorito.</p>{/if}
					{#if !searchAvailable}<p class="lookup-status">La búsqueda de lugares no está disponible. Puedes usar las coordenadas manuales.</p>{/if}
				</div>
				<div class="field full"><label for="favorite-name">Nombre del lugar</label><input id="favorite-name" maxlength="160" autocomplete="off" placeholder="Puedes editar el nombre sugerido" bind:value={form.name} disabled={busy || saving} aria-invalid={!!errors.name} aria-describedby={errors.name ? 'favorite-name-error' : undefined} />{#if errors.name}<small id="favorite-name-error">{errors.name}</small>{/if}</div>
				<details class="manual-coordinate-details" bind:this={manualCoordinatesElement} bind:open={manualCoordinatesOpen} ontoggle={clearPlaceValidationError}>
					<summary>Introducir coordenadas</summary>
					<div class="favorite-fields manual-fields">
						<div class="field"><label for="favorite-latitude">Latitud</label><input id="favorite-latitude" type="text" inputmode="decimal" placeholder="Coordenada manual" bind:value={form.latitude} oninput={editManualCoordinate} disabled={busy || saving} aria-invalid={!!errors.latitude} aria-describedby={errors.latitude ? 'favorite-latitude-error' : undefined} />{#if errors.latitude}<small id="favorite-latitude-error">{errors.latitude}</small>{/if}</div>
						<div class="field"><label for="favorite-longitude">Longitud</label><input id="favorite-longitude" type="text" inputmode="decimal" placeholder="Coordenada manual" bind:value={form.longitude} oninput={editManualCoordinate} disabled={busy || saving} aria-invalid={!!errors.longitude} aria-describedby={errors.longitude ? 'favorite-longitude-error' : undefined} />{#if errors.longitude}<small id="favorite-longitude-error">{errors.longitude}</small>{/if}</div>
					</div>
				</details>
			</div>
			<div class="form-actions"><button class="button button-secondary" type="button" onclick={closeForm} disabled={busy || saving}>Cancelar</button><button class="button button-primary" type="submit" disabled={busy || saving || !online}>{saving ? 'Guardando…' : sent ? 'Revisar y reintentar' : 'Guardar favorito'}</button></div>
		</form>
	{/if}
	{#if page && !loading}
		{#if page.items.length}
			<div class="favorites-intro"><p>{total} {total === 1 ? 'lugar guardado' : 'lugares guardados'}</p><span>Las coordenadas se muestran para que puedas revisarlas antes de salir a mapas.</span></div>
			<ul class="favorite-list evolution-list" aria-label="Lugares favoritos">{#each page.items as favorite (favorite.id)}
				{@const links = linksFor(favorite)}
				<li><article class="favorite-card evolution-item"><div class="favorite-main"><div class="favorite-title"><h3>{favorite.name}</h3><span>Lugar guardado</span></div><p class="coordinates"><span>Lat {coordinateFormat.format(favorite.latitude)}</span><span>Lon {coordinateFormat.format(favorite.longitude)}</span></p><p class="favorite-note">Abre las coordenadas en el servicio elegido. Pulse no comparte tu ubicación actual.</p></div><div class="favorite-actions">{#if links}<a class="button button-secondary" href={links.google} target="_blank" rel="noopener noreferrer">Google Maps</a><a class="button button-secondary" href={links.apple} target="_blank" rel="noopener noreferrer">Apple Maps</a>{:else}<span class="link-unavailable">Coordenadas no aptas para enlaces externos</span>{/if}<button class="button button-quiet danger-text" type="button" disabled={busy || saving || !online} onclick={() => void remove(favorite)}>Quitar favorito</button></div></article></li>
				{/each}</ul>
		{:else}<div class="empty-state"><span class="favorite-empty-mark" aria-hidden="true"><Glyph name="navigation" size={24} /></span><h3>Aún no hay lugares guardados</h3><p>Encuentra un destino y guárdalo para tenerlo a mano.</p><button class="button button-primary" type="button" disabled={busy || saving || !online} onclick={openForm}>Añadir el primero</button></div>{/if}
	{:else if !loading && !readError}<p class="state-message">Conéctate para consultar la lista guardada en tu cuenta.</p>{/if}
</section>

<style>
	.favorites-panel { display: grid; gap: 16px; min-width: 0; }
	.favorites-header, .form-heading, .form-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	.favorites-header { align-items: flex-start; padding-bottom: 17px; border-bottom: 1px solid var(--line); }
	.favorites-header h2 { margin: 0; font-size: 1.2rem; letter-spacing: -.03em; }
	.favorite-empty-mark :global(svg) { display: block; }
	.eyebrow { margin: 0; color: var(--forest); font-size: .75rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
	.button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 48px; padding: 9px 14px; border: 1px solid transparent; border-radius: 14px; font: inherit; font-weight: 680; cursor: pointer; transition: background-color 150ms ease, border-color 150ms ease, transform 150ms ease; }
	.button-primary { background: var(--forest); color: var(--on-primary); }
	.button-primary:hover:not(:disabled) { background: var(--forest-hover); transform: translateY(-1px); }
	.button-secondary { border-color: var(--line-strong); background: var(--surface); color: var(--ink); }
	.button-secondary:hover:not(:disabled), .button-quiet:hover:not(:disabled) { background: var(--surface-soft); }
	.button-quiet { border-color: var(--line); background: transparent; color: var(--muted-strong); }
	.button:disabled { opacity: .58; cursor: not-allowed; }
	.danger-text { color: var(--danger); }
	.favorite-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--danger) 32%, var(--line)); border-radius: 12px; background: var(--danger-soft); color: var(--danger); line-height: 1.45; overflow-wrap: anywhere; }
	.state-message { margin: 0; padding: 13px; border-radius: 12px; background: var(--surface-soft); color: var(--muted); }
	.favorite-form { display: grid; gap: 13px; min-width: 0; padding: clamp(16px, 2.4vw, 22px); border: 1px solid var(--line); border-radius: 26px; background: var(--surface); box-shadow: var(--shadow); }
	.form-heading h3 { margin: 2px 0 0; font-size: 1.15rem; }
	.icon-button { min-width: 48px; min-height: 48px; border: 1px solid var(--line); border-radius: 50%; background: var(--surface-soft); font-size: 1.5rem; cursor: pointer; }
	.form-intro { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.favorite-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
	.field { display: grid; align-content: start; gap: 6px; min-width: 0; }
	.field.full { grid-column: 1 / -1; }
	.field label { color: var(--muted-strong); font-size: .875rem; font-weight: 660; }
	.lookup-controls { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 8px; }
	.lookup-controls input { min-width: 0; }
	.lookup-button { min-height: 48px; white-space: nowrap; }
	.field-hint { margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.45; }
	.field input { width: 100%; min-width: 0; min-height: 48px; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 12px; background: var(--surface); color: var(--ink); font: inherit; }
	.field input[aria-invalid="true"] { border-color: var(--danger); }
	.field small { color: var(--danger); font-size: .8125rem; }
	.lookup-status, .lookup-selection { margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.45; }
	.lookup-selection { padding: 9px 11px; border-radius: 10px; background: var(--forest-soft); color: var(--forest); }
	.lookup-results { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
	.lookup-results li { min-width: 0; }
	.lookup-result { display: flex; align-items: center; justify-content: space-between; gap: 8px 14px; width: 100%; min-width: 0; min-height: 50px; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 12px; background: var(--surface); color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
	.lookup-result[aria-pressed="true"] { border-color: var(--forest); background: var(--forest-soft); }
	.lookup-result:disabled { opacity: .58; cursor: not-allowed; }
	.lookup-result > span { min-width: 0; overflow-wrap: anywhere; }
	.lookup-result small { flex: 0 0 auto; color: var(--muted); font-size: .75rem; font-variant-numeric: tabular-nums; }
	.manual-coordinate-details { grid-column: 1 / -1; min-width: 0; }
	.manual-coordinate-details summary { min-height: 44px; display: flex; align-items: center; color: var(--muted-strong); font-size: .875rem; font-weight: 660; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
	.manual-fields { margin-top: 7px; }
	.form-errors { padding: 11px 13px; border: 1px solid color-mix(in srgb, var(--danger) 32%, var(--line)); border-radius: 11px; background: var(--danger-soft); color: var(--danger); }
	.form-errors ul { margin: 4px 0 0; padding-left: 20px; }
	.form-errors a { text-decoration: underline; text-underline-offset: 2px; }
	.form-actions { justify-content: flex-end; flex-wrap: wrap; }
	.favorites-intro { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 4px 14px; color: var(--muted); font-size: .8125rem; }
	.favorites-intro p { margin: 0; color: var(--ink); font-weight: 680; }
	.favorite-list.evolution-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
	.favorite-card.evolution-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 12px 18px; min-width: 0; padding: 17px 0; border-bottom: 1px solid var(--line); background: transparent; }
	.favorite-main { display: grid; gap: 6px; min-width: 0; }
	.favorite-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 5px 9px; }
	.favorite-title h3 { margin: 0; font-size: 1.05rem; }
	.favorite-title span { padding: 3px 7px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: .6875rem; }
	.coordinates { display: flex; flex-wrap: wrap; gap: 5px 13px; margin: 0; color: var(--muted-strong); font-size: .8125rem; font-variant-numeric: tabular-nums; }
	.favorite-note { margin: 0; color: var(--muted); font-size: .8125rem; line-height: 1.4; }
	.favorite-actions { display: grid; justify-items: stretch; gap: 6px; }
	.favorite-actions .button { min-height: 48px; font-size: .8125rem; }
	.favorite-actions .button-quiet { min-height: 48px; }
	.link-unavailable { max-width: 180px; color: var(--muted); font-size: .75rem; }
	.empty-state { display: grid; justify-items: start; gap: 6px; padding: clamp(20px, 4vw, 34px); border: 1px dashed var(--line-strong); border-radius: 24px; background: var(--surface); }
	.empty-state > span { color: var(--forest); font-size: 1.6rem; }
	.favorite-empty-mark { display: grid; width: 40px; height: 40px; place-items: center; border-radius: 12px; background: var(--forest-soft); }
	.empty-state h3, .empty-state p { margin: 0; }
	.empty-state p { max-width: 58ch; color: var(--muted); }
	@media (max-width: 660px) { .favorites-header { align-items: stretch; flex-direction: column; }.favorites-header > button { width: 100%; }.favorite-card.evolution-item { grid-template-columns: 42px minmax(0, 1fr); }.favorite-actions { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); }.favorite-actions .button-quiet, .link-unavailable { grid-column: 1 / -1; }.favorite-fields { grid-template-columns: 1fr; }.field.full { grid-column: auto; } }
	@media (prefers-reduced-motion: reduce) { .button { transition: none; }.button-primary:hover:not(:disabled) { transform: none; } }
	.favorite-main { gap: 5px; }
	.favorite-title h3 { font-size: .9375rem; }
	.favorite-actions { grid-template-columns: minmax(0, 1fr); align-items: start; }
	.favorite-actions .button { min-height: 44px; }
	.favorite-actions .button-quiet { padding-inline: 8px; }
	@media (max-width: 660px) {
		.favorite-card.evolution-item { grid-template-columns: minmax(0, 1fr); }
		.favorite-actions { grid-column: auto; grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.favorite-actions .button-quiet, .link-unavailable { grid-column: 1 / -1; }
		.lookup-controls { grid-template-columns: minmax(0, 1fr); }
		.lookup-button { width: 100%; }
		.lookup-result { align-items: flex-start; flex-direction: column; }
	}
</style>
