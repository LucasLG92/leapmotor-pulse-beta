<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { ApiError, getNavigationFavorites } from './api';
	import Glyph from './Glyph.svelte';
	import { mapLinks } from './navigation';
	import type { NavigationFavorite, NavigationFavoritePage, PageProps } from './types';

	let { busy, online, revision, drafts, mutate, confirmAction, onSessionExpired }:
		Pick<PageProps, 'busy' | 'online' | 'revision' | 'drafts' | 'mutate' | 'confirmAction' | 'onSessionExpired'> = $props();
	type FavoriteForm = { name: string; latitude: string; longitude: string };
	const emptyForm = (): FavoriteForm => ({ name: '', latitude: '', longitude: '' });
	const coordinatePattern = /^-?(?:\d+(?:\.\d+)?|\.\d+)$/;
	const coordinateFormat = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 6 });
	let page = $state<NavigationFavoritePage | null>(null);
	let loading = $state(false), readError = $state(''), actionError = $state('');
	let formOpen = $state(false), form = $state<FavoriteForm>(emptyForm()), baseline = '';
	let errors = $state<Record<string, string>>({}), saveError = $state(''), saving = $state(false);
	let sent = $state(false), sentAttempt = '';
	let mounted = false, requestGeneration = 0, formGeneration = 0, observedKey = '';
	let controller: AbortController | null = null;
	let errorSummary = $state<HTMLDivElement | undefined>();

	function fingerprint(): string { return JSON.stringify(form); }
	function dirty(): boolean { return formOpen && (sent || fingerprint() !== baseline); }
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
	onDestroy(() => { mounted = false; requestGeneration++; formGeneration++; controller?.abort(); });
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

	async function openForm() {
		if (busy || saving || !online) return;
		const generation = formGeneration;
		if (dirty() && !await confirmDiscard()) return;
		if (!mounted || busy || saving || generation !== formGeneration) return;
		formGeneration++; form = emptyForm(); errors = {}; saveError = ''; sent = false; sentAttempt = '';
		formOpen = true; baseline = fingerprint();
		requestAnimationFrame(() => { if (mounted) document.getElementById('favorite-name')?.focus(); });
	}

	async function confirmDiscard(): Promise<boolean> {
		if (!dirty()) return true;
		return confirmAction(sent ? 'Alta sin confirmar' : 'Cambios sin guardar', sent
			? 'El favorito pudo haberse creado. Actualiza la lista antes de descartarlo o reenviar los mismos datos.'
			: 'Se descartarán los datos escritos en este formulario.', 'Descartar formulario');
	}
	async function closeForm() {
		if (busy || saving) return;
		const generation = formGeneration;
		if (!await confirmDiscard() || !mounted || busy || saving || generation !== formGeneration) return;
		formGeneration++; formOpen = false; form = emptyForm(); baseline = fingerprint(); errors = {}; saveError = ''; sent = false; sentAttempt = '';
	}

	function validate(): { name: string; latitude: number; longitude: number } | null {
		const next: Record<string, string> = {};
		const name = form.name.trim();
		if (!name) next.name = 'Escribe un nombre para este lugar.';
		else if (name.length > 160) next.name = 'El nombre admite hasta 160 caracteres.';
		const latitude = coordinate(form.latitude, -90, 90), longitude = coordinate(form.longitude, -180, 180);
		if (latitude === null) next.latitude = 'Indica una latitud entre −90 y 90.';
		if (longitude === null) next.longitude = 'Indica una longitud entre −180 y 180.';
		errors = next;
		return Object.keys(next).length ? null : { name, latitude: latitude!, longitude: longitude! };
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!mounted || busy || saving || !online) return;
		const generation = formGeneration, originalForm = fingerprint();
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
		if (!mounted || generation !== formGeneration || originalForm !== fingerprint()) return;
		saving = true; saveError = '';
		try {
			sent = true; sentAttempt = attempt;
			const created = await mutate('/api/navigation/favorites', 'POST', values, 'El lugar se ha añadido a tus favoritos.');
			if (!mounted || generation !== formGeneration) return;
			if (created) { formGeneration++; formOpen = false; form = emptyForm(); baseline = fingerprint(); sent = false; sentAttempt = ''; errors = {}; void load(); }
			else saveError = 'No se confirmó la creación. Conservamos los datos: actualiza la lista antes de decidir si reenvías esta alta.';
		} catch {
			if (mounted && generation === formGeneration) saveError = 'No se confirmó la creación. Conservamos los datos; revisa la lista antes de decidir si reenvías esta alta.';
		} finally { if (mounted) saving = false; }
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
	<header class="favorites-header"><div><h2 id="favorites-heading">Lugares guardados</h2></div><button class="button button-primary" type="button" disabled={busy || saving || !online} onclick={openForm}><Glyph name="plus" size={16} /> Añadir lugar</button></header>
	{#if actionError}<div class="favorite-error" role="alert"><span>{actionError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></div>{/if}
	{#if readError}<div class="favorite-error" role="alert"><span>{readError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Reintentar</button></div>{/if}
	{#if loading}<p class="state-message" role="status">Consultando favoritos…</p>{/if}
	{#if formOpen}
		<form class="favorite-form" onsubmit={save} novalidate aria-labelledby="favorite-form-heading">
			<div class="form-heading"><div><p class="eyebrow">Entrada manual</p><h3 id="favorite-form-heading">Añadir lugar</h3></div><button class="icon-button" type="button" aria-label="Cerrar formulario" onclick={closeForm} disabled={busy || saving}>×</button></div>
			<p class="form-intro">Introduce nombre y coordenadas en esta pantalla. No se activa la ubicación del dispositivo ni se envía una búsqueda automática.</p>
			{#if Object.keys(errors).length}<div class="form-errors" role="alert" tabindex="-1" bind:this={errorSummary}><strong>Revisa estos campos</strong><ul>{#each Object.entries(errors) as [key, message]}<li><a href={`#favorite-${key}`}>{message}</a></li>{/each}</ul></div>{/if}
			{#if saveError}<div class="favorite-error" role="alert" aria-atomic="true"><span>{saveError}</span><button class="button button-secondary" type="button" onclick={() => void load()} disabled={!online || loading}>Actualizar lista</button></div>{/if}
			<div class="favorite-fields">
				<div class="field full"><label for="favorite-name">Nombre del lugar</label><input id="favorite-name" maxlength="160" autocomplete="off" placeholder="Por ejemplo, aparcamiento habitual" bind:value={form.name} disabled={busy || saving} aria-invalid={!!errors.name} />{#if errors.name}<small>{errors.name}</small>{/if}</div>
				<div class="field"><label for="favorite-latitude">Latitud</label><input id="favorite-latitude" type="text" inputmode="decimal" placeholder="Coordenada manual" bind:value={form.latitude} disabled={busy || saving} aria-invalid={!!errors.latitude} />{#if errors.latitude}<small>{errors.latitude}</small>{/if}</div>
				<div class="field"><label for="favorite-longitude">Longitud</label><input id="favorite-longitude" type="text" inputmode="decimal" placeholder="Coordenada manual" bind:value={form.longitude} disabled={busy || saving} aria-invalid={!!errors.longitude} />{#if errors.longitude}<small>{errors.longitude}</small>{/if}</div>
			</div>
			<div class="form-actions"><button class="button button-secondary" type="button" onclick={closeForm} disabled={busy || saving}>Cancelar</button><button class="button button-primary" type="submit" disabled={busy || saving || !online}>{saving ? 'Guardando…' : sent ? 'Revisar y reintentar' : 'Guardar favorito'}</button></div>
		</form>
	{/if}
	{#if page && !loading}
		{#if page.items.length}
			<div class="favorites-intro"><p>{total} {total === 1 ? 'lugar guardado' : 'lugares guardados'}</p><span>Las coordenadas se muestran para que puedas revisarlas antes de salir a mapas.</span></div>
			<ul class="favorite-list evolution-list" aria-label="Lugares favoritos">{#each page.items as favorite (favorite.id)}
				{@const links = linksFor(favorite)}
				<li><article class="favorite-card evolution-item"><div class="favorite-main"><div class="favorite-title"><h3>{favorite.name}</h3><span>Entrada manual</span></div><p class="coordinates"><span>Lat {coordinateFormat.format(favorite.latitude)}</span><span>Lon {coordinateFormat.format(favorite.longitude)}</span></p><p class="favorite-note">Abre las coordenadas en el servicio elegido. Pulse no comparte tu ubicación actual.</p></div><div class="favorite-actions">{#if links}<a class="button button-secondary" href={links.google} target="_blank" rel="noopener noreferrer">Google Maps</a><a class="button button-secondary" href={links.apple} target="_blank" rel="noopener noreferrer">Apple Maps</a>{:else}<span class="link-unavailable">Coordenadas no aptas para enlaces externos</span>{/if}<button class="button button-quiet danger-text" type="button" disabled={busy || saving || !online} onclick={() => void remove(favorite)}>Quitar favorito</button></div></article></li>
				{/each}</ul>
		{:else}<div class="empty-state"><span class="favorite-empty-mark" aria-hidden="true"><Glyph name="navigation" size={24} /></span><h3>Aún no hay lugares guardados</h3><p>Añade las coordenadas manualmente para tener accesos disponibles cuando tú los abras.</p><button class="button button-primary" type="button" disabled={busy || saving || !online} onclick={openForm}>Añadir el primero</button></div>{/if}
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
	.field input { width: 100%; min-width: 0; min-height: 48px; padding: 9px 11px; border: 1px solid var(--line-strong); border-radius: 12px; background: var(--surface); color: var(--ink); font: inherit; }
	.field input[aria-invalid="true"] { border-color: var(--danger); }
	.field small { color: var(--danger); font-size: .8125rem; }
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
	}
</style>
