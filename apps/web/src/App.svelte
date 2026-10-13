<script lang="ts">
	import { onMount, tick, type Component } from 'svelte';
	import { ApiError, getSession, getState, mutateApi, queryNavigation, requestApi } from './lib/api';
	import { getNativeBridge, isNativeApp, type NativeBridgeStatus } from './lib/native';
	import { DraftGuard } from './lib/draft-guard';
	import { observeServiceWorkerUpdate } from './lib/pwa-update';
	import { createStartupAttempt } from './lib/startup-read';
	import { createStateRefresh } from './lib/state-refresh';
	import { internalAppPath, preparationSection } from './lib/app-links';
import type { BackupActionResult, ChargeCreationRequest, ChargeQuote, ChargeQuotePayload, HttpMethod, ManualJourneyCreationRequest, MutationPayload, PageProps, PulseState, Route, SessionState, ThemePreference, WebPushSubscriptionInput, WidgetPairingResult, PushDevice, PushTestResult } from './lib/types';
	import Glyph from './lib/Glyph.svelte';

	type ViewComponent = Component<PageProps>;
	type BootStatus = 'loading' | 'ready' | 'unauthenticated' | 'error';
	type BeforeInstallPromptEvent = Event & {
		prompt: () => Promise<void>;
		userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
	};

	const navigation: Array<{ route: Route; path: string; label: string; icon: string }> = [
		{ route: 'overview', path: '/', label: 'Coche', icon: 'car' },
		{ route: 'energy', path: '/energy', label: 'Energía', icon: 'energy' },
		{ route: 'activity', path: '/activity', label: 'Diario', icon: 'activity' },
		{ route: 'navigation', path: '/navigation', label: 'Mapa', icon: 'navigation' },
		{ route: 'protection', path: '/protection', label: 'Estados', icon: 'protection' },
		{ route: 'settings', path: '/settings', label: 'Perfil', icon: 'settings' }
	];
	const titles: Record<Route, string> = {
		overview: 'Coche', energy: 'Energía', navigation: 'Mapa', activity: 'Diario', protection: 'Estados', settings: 'Perfil'
	};
	const loaders: Record<Route, () => Promise<{ default: ViewComponent }>> = {
		overview: () => import('./routes/Overview.svelte') as Promise<{ default: ViewComponent }>,
		energy: () => import('./routes/Energy.svelte') as Promise<{ default: ViewComponent }>,
		navigation: () => import('./routes/Navigation.svelte') as Promise<{ default: ViewComponent }>,
		activity: () => import('./routes/Activity.svelte') as Promise<{ default: ViewComponent }>,
		protection: () => import('./routes/Protection.svelte') as Promise<{ default: ViewComponent }>,
		settings: () => import('./routes/Settings.svelte') as Promise<{ default: ViewComponent }>
	};

	function routeFromPath(path: string): Route {
		if (path === '/energy') return 'energy';
		if (path === '/navigation') return 'navigation';
		if (path === '/activity') return 'activity';
		if (path === '/protection') return 'protection';
		if (path === '/settings') return 'settings';
		return 'overview';
	}

	let route = $state<Route>(routeFromPath(window.location.pathname));
	let View = $state<ViewComponent | null>(null);
	let viewLoadFailed = $state(false);
	let viewLoadAttempt = $state(0);
	let viewRetryButton = $state<HTMLButtonElement | undefined>();
	let viewRetryFocusRequested = false;
	let pageTitle = $state<HTMLHeadingElement | undefined>();
	let routeFocusPending = $state(window.location.pathname === '/settings' && window.location.hash === '#preparation');
	const drafts = new DraftGuard();
	let formContextRevision = $state(0);
	// Per-load routing metadata, not an identity or an authentication token.
	const historyScope = `pulse-${Date.now()}-${Math.random()}`;
	let historyIndex = 0;
	let currentLocation = $state(window.location.pathname + window.location.search + window.location.hash);
	const settingsSection = $derived(preparationSection(currentLocation));
	let restoringHistoryIndex: number | null = null;
	let navigationPending = false;
	let navigationGeneration = 0;
	let approvedReload = false;
	let compactNavigationOpen = $state(false);
	let bootStatus = $state<BootStatus>('loading');
	let pulseState = $state<PulseState | null>(null);
	let session = $state<SessionState | null>(null);
	let csrfToken = $state('');
	let busyCount = $state(0);
	let online = $state(navigator.onLine);
	let dataRevision = $state(0);
	let toastMessage = $state('');
	let toastTone = $state<'info' | 'success' | 'error'>('info');
	let theme = $state<ThemePreference>('system');
	let confirmDialog: HTMLDialogElement;
	let confirmTitle = $state('');
	let confirmMessage = $state('');
	let confirmLabel = $state('Confirmar');
	let confirmResolver: ((answer: boolean) => void) | null = null;
	let serviceWorkerRegistration: ServiceWorkerRegistration | null = null;
	let eventSource: EventSource | null = null;
	const nativeBridge = getNativeBridge();
	const nativeApp = isNativeApp();
	let nativeStatus = $state<NativeBridgeStatus | null>(null);
	let nativeUnsubscribe: (() => void) | null = null;
	let nativeSubscriptionRequest = 0;
	let loginBusy = $state(false);
	let eventSourceConnected = false;
	let latestEventRevision: number | null = null;
	let eventRefreshPending = false;
	let eventRefreshTimer: ReturnType<typeof setTimeout> | undefined;
	let eventFallbackTimer: ReturnType<typeof setInterval> | undefined;
	let initialEventTimer: ReturnType<typeof setTimeout> | undefined;
	let resolveInitialEvent: ((received: boolean) => void) | undefined;
	let initialStateFetchStarted = false;
	let initialStateReady = false;
	let nextSessionCheckAt = 0;
	let updateAvailable = $state(false);
	let installPrompt: BeforeInstallPromptEvent | null = null;
	let canInstall = $state(false);
	let updateBusy = $state(false);
	let updateTimeout: ReturnType<typeof setTimeout> | undefined;
	let toastTimeout: ReturnType<typeof setTimeout> | undefined;
	let bootMessage = $state('Estamos conectando con Pulse…');
	let startupAttempt: ReturnType<typeof createStartupAttempt> | null = null;
	let bootRetryButton = $state<HTMLButtonElement | undefined>();

	const isBusy = $derived(busyCount > 0);
	const appName = $derived(pulseState?.app?.name || 'Leapmotor Pulse');
	const isDemo = $derived(pulseState?.mode === 'demo');

	$effect(() => {
		const selectedRoute = route;
		viewLoadAttempt;
		const restoreRetryFocus = viewRetryFocusRequested;
		viewRetryFocusRequested = false;
		let cancelled = false;
		viewLoadFailed = false;
		View = null;
		loaders[selectedRoute]().then((module) => {
			if (cancelled || route !== selectedRoute) return;
			View = module.default;
		}).catch(() => {
			if (cancelled || route !== selectedRoute) return;
			viewLoadFailed = true;
			showToast('No se pudo abrir esta sección. Puedes volver a intentarlo.', 'error');
			if (restoreRetryFocus) void tick().then(() => {
				if (!cancelled && route === selectedRoute && document.hasFocus() && document.activeElement === document.body) {
					viewRetryButton?.focus({ preventScroll: true });
				}
			});
		});
		return () => { cancelled = true; };
	});

	$effect(() => {
		const pending = routeFocusPending;
		const selectedRoute = route;
		const selectedView = View;
		const selectedLocation = currentLocation;
		const title = pageTitle;
		const section = settingsSection;
		if (!pending || !selectedView || !title) return;
		void tick().then(() => {
			if (!routeFocusPending || route !== selectedRoute || View !== selectedView || currentLocation !== selectedLocation) return;
			const target = section === 'preparation' ? document.getElementById('pulse-preparation') : title;
			if (!target) return;
			routeFocusPending = false;
			target.focus({ preventScroll: true });
			if (section === 'preparation') target.scrollIntoView({ block: 'start', behavior: 'instant' });
		});
	});

	function applyTheme(preference: ThemePreference) {
		document.documentElement.dataset.theme = preference;
		updateThemeColor(preference);
		try {
			localStorage.setItem('pulse-theme', preference);
		} catch {
			// El tema sigue funcionando en memoria aunque el navegador desactive el almacenamiento local.
		}
	}

	function updateThemeColor(preference: ThemePreference) {
		const dark = preference === 'dark' || (preference === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
		document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#1c1f21' : '#f5f6f4');
	}

	function setTheme(preference: ThemePreference) {
		theme = preference;
		applyTheme(preference);
	}

	function showToast(message: string, tone: 'info' | 'success' | 'error' = 'info') {
		toastMessage = message;
		toastTone = tone;
		if (toastTimeout) clearTimeout(toastTimeout);
		toastTimeout = setTimeout(() => { toastMessage = ''; }, 5200);
	}

	const stateRefresh = createStateRefresh(getState, (state) => {
		pulseState = state;
		eventRefreshPending = false;
		dataRevision += 1;
	});
	const refreshState = stateRefresh.refresh;

	function closeEventSource() {
		nativeSubscriptionRequest += 1;
		const unsubscribe = nativeUnsubscribe;
		nativeUnsubscribe = null;
		unsubscribe?.();
		eventSource?.close();
		eventSource = null;
		eventSourceConnected = false;
		if (eventFallbackTimer) clearInterval(eventFallbackTimer);
		eventFallbackTimer = undefined;
		if (initialEventTimer) clearTimeout(initialEventTimer);
		initialEventTimer = undefined;
		const resolvePendingEvent = resolveInitialEvent;
		resolveInitialEvent = undefined;
		resolvePendingEvent?.(false);
	}

	function cancelStartup() {
		const attempt = startupAttempt;
		startupAttempt = null;
		attempt?.dispose();
	}

	function expireSession() {
		stateRefresh.invalidate();
		cancelStartup();
		navigationGeneration += 1;
		navigationPending = false;
		drafts.clear();
		if (confirmResolver) answerConfirmation(false);
		closeEventSource();
		if (eventRefreshTimer) clearTimeout(eventRefreshTimer);
		eventRefreshTimer = undefined;
		eventRefreshPending = false;
		latestEventRevision = null;
		initialStateFetchStarted = false;
		initialStateReady = false;
		csrfToken = '';
		session = null;
		pulseState = null;
		bootStatus = 'unauthenticated';
	}

	function scheduleEventRefresh() {
		if (eventRefreshTimer) clearTimeout(eventRefreshTimer);
		eventRefreshTimer = setTimeout(() => {
			eventRefreshTimer = undefined;
			if (!eventRefreshPending) return;
			if (isBusy || updateBusy || document.visibilityState === 'hidden' || !session?.authenticated) return;
			eventRefreshPending = false;
			void refreshState().catch((error) => {
				if (error instanceof ApiError && error.status === 401) expireSession();
				else showToast('No se pudo sincronizar Pulse con el servidor.', 'error');
			});
		}, 90);
	}

	function requestStateRefresh() {
		eventRefreshPending = true;
		if (stateRefresh.markDirty()) {
			if (eventRefreshTimer) clearTimeout(eventRefreshTimer);
			eventRefreshTimer = undefined;
			return;
		}
		if (initialStateReady && !isBusy && !updateBusy) scheduleEventRefresh();
	}

	function startEventFallback() {
		if (updateBusy || eventFallbackTimer || !session?.authenticated || document.visibilityState === 'hidden') return;
		eventFallbackTimer = setInterval(() => {
			if (updateBusy || eventSourceConnected || !session?.authenticated || document.visibilityState === 'hidden' || !navigator.onLine) return;
			requestStateRefresh();
		}, 60_000);
	}

	function handleStateEvent(event: Event) {
		let revision: unknown;
		try {
			revision = JSON.parse((event as MessageEvent<string>).data)?.revision;
		} catch {
			return;
		}
		if (typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 0) return;
		const previousRevision = latestEventRevision;
		latestEventRevision = revision;
		if (previousRevision === null) {
			if (initialStateFetchStarted || initialStateReady) requestStateRefresh();
			return;
		}
		if (revision !== previousRevision) requestStateRefresh();
	}

	function handleNativeRevision(revision: number) {
		if (!Number.isSafeInteger(revision) || revision < 0) return;
		const previousRevision = latestEventRevision;
		latestEventRevision = revision;
		if (previousRevision === null || revision !== previousRevision) requestStateRefresh();
	}

	async function openNativeSubscription(): Promise<boolean> {
		if (!nativeBridge || !session?.authenticated || document.visibilityState === 'hidden' || nativeUnsubscribe) return Boolean(nativeUnsubscribe);
		const request = ++nativeSubscriptionRequest;
		let failed = false;
		try {
			const unsubscribe = await nativeBridge.subscribe((revision) => {
				if (request === nativeSubscriptionRequest && session?.authenticated) handleNativeRevision(revision);
			}, () => {
				if (request !== nativeSubscriptionRequest) return;
				failed = true;
				nativeSubscriptionRequest += 1;
				const cleanup = nativeUnsubscribe;
				nativeUnsubscribe = null;
				cleanup?.();
				eventSourceConnected = false;
				startEventFallback();
			});
			if (failed || request !== nativeSubscriptionRequest || !session?.authenticated || isDocumentHidden()) {
				unsubscribe();
				return false;
			}
			nativeUnsubscribe = unsubscribe;
			eventSourceConnected = true;
			if (eventFallbackTimer) clearInterval(eventFallbackTimer);
			eventFallbackTimer = undefined;
			return true;
		} catch {
			if (request === nativeSubscriptionRequest) {
				eventSourceConnected = false;
				startEventFallback();
			}
			return false;
		}
	}

	function openEventSource(): Promise<boolean> {
		if (updateBusy) return Promise.resolve(false);
		if (nativeApp) return openNativeSubscription();
		if (!session?.authenticated || document.visibilityState === 'hidden' || eventSource) return Promise.resolve(false);
		let source: EventSource;
		try {
			source = new EventSource('/api/events', { withCredentials: true });
		} catch {
			startEventFallback();
			return Promise.resolve(false);
		}
		eventSource = source;
		const initialStateEvent = new Promise<boolean>((resolve) => { resolveInitialEvent = resolve; });
		initialEventTimer = setTimeout(() => {
			initialEventTimer = undefined;
			const resolve = resolveInitialEvent;
			resolveInitialEvent = undefined;
			resolve?.(false);
		}, 1500);
		source.addEventListener('state', (event) => {
			if (eventSource !== source) return;
			eventSourceConnected = true;
			if (eventFallbackTimer) clearInterval(eventFallbackTimer);
			eventFallbackTimer = undefined;
			if (initialEventTimer) clearTimeout(initialEventTimer);
			initialEventTimer = undefined;
			const resolve = resolveInitialEvent;
			resolveInitialEvent = undefined;
			resolve?.(true);
			handleStateEvent(event);
		});
		source.addEventListener('open', () => {
			if (eventSource !== source || !initialStateReady) return;
			requestStateRefresh();
		});
		source.addEventListener('error', () => {
			if (eventSource !== source) return;
			eventSourceConnected = false;
			startEventFallback();
			if (!navigator.onLine || Date.now() < nextSessionCheckAt) return;
			nextSessionCheckAt = Date.now() + 30_000;
			void getSession().then((currentSession) => {
				if (eventSource !== source) return;
				if (!currentSession.authenticated) {
					expireSession();
					return;
				}
				session = currentSession;
				csrfToken = currentSession.csrf_token;
			}).catch(() => {
				// EventSource reconecta solo; el fallback visible mantiene el estado al día.
			});
		});
		if (initialStateReady) startEventFallback();
		return initialStateEvent;
	}

	function handleVisibilityChange() {
		if (document.visibilityState === 'hidden') {
			closeEventSource();
			return;
		}
		if (bootStatus !== 'ready') return;
		void openEventSource();
		if (eventRefreshPending) scheduleEventRefresh();
	}

	function isDocumentHidden(): boolean {
		return document.visibilityState === 'hidden';
	}

	async function startSession() {
		if (updateBusy || startupAttempt) return;
		stateRefresh.invalidate();
		const attempt = createStartupAttempt();
		startupAttempt = attempt;
		closeEventSource();
		if (eventRefreshTimer) clearTimeout(eventRefreshTimer);
		eventRefreshTimer = undefined;
		bootStatus = 'loading';
		bootMessage = 'Estamos conectando con Pulse…';
		session = null;
		csrfToken = '';
		pulseState = null;
		latestEventRevision = null;
		eventRefreshPending = false;
		initialStateFetchStarted = false;
		initialStateReady = false;
		try {
			if (nativeBridge) {
				try {
					nativeStatus = await attempt.wait(nativeBridge.status());
				} catch (error) {
					if (attempt.signal.aborted) throw error;
					throw new ApiError('No se pudo consultar el acceso de la app. Inténtalo de nuevo.', 0);
				}
			}
			const currentSession = await attempt.wait(getSession(attempt.signal));
			if (startupAttempt !== attempt) return;
			session = currentSession;
			csrfToken = session.csrf_token;
			if (!session.authenticated) {
				closeEventSource();
				latestEventRevision = null;
				eventRefreshPending = false;
				pulseState = null;
				bootStatus = 'unauthenticated';
				return;
			}
			const receivedInitialEvent = await attempt.wait(openEventSource());
			if (startupAttempt !== attempt || !session?.authenticated) return;
			initialStateFetchStarted = true;
			const currentState = await attempt.wait(getState(attempt.signal));
			if (startupAttempt !== attempt || !session?.authenticated) return;
			pulseState = currentState;
			initialStateReady = true;
			if (!receivedInitialEvent) {
				eventSourceConnected = false;
				startEventFallback();
			}
			bootStatus = 'ready';
			if (!isDocumentHidden()) void openEventSource();
			if (eventRefreshPending) scheduleEventRefresh();
		} catch (error) {
			if (startupAttempt !== attempt) return;
			if (error instanceof ApiError && error.status === 401) {
				expireSession();
				return;
			}
			closeEventSource();
			session = null;
			csrfToken = '';
			pulseState = null;
			initialStateReady = false;
			eventRefreshPending = false;
			bootStatus = 'error';
			bootMessage = !navigator.onLine
				? 'Pulse está sin conexión. El contenido personal no se guarda en este dispositivo.'
				: error instanceof Error ? error.message : 'No se pudo conectar con Pulse.';
		} finally {
			attempt.dispose();
			if (startupAttempt === attempt) startupAttempt = null;
		}
	}

	async function retryStartup() {
		const restoreFocus = document.activeElement === bootRetryButton;
		await startSession();
		if (!restoreFocus || bootStatus !== 'error') return;
		await tick();
		// Restore only the focus lost with the retry control, never another interaction.
		if (document.hasFocus() && document.activeElement === document.body) {
			bootRetryButton?.focus({ preventScroll: true });
		}
	}

	async function mutate(path: string, method: HttpMethod, data?: MutationPayload, success = 'Cambios guardados.') {
		if (updateBusy) { showToast('Pulse se está actualizando. Espera antes de guardar cambios.', 'info'); return false; }
		stateRefresh.invalidate();
		busyCount += 1;
		try {
			await mutateApi(path, method, csrfToken, data);
			const eventRevisionAtRefreshStart = latestEventRevision;
			eventRefreshPending = false;
			try {
				if (!await refreshState()) return false;
				showToast(success, 'success');
			} catch {
				eventRefreshPending = true;
				showToast('El servidor guardó el cambio, pero no se pudo actualizar la vista. Pulsa «Reintentar».', 'info');
			}
			if (latestEventRevision !== eventRevisionAtRefreshStart) eventRefreshPending = true;
			return true;
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) {
				expireSession();
				return false;
			}
			const message = error instanceof Error ? error.message : 'No se pudo completar la operación.';
			showToast(message, 'error');
			return false;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	async function reserveChargeCreation(): Promise<ChargeCreationRequest> {
		if (updateBusy) throw new Error('Pulse se está actualizando. Espera antes de guardar la carga.');
		busyCount += 1;
		try {
			const result = await mutateApi<ChargeCreationRequest>('/api/charges/requests', 'POST', csrfToken);
			if (!result || typeof result.id !== 'string' || !/^[a-f0-9]{32}$/.test(result.id)
				|| typeof result.expires_at !== 'string' || !Number.isFinite(Date.parse(result.expires_at))) {
				throw new Error('No se pudo preparar el guardado de la carga.');
			}
			return result;
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	async function reserveJourneyCreation(): Promise<ManualJourneyCreationRequest> {
		if (updateBusy) throw new Error('Pulse se está actualizando. Espera antes de guardar el trayecto.');
		busyCount += 1;
		try {
			const result = await mutateApi<ManualJourneyCreationRequest>('/api/journeys/requests', 'POST', csrfToken);
			if (!result || typeof result.id !== 'string' || !/^[a-f0-9]{32}$/.test(result.id)
				|| typeof result.expires_at !== 'string' || !Number.isFinite(Date.parse(result.expires_at))) {
				throw new Error('No se pudo preparar el guardado del trayecto.');
			}
			return result;
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	async function quoteCharge(payload: ChargeQuotePayload): Promise<ChargeQuote> {
		if (updateBusy) throw new Error('Pulse se está actualizando. Espera antes de calcular el coste.');
		busyCount += 1;
		try {
			return await mutateApi<ChargeQuote>('/api/charges/quote', 'POST', csrfToken, payload);
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	async function navigationRead<T>(path: Parameters<typeof queryNavigation>[0], payload?: object, signal?: AbortSignal): Promise<T> {
		if (updateBusy) throw new Error('Pulse se está actualizando. Espera antes de consultar el mapa.');
		if (!online) throw new Error('No hay conexión. Vuelve a intentarlo cuando se recupere.');
		try { return await queryNavigation<T>(path, csrfToken, payload, signal); }
		catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		}
	}

	const navigationClient: PageProps['navigation'] = {
		status: (signal) => navigationRead('/api/navigation/status', undefined, signal),
		search: (query, signal) => navigationRead('/api/navigation/places', { query, consent: true }, signal),
		chargers: (latitude, longitude, signal) => navigationRead('/api/navigation/chargers', { latitude, longitude, consent: true }, signal),
		tile: (z, x, y, signal) => navigationRead('/api/navigation/tiles', { z, x, y, consent: true }, signal)
	};

	async function createWidgetPairing(profileId: string): Promise<WidgetPairingResult> {
		if (updateBusy) throw new Error('Pulse se está actualizando. Espera antes de enlazar el dispositivo.');
		busyCount += 1;
		try {
			return await mutateApi<WidgetPairingResult>(
				`/api/widget-profiles/${encodeURIComponent(profileId)}/pairings`,
				'POST',
				csrfToken,
				{}
			);
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
		}
	}

	function handleNotificationApiError(error: unknown): never {
		if (error instanceof ApiError && error.status === 401) expireSession();
		throw error;
	}

	const notifications: PageProps['notifications'] = {
		capabilities: async () => {
			try { return await (await import('./lib/push')).getPushCapabilities(); }
			catch (error) { return handleNotificationApiError(error); }
		},
		devices: async () => {
			try { return await (await import('./lib/push')).getPushDevices(); }
			catch (error) { return handleNotificationApiError(error); }
		},
		register: async (name: string, subscription: WebPushSubscriptionInput): Promise<PushDevice> => {
			try {
				return await mutateApi<PushDevice>('/api/push/subscriptions', 'POST', csrfToken, { name, subscription });
			} catch (error) { return handleNotificationApiError(error); }
		},
		revoke: async (id: string): Promise<void> => {
			try { await mutateApi(`/api/push/subscriptions/${encodeURIComponent(id)}`, 'DELETE', csrfToken, {}); }
			catch (error) { handleNotificationApiError(error); }
		},
		test: async (id: string): Promise<PushTestResult> => {
			try {
				return await mutateApi<PushTestResult>(`/api/push/subscriptions/${encodeURIComponent(id)}/test`, 'POST', csrfToken, { confirmed: true });
			} catch (error) { return handleNotificationApiError(error); }
		},
		setBackupAlerts: async (id: string, enabled: boolean): Promise<PushDevice> => {
			try {
				return await mutateApi<PushDevice>(`/api/push/subscriptions/${encodeURIComponent(id)}/backup-alerts`, 'POST', csrfToken, { enabled, confirmed: enabled });
			} catch (error) { return handleNotificationApiError(error); }
		}
	};

	async function runNotificationAction(operation: () => Promise<void>): Promise<void> {
		if (updateBusy || isBusy) throw new Error('Termina la acción en curso antes de gestionar las notificaciones.');
		busyCount += 1;
		try {
			await operation();
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) expireSession();
			throw error;
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	function confirmAction(title: string, message: string, label = 'Confirmar'): Promise<boolean> {
		// Never replace another action's resolver or its consent dialog.
		if (confirmResolver || confirmDialog?.open) return Promise.resolve(false);
		return new Promise((resolve) => {
			confirmTitle = title;
			confirmMessage = message;
			confirmLabel = label;
			confirmResolver = resolve;
			void tick().then(() => {
				if (confirmResolver !== resolve) return;
				try { confirmDialog.showModal(); }
				catch { answerConfirmation(false); }
			});
		});
	}

	function answerConfirmation(answer: boolean) {
		if (confirmDialog.open) confirmDialog.close();
		const resolve = confirmResolver;
		confirmResolver = null;
		resolve?.(answer);
	}

	function historyState(index: number) {
		return { ...(history.state && typeof history.state === 'object' ? history.state : {}), pulse: { scope: historyScope, index } };
	}

	function managedHistoryIndex(state: unknown): number | null {
		if (!state || typeof state !== 'object' || !('pulse' in state)) return null;
		const entry = state.pulse;
		if (!entry || typeof entry !== 'object' || !('scope' in entry) || !('index' in entry)) return null;
		return entry.scope === historyScope && typeof entry.index === 'number' && Number.isSafeInteger(entry.index) && entry.index >= 0
			? entry.index : null;
	}

	function restoreHistory(targetIndex: number | null) {
		if (targetIndex !== null && targetIndex !== historyIndex) {
			restoringHistoryIndex = historyIndex;
			history.go(historyIndex - targetIndex);
		} else {
			// Entries from before this app load have no reliable traversal direction.
			// Keep the active form and URL; never reload to cancel a traversal.
			history.replaceState(historyState(historyIndex), '', currentLocation);
			restoringHistoryIndex = null;
		}
	}

	async function approveDraftExit(title = 'Cambios sin guardar', label = 'Descartar y salir'): Promise<boolean> {
		if (!drafts.hasDirty()) return true;
		if (drafts.hasUnconfirmedSave()) {
			return confirmAction(title === 'Cambios sin guardar' ? 'Guardado sin confirmar' : title,
				'El guardado anterior podría haberse completado. Revisa el historial antes de continuar. Si sales, se descartará el formulario; puedes cancelar para conservarlo.', label);
		}
		return confirmAction(title, 'Hay cambios sin guardar en esta pantalla. Si continúas, se descartarán. Puedes cancelar para seguir editando.', label);
	}

	function showRoute(path: string) {
		compactNavigationOpen = false;
		const nextRoute = routeFromPath(new URL(path, window.location.origin).pathname);
		if (route !== nextRoute || preparationSection(path)) routeFocusPending = true;
		else pageTitle?.focus({ preventScroll: true });
		route = nextRoute;
		currentLocation = path;
		window.scrollTo({ top: 0, behavior: 'instant' });
	}

	async function navigate(event: MouseEvent, path: string) {
		if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		if (restoringHistoryIndex !== null || navigationPending || confirmResolver) return;
		if (routeFromPath(new URL(path, window.location.origin).pathname) === route) {
			if (path !== currentLocation) {
				historyIndex += 1;
				history.pushState(historyState(historyIndex), '', path);
			}
			showRoute(path);
			return;
		}
		if (isBusy || updateBusy) { showToast('Termina la acción en curso antes de cambiar de sección.', 'info'); return; }
		const generation = ++navigationGeneration;
		navigationPending = true;
		try {
			if (!await approveDraftExit() || generation !== navigationGeneration || !session?.authenticated || isBusy || updateBusy) return;
			historyIndex += 1;
			history.pushState(historyState(historyIndex), '', path);
			showRoute(path);
		} finally {
			if (generation === navigationGeneration) navigationPending = false;
		}
	}

	async function handlePopstate(event: PopStateEvent) {
		const targetIndex = managedHistoryIndex(event.state);
		const path = window.location.pathname + window.location.search + window.location.hash;
		if (restoringHistoryIndex !== null) {
			if (targetIndex === restoringHistoryIndex) {
				restoringHistoryIndex = null;
				if (path !== currentLocation) history.replaceState(historyState(historyIndex), '', currentLocation);
			} else restoreHistory(targetIndex);
			return;
		}
		if (path === currentLocation) {
			historyIndex = targetIndex ?? 0;
			if (targetIndex === null) history.replaceState(historyState(historyIndex), '', path);
			return;
		}
		if (navigationPending) {
			navigationGeneration += 1;
			navigationPending = false;
			answerConfirmation(false);
			restoreHistory(targetIndex);
			return;
		}
		if (confirmResolver || isBusy || updateBusy) { restoreHistory(targetIndex); return; }
		if (routeFromPath(window.location.pathname) === route) {
			historyIndex = targetIndex ?? 0;
			if (targetIndex === null) history.replaceState(historyState(historyIndex), '', path);
			showRoute(path);
			return;
		}
		const generation = ++navigationGeneration;
		navigationPending = true;
		try {
			const approved = await approveDraftExit();
			if (generation !== navigationGeneration) return;
			if (!approved || isBusy || updateBusy || !session?.authenticated) { restoreHistory(targetIndex); return; }
			historyIndex = targetIndex ?? 0;
			if (targetIndex === null) history.replaceState(historyState(historyIndex), '', path);
			showRoute(path);
		} finally {
			if (generation === navigationGeneration) navigationPending = false;
		}
	}

	function handleInternalLink(event: MouseEvent) {
		if (event.defaultPrevented || !(event.target instanceof Element)) return;
		const link = event.target.closest('a');
		if (!(link instanceof HTMLAnchorElement) || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
		const url = new URL(link.href);
		const path = internalAppPath(url, window.location.origin, navigation.map((item) => item.path));
		if (path !== null) void navigate(event, path);
	}

	function handleBeforeUnload(event: BeforeUnloadEvent) {
		if (approvedReload || (!drafts.hasDirty() && !isBusy)) return;
		event.preventDefault();
		event.returnValue = '';
	}

	function handleOnlineChange() {
		online = navigator.onLine;
		if (!online) return;
		if (bootStatus === 'error') void startSession();
		else if (bootStatus === 'ready') void openEventSource();
	}

	function safeLoginUrl(): string {
		const candidate = session?.login_url || '/api/auth/login';
		try {
			const url = new URL(candidate, window.location.origin);
			if (url.origin === window.location.origin) return url.href;
		} catch {
			// El enlace de acceso es opcional; se usa la ruta local conocida del backend.
		}
		return new URL('/api/auth/login', window.location.origin).href;
	}

	async function beginLogin() {
		if (nativeApp) {
			if (!nativeBridge || !nativeStatus?.configured || !online || loginBusy) return;
			loginBusy = true;
			try {
				await nativeBridge.login();
				await startSession();
			} catch {
				bootStatus = 'error';
				bootMessage = 'No se pudo iniciar sesión. Comprueba la conexión e inténtalo de nuevo.';
			} finally {
				loginBusy = false;
			}
			return;
		}
		window.location.assign(safeLoginUrl());
	}

	async function logout() {
		if (updateBusy || isBusy || navigationPending || confirmResolver || restoringHistoryIndex !== null) return;
		const generation = navigationGeneration;
		if (!await approveDraftExit('Cerrar sesión con cambios sin guardar', 'Descartar y cerrar sesión') || generation !== navigationGeneration || !session?.authenticated) return;
		busyCount += 1;
		try {
			if (nativeBridge) await nativeBridge.logout();
			else await mutateApi('/api/auth/logout', 'POST', csrfToken, {});
			expireSession();
			showToast('Has cerrado la sesión.', 'success');
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) {
				expireSession();
				return;
			}
			showToast(nativeBridge ? 'No se pudo cerrar la sesión. Inténtalo de nuevo.' : error instanceof Error ? error.message : 'No se pudo cerrar la sesión.', 'error');
		} finally {
			busyCount = Math.max(0, busyCount - 1);
		}
	}

	async function exportData(): Promise<BackupActionResult> {
		try {
			const response = await requestApi('/api/export', {
				credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }
			});
			if (!response.ok) {
				let message = 'No se pudo crear la copia.';
				try {
					const body = await response.json();
					if (typeof body.detail === 'string') message = body.detail;
				} catch { /* No se muestra HTML ni respuestas inesperadas. */ }
				throw new ApiError(message, response.status);
			}
			const blob = await response.blob();
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = 'leapmotor-pulse-backup.json';
			document.body.append(link);
			link.click();
			link.remove();
			URL.revokeObjectURL(url);
			showToast('La copia JSON está preparada. Comprueba la descarga en el navegador.', 'success');
			return { status: 'success' };
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) {
				expireSession();
				return { status: 'cancelled' };
			}
			const message = error instanceof Error ? error.message : 'No se pudo crear la copia.';
			showToast(message, 'error');
			return { status: 'error', message };
		}
	}

	async function importData(file: File): Promise<BackupActionResult> {
		if (updateBusy) {
			const message = 'Pulse se está actualizando. Espera antes de restaurar una copia.';
			showToast(message, 'info'); return { status: 'error', message };
		}
		if (isBusy) {
			const message = 'Termina la acción en curso antes de restaurar una copia.';
			showToast(message, 'info'); return { status: 'error', message };
		}
		busyCount += 1;
		try {
			let data: Record<string, unknown>;
			try {
				const parsed: unknown = JSON.parse(await file.text());
				if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
					showToast('La copia JSON debe contener un objeto en la raíz.', 'error');
					return { status: 'error', message: 'La copia JSON debe contener un objeto en la raíz.' };
				}
				data = parsed as Record<string, unknown>;
			} catch {
				showToast('El archivo no contiene JSON válido.', 'error');
				return { status: 'error', message: 'El archivo no contiene JSON válido.' };
			}
			const confirmed = await confirmAction(
				'Restaurar una copia',
				'Esta operación sustituirá los datos actuales de Pulse por el contenido del archivo. El servidor validará la copia antes de aplicarla.' + (drafts.hasUnconfirmedSave()
					? ' También se descartará el formulario de un guardado sin confirmar, que podría haberse completado.'
					: drafts.hasDirty() ? ' También se descartarán los cambios sin guardar de esta pantalla.' : ''),
				'Restaurar datos'
			);
			if (confirmed) {
				const beforeRefresh = pulseState;
				if (await mutate('/api/import', 'POST', { confirmed: true, data }, 'La copia se ha restaurado.')) {
					drafts.clear();
					formContextRevision += 1;
					if (pulseState === beforeRefresh) {
						closeEventSource();
						pulseState = null;
						bootStatus = 'error';
						bootMessage = 'La copia se restauró, pero no se pudo leer el estado nuevo. Pulsa «Reintentar» para continuar.';
					}
					return { status: 'success' };
				}
				return { status: 'error', message: 'No se ha podido confirmar la restauración. Revisa los datos actuales antes de reintentarlo.' };
			}
			return { status: 'cancelled' };
		} finally {
			busyCount = Math.max(0, busyCount - 1);
			if (!isBusy && eventRefreshPending) scheduleEventRefresh();
		}
	}

	async function installApp() {
		if (!installPrompt) return;
		try {
			await installPrompt.prompt();
			const result = await installPrompt.userChoice;
			if (result.outcome === 'accepted') showToast('Pulse se está añadiendo a tus aplicaciones.', 'success');
		} catch {
			showToast('El navegador no pudo iniciar la instalación.', 'error');
		} finally {
			installPrompt = null;
			canInstall = false;
		}
	}

	function setupServiceWorker() {
		if (nativeApp || !('serviceWorker' in navigator)) return () => {};
		const container = navigator.serviceWorker;
		let disposed = false;
		let stopObserving = () => {};
		const controllerChanged = () => {
			if (approvedReload && updateBusy && !isBusy) window.location.reload();
		};
		container.addEventListener('controllerchange', controllerChanged);
		void container.register('/sw.js', { scope: '/' }).then((registration) => {
			if (disposed) return;
			serviceWorkerRegistration = registration;
			stopObserving = observeServiceWorkerUpdate(registration,
				() => Boolean(container.controller), () => { updateAvailable = true; });
		}).catch(() => {
			// La aplicación sigue siendo utilizable como web aunque el navegador no registre el SW.
		});
		return () => {
			disposed = true;
			stopObserving();
			container.removeEventListener('controllerchange', controllerChanged);
		};
	}

	function updateBlocked() {
		return nativeApp || !updateAvailable || bootStatus === 'loading' || loginBusy || isBusy || updateBusy || navigationPending || confirmResolver !== null || restoringHistoryIndex !== null;
	}

	async function applyUpdate() {
		if (updateBlocked()) return;
		const generation = navigationGeneration;
		if (!await approveDraftExit('Actualizar con cambios sin guardar', 'Descartar y actualizar') || generation !== navigationGeneration || updateBlocked()) return;
		updateBusy = true;
		approvedReload = true;
		// Una respuesta SSE interceptada por el servicio anterior puede impedir su relevo.
		closeEventSource();
		try {
			const worker = serviceWorkerRegistration?.waiting;
			if (!worker) {
				window.location.reload();
				return;
			}
			worker.postMessage({ type: 'PULSE_SKIP_WAITING' });
			updateTimeout = setTimeout(() => {
				recoverUpdate();
				showToast('La actualización está tardando. Cierra otras pestañas de Pulse y vuelve a intentarlo.', 'error');
			}, 8000);
		} catch {
			recoverUpdate();
			showToast('No se pudo comprobar la actualización. Recarga la página para volver a intentarlo.', 'error');
		}
	}

	function recoverUpdate() {
		if (updateTimeout) clearTimeout(updateTimeout);
		updateTimeout = undefined;
		updateBusy = false;
		approvedReload = false;
		void openEventSource();
		if (eventRefreshPending) scheduleEventRefresh();
	}

	onMount(() => {
		history.replaceState(historyState(historyIndex), '', currentLocation);
		try {
			const saved = localStorage.getItem('pulse-theme');
			if (saved === 'light' || saved === 'dark' || saved === 'system') theme = saved;
		} catch { /* La preferencia predeterminada es suficiente cuando localStorage está bloqueado. */ }
		applyTheme(theme);
		const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
		const themeChanged = () => updateThemeColor(theme);
		themeMedia.addEventListener('change', themeChanged);
		void startSession();
		const stopServiceWorker = setupServiceWorker();
		document.addEventListener('visibilitychange', handleVisibilityChange);
		document.addEventListener('click', handleInternalLink);
		window.addEventListener('beforeunload', handleBeforeUnload);
		const beforeInstall = (event: Event) => {
			event.preventDefault();
			installPrompt = event as BeforeInstallPromptEvent;
			canInstall = true;
		};
		const installed = () => { installPrompt = null; canInstall = false; };
		if (!nativeApp) {
			window.addEventListener('beforeinstallprompt', beforeInstall);
			window.addEventListener('appinstalled', installed);
		}
		return () => {
			stateRefresh.invalidate();
			cancelStartup();
			stopServiceWorker();
			drafts.clear();
			if (confirmResolver) answerConfirmation(false);
			themeMedia.removeEventListener('change', themeChanged);
			if (updateTimeout) clearTimeout(updateTimeout);
			closeEventSource();
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			document.removeEventListener('click', handleInternalLink);
			window.removeEventListener('beforeunload', handleBeforeUnload);
			if (eventRefreshTimer) clearTimeout(eventRefreshTimer);
			if (!nativeApp) {
				window.removeEventListener('beforeinstallprompt', beforeInstall);
				window.removeEventListener('appinstalled', installed);
			}
			if (toastTimeout) clearTimeout(toastTimeout);
		};
	});
</script>

<svelte:window onpopstate={handlePopstate} ononline={handleOnlineChange} onoffline={handleOnlineChange} />

{#snippet updateNotice()}
	{#if updateAvailable}
		<div class="update-ribbon" role="status">
			<span><strong>Pulse tiene una actualización lista.</strong> Se aplicará solo cuando tú lo indiques.</span>
			<button class="button button-small button-secondary" type="button" disabled={updateBlocked()} onclick={applyUpdate}>{updateBusy ? 'Actualizando…' : bootStatus === 'loading' ? 'Conectando…' : isBusy || loginBusy ? 'Termina la acción primero' : 'Actualizar ahora'}</button>
		</div>
	{/if}
{/snippet}

{#if bootStatus === 'loading'}
	<main class="boot-screen" aria-live="polite">
		<div class="boot-brand">
			<span class="boot-mark" aria-hidden="true"><img src="/icon.svg" width="56" height="56" alt="" /></span>
			<span class="boot-brand-name">{appName}</span>
		</div>
		<h1>{bootMessage}</h1>
		<span class="boot-progress" aria-hidden="true"></span>
		{@render updateNotice()}
	</main>
{:else if bootStatus === 'error'}
	<main class="boot-screen">
		<div class="boot-brand">
			<span class="boot-mark" aria-hidden="true"><img src="/icon.svg" width="56" height="56" alt="" /></span>
			<span class="boot-brand-name">{appName}</span>
		</div>
		<div role="alert" aria-atomic="true"><h1>{bootMessage}</h1></div>
		<p class="body-copy">La aplicación no almacena datos del vehículo en este dispositivo. Cuando vuelva la conexión, podrás cargar el estado más reciente del servidor.</p>
		<button class="button button-primary" type="button" bind:this={bootRetryButton} disabled={updateBusy} onclick={retryStartup}>Reintentar conexión</button>
		{#if !online}<span class="status-pill status-neutral"><span class="status-dot"></span> Sin conexión</span>{/if}
		{@render updateNotice()}
	</main>
{:else if bootStatus === 'unauthenticated'}
	<main class="boot-screen">
		<div class="boot-brand">
			<span class="boot-mark" aria-hidden="true"><img src="/icon.svg" width="56" height="56" alt="" /></span>
			<span class="boot-brand-name">{appName}</span>
		</div>
		<h1>Tu espacio privado de movilidad.</h1>
		<p class="body-copy">Inicia sesión para consultar los datos guardados en tu servidor Pulse.</p>
		{#if nativeApp}
			{#if nativeStatus?.configured}
				<button class="button button-primary" type="button" disabled={!online || loginBusy} onclick={beginLogin}>{loginBusy ? 'Abriendo acceso…' : 'Iniciar sesión'} <Glyph name="arrow" /></button>
			{:else}
				<p class="body-copy" role="status">El acceso de esta app aún no está configurado.</p>
				<button class="button button-primary" type="button" disabled>Iniciar sesión</button>
			{/if}
		{:else}
			{#if updateBusy}
				<button class="button button-primary" type="button" disabled>Iniciar sesión <Glyph name="arrow" /></button>
			{:else}
				<a class="button button-primary" href={safeLoginUrl()}>Iniciar sesión <Glyph name="arrow" /></a>
			{/if}
		{/if}
		{@render updateNotice()}
	</main>
{:else if pulseState}
	<a class="skip-link" href="#pulse-content">Saltar al contenido</a>
	<div class="app-shell" data-route={route}>
		<aside class="desktop-rail" aria-label="Navegación principal">
			<a class="brand-lockup" href="/" onclick={(event) => navigate(event, '/')} aria-label={`${appName}, ir al resumen`}>
				<span class="brand-monogram" aria-hidden="true"><svg viewBox="0 0 36 36" fill="none"><path d="M5 25V10h12a7 7 0 0 1 0 14h-4V15h4a2 2 0 0 1 0 4" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M26 26h5" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/></svg></span><span><strong>PULSE</strong><small>Movilidad personal</small></span>
			</a>
			<nav class="primary-nav" aria-label="Secciones de Pulse">
				{#each navigation as item (item.route)}
					<a class:active={route === item.route} href={item.path} aria-current={route === item.route ? 'page' : undefined} onclick={(event) => navigate(event, item.path)}>
						<Glyph name={item.icon} size={19} /><span>{item.label}</span>
					</a>
				{/each}
			</nav>
			<div class="rail-spacer"></div>
			<div class="rail-footer">
				<div class="rail-connection"><span class:offline={!online} class="status-dot"></span><span>{online ? 'Red disponible' : 'Sin conexión'}</span></div>
				<a class="account-link" href="/settings" onclick={(event) => navigate(event, '/settings')}>
					<span class="account-avatar" aria-hidden="true">{(session?.user?.name || 'L').slice(0, 1).toUpperCase()}</span>
					<span><strong>{session?.user?.name || 'Cuenta personal'}</strong><small>Preferencias y copias</small></span>
					<Glyph name="chevron" size={16} />
				</a>
			</div>
		</aside>

		<div class="workspace">
			<header class="topbar">
				<div class="topbar-context">
					<a class="mobile-wordmark" href="/" onclick={(event) => navigate(event, '/')} aria-label="Pulse, ir a Coche">PULSE<span aria-hidden="true">.</span></a><h1 id="page-title" tabindex="-1" bind:this={pageTitle}>{titles[route]}</h1>
			{#if isDemo}
				<details class="demo-context"><summary><span class="demo-indicator" aria-hidden="true"></span>Demostración · vehículo simulado <Glyph name="chevron" size={13} /></summary><p>Los datos y controles del vehículo son ejemplos. Tus registros manuales se identifican por separado.</p></details>
			{/if}

				</div>
				<div class="topbar-actions">
					<a class="header-icon-link" href="/protection" aria-label="Ver historial de estados" onclick={(event) => navigate(event, '/protection')}><Glyph name="protection" size={20} /></a>
					<a class="header-profile-link" href="/settings" aria-label="Abrir ajustes del vehículo" onclick={(event) => navigate(event, '/settings')}><span aria-hidden="true">B10</span><span class="header-profile-name">{pulseState.settings.vehicle_name}</span><Glyph name="chevron" size={16} /></a>
					{#if !online}<span class="status-pill status-neutral mobile-network-status">Sin conexión</span>{/if}

				</div>
			</header>

			{@render updateNotice()}

			<main id="pulse-content" class="page-content" class:cockpit-content={route === 'overview'} tabindex="-1" aria-labelledby="page-title" aria-busy={!View && !viewLoadFailed}>
				{#if View}
					<div class="view-entry">
					{#key formContextRevision}
					<View
						{drafts}
						{pulseState}
						busy={isBusy || updateBusy}
						online={online}
						revision={dataRevision}
						{settingsSection}
						user={session?.user?.name ?? null}
						{theme}
						{canInstall}
						{mutate}
						{quoteCharge}
						{reserveChargeCreation}
						{reserveJourneyCreation}
						onSessionExpired={expireSession}
						navigation={navigationClient}
						{createWidgetPairing}
						{notifications}
						{runNotificationAction}
						{confirmAction}
						toast={showToast}
						{setTheme}
						onLogout={logout}
						onExport={exportData}
						onImport={importData}
						onInstall={installApp}
					/>
					{/key}
					</div>
				{:else if viewLoadFailed}
					<section class="route-load-state" aria-labelledby="route-load-title">
						<span class="route-load-mark" aria-hidden="true"><Glyph name={navigation.find((item) => item.route === route)?.icon ?? 'overview'} size={28} /></span>
						<h2 id="route-load-title">Esta sección no se ha podido abrir</h2>
						<p role="alert">Comprueba la conexión y vuelve a intentarlo.</p>
						<button class="button button-primary" type="button" bind:this={viewRetryButton} onclick={() => { routeFocusPending = true; viewRetryFocusRequested = true; viewLoadAttempt += 1; }}>Reintentar sección <Glyph name="arrow" size={18} /></button>
					</section>
				{:else}
					<div class="route-load-state" role="status">
						<span class="route-load-mark" aria-hidden="true"><Glyph name={navigation.find((item) => item.route === route)?.icon ?? 'overview'} size={28} /></span>
						<p>Abriendo {titles[route].toLocaleLowerCase('es')}…</p>
						<div class="route-load-lines" aria-hidden="true"><span></span><span></span><span></span></div>
					</div>
				{/if}
			</main>
		</div>

		<div class="mobile-navigation-shell">
		<details class="compact-mobile-nav" bind:open={compactNavigationOpen}>
			<summary aria-expanded={compactNavigationOpen} aria-label={`Secciones de Pulse · ${titles[route]}`}><span>Secciones</span><strong>{titles[route]}</strong><Glyph name="overview" size={18} /></summary>
			<nav aria-label="Navegación principal">
				{#each navigation as item (item.route)}
					<a class:active={route === item.route} href={item.path} aria-current={route === item.route ? 'page' : undefined} onclick={(event) => navigate(event, item.path)}><Glyph name={item.icon} size={19} /><span>{item.label}</span></a>
				{/each}
			</nav>
		</details>
		<nav class="mobile-nav" aria-label="Navegación principal">
			{#each navigation.filter((item) => item.route !== 'protection') as item (item.route)}
				<a class:active={route === item.route} href={item.path} aria-current={route === item.route ? 'page' : undefined} onclick={(event) => navigate(event, item.path)}>
					<Glyph name={item.icon} size={20} /><span>{item.route === 'navigation' ? 'Mapa' : item.label}</span>
				</a>
			{/each}

		</nav>
		</div>
	</div>
{/if}

{#if toastMessage}
	<div class="toast" class:toast-error={toastTone === 'error'} class:toast-success={toastTone === 'success'} role="status" aria-live="polite"><span class="toast-mark" aria-hidden="true"><Glyph name={toastTone === 'success' ? 'check' : toastTone === 'error' ? 'close' : 'activity'} size={18} /></span><span>{toastMessage}</span></div>
{/if}

<dialog aria-labelledby="confirm-title" aria-describedby="confirm-message" class="confirm-dialog" bind:this={confirmDialog} oncancel={(event) => { event.preventDefault(); answerConfirmation(false); }}>
	<form method="dialog" onsubmit={(event) => event.preventDefault()}>
		<div class="dialog-mark" aria-hidden="true"><Glyph name="check" size={19} /></div>
		<h2 id="confirm-title">{confirmTitle}</h2>
		<p id="confirm-message" class="body-copy">{confirmMessage}</p>
		<div class="dialog-actions">
			<button class="button button-quiet" type="button" onclick={() => answerConfirmation(false)}>Cancelar</button>
			<button class="button button-primary" type="button" onclick={() => answerConfirmation(true)}>{confirmLabel}</button>
		</div>
	</form>
</dialog>
