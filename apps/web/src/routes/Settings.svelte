<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import AbrpIntegrationPanel from '../lib/AbrpIntegrationPanel.svelte';
	import Glyph from '../lib/Glyph.svelte';
	import HomeIntegrationPanel from '../lib/HomeIntegrationPanel.svelte';
	import NativeWidgetPanel from '../lib/NativeWidgetPanel.svelte';
	import PushNotificationsPanel from '../lib/PushNotificationsPanel.svelte';
	import SetupPanel from '../lib/SetupPanel.svelte';
	import { isNativeApp } from '../lib/native';
	import WidgetPreview from '../lib/WidgetPreview.svelte';
	import VehicleArtwork from '../lib/VehicleArtwork.svelte';
	import { vehicleColors, type VehicleColor } from '../lib/vehicle-colors';
	import { ApiError, getEnergyAnalytics, getNativeCapabilities, getWidgetGrants, getWidgetProfiles } from '../lib/api';
	import { count, dateTime, euros } from '../lib/format';
	import type { EnergyAnalytics, NativeCapabilities, PageProps, PricingMethod, ThemePreference, WidgetGrant, WidgetPairingResult, WidgetProfile, WidgetProfileField, WidgetProfileInput } from '../lib/types';

	type PreferencesDraft = { vehicleName: string; model: string; vehicleColor: VehicleColor; homeLabel: string; tariff: string; pricingMethod: PricingMethod };

	let { pulseState, busy, online, revision, user, theme, canInstall, mutate, createWidgetPairing, notifications, runNotificationAction, confirmAction, setTheme, onLogout, onExport, onImport, onInstall, drafts, settingsSection }: PageProps = $props();
	type SettingsSection = 'vehicle' | 'connections' | 'preferences' | 'data';
	let settingsSectionOverride = $state<SettingsSection | null>(null);
	let activeSettingsSection = $derived(settingsSectionOverride ?? (settingsSection === 'preparation' ? 'connections' : 'vehicle'));
	$effect(() => { if (settingsSection === 'preparation') settingsSectionOverride = null; });
	function selectSettingsSection(section: SettingsSection) { settingsSectionOverride = section; }
	let vehicleName = $state('');
	let model = $state('');
	let vehicleColor = $state<VehicleColor>(null);
	let homeLabel = $state('');
	let tariff = $state('');
	let pricingMethod = $state<PricingMethod>('manual');
	let settingsError = $state('');
	let settingsErrorField = $state<'vehicleName' | 'model' | 'homeLabel' | 'tariff' | null>(null);
	let preferencesSaving = $state(false);
	let preferencesBaseline: PreferencesDraft | null = null;
	let fileInput: HTMLInputElement;
	let importPickerButton = $state<HTMLButtonElement | undefined>(undefined);
	let importPickerRequest = 0;
	let backupBusy = $state(false);
	let backupError = $state('');
	let widgetProfilesHeading: HTMLHeadingElement | undefined;
	let widgetSection: HTMLElement | undefined;
	let connectionSection: HTMLElement | undefined;
	let connectionHeading: HTMLHeadingElement | undefined;
	let widgetActionError = $state('');
	let widgetProfileForm = $state<HTMLFormElement | undefined>(undefined);
	let demoToggleBusy = $state(false);
	let demoToggleError = $state('');
	let widgetProfiles = $state<WidgetProfile[]>([]);
	let widgetProfilesLoaded = $state(false);
	let widgetProfilesLoading = $state(false);
	let widgetProfilesError = $state('');
	let widgetProfileFormOpen = $state(false);
	let widgetProfileEditingId = $state<string | null>(null);
	let widgetProfileBaseline: WidgetProfileInput | null = null;
	let widgetProfileSelectedId = $state<string | null>(null);
	let widgetProfileBusy = $state(false);
	let widgetProfileFormError = $state('');
	let widgetProfileFormErrorField = $state<'name' | 'fields' | 'age' | null>(null);
	let widgetProfileRequest = 0;
	let widgetProfileSwitchRequest = 0;
	let widgetProfileDraft = $state<WidgetProfileInput>(newWidgetProfileDraft());
	let widgetEnergy = $state<EnergyAnalytics | null>(null);
	let widgetEnergyLoading = $state(false);
	let widgetEnergyError = $state('');
	let widgetEnergyRequest = 0;
	let widgetEnergyController: AbortController | null = null;
	let nativeCapabilities = $state<NativeCapabilities | null>(null);
	let nativeCapabilitiesLoading = $state(false);
	let nativeCapabilitiesError = $state('');
	let nativeCapabilitiesRequest = 0;
	let nativeCapabilitiesController: AbortController | null = null;
	let widgetGrants = $state<WidgetGrant[]>([]);
	let widgetGrantProfileId = $state<string | null>(null);
	let widgetGrantsLoaded = $state(false);
	let widgetGrantsLoading = $state(false);
	let widgetGrantsLoadingProfileId = $state<string | null>(null);
	let widgetGrantsError = $state('');
	let widgetGrantsErrorProfileId = $state<string | null>(null);
	let widgetGrantsRequest = 0;
	let widgetGrantsController: AbortController | null = null;
	let widgetPairingBusy = $state(false);
	let widgetPairingError = $state('');
	let widgetPairingResult = $state<WidgetPairingResult | null>(null);
	let widgetPairingProfileRevision: number | null = null;
	let widgetPairingProfileId = $state<string | null>(null);
	let widgetPairingVisible = $state(false);
	let widgetPairingCopyState = $state<'idle' | 'copied' | 'error'>('idle');
	let widgetPairingRequest = 0;
	let widgetPairingTimer: ReturnType<typeof setTimeout> | undefined;
	let previousWidgetProfileId: string | null = null;
	let settingsDisposed = false;

	function currentPreferences(): PreferencesDraft {
		return { vehicleName, model, vehicleColor, homeLabel, tariff, pricingMethod };
	}

	function samePreferences(left: PreferencesDraft, right: PreferencesDraft): boolean {
		return left.vehicleName === right.vehicleName && left.model === right.model && left.vehicleColor === right.vehicleColor && left.homeLabel === right.homeLabel && left.tariff === right.tariff && left.pricingMethod === right.pricingMethod;
	}

	function isPreferencesDirty(): boolean {
		if (!preferencesBaseline) return false;
		const current = currentPreferences();
		return current.vehicleName !== preferencesBaseline.vehicleName || current.model !== preferencesBaseline.model || current.vehicleColor !== preferencesBaseline.vehicleColor || current.homeLabel !== preferencesBaseline.homeLabel || current.pricingMethod !== preferencesBaseline.pricingMethod || (current.pricingMethod === 'manual' && current.tariff !== preferencesBaseline.tariff);
	}

	function applyPreferences(values: PreferencesDraft) {
		vehicleName = values.vehicleName;
		model = values.model;
		vehicleColor = values.vehicleColor;
		homeLabel = values.homeLabel;
		tariff = values.tariff;
		pricingMethod = values.pricingMethod;
	}

	function widgetProfileSnapshot(value: WidgetProfileInput): WidgetProfileInput {
		return { ...value, fields: [...value.fields] };
	}

	function isWidgetProfileDirty(): boolean {
		const baseline = widgetProfileBaseline;
		if (!baseline) return false;
		const current = widgetProfileDraft;
		return current.name !== baseline.name || current.appearance !== baseline.appearance || current.privacy_mode !== baseline.privacy_mode || current.max_age_minutes !== baseline.max_age_minutes || current.fields.length !== baseline.fields.length || current.fields.some((field, index) => field !== baseline.fields[index]);
	}

	onMount(() => {
		const unregisterPreferences = drafts.register('preferences', isPreferencesDirty);
		const unregisterWidgetProfile = drafts.register('widget-profile', isWidgetProfileDirty);
		return () => {
			unregisterPreferences();
			unregisterWidgetProfile();
		};
	});

	const widgetFieldLabels: Record<WidgetProfileField, string> = {
		battery_pct: 'Batería', range_km: 'Autonomía', charging: 'Estado de carga', locked: 'Cierre del coche',
		energy_kwh: 'Energía registrada', energy_cost_eur: 'Coste registrado'
	};
	const widgetTemplates: { id: string; name: string; detail: string; fields: WidgetProfileField[] }[] = [
		{ id: 'battery', name: 'Batería y autonomía', detail: 'Para preparar tu próxima salida', fields: ['battery_pct', 'range_km'] },
		{ id: 'vehicle', name: 'Estado del coche', detail: 'Cierre y estado de carga', fields: ['locked', 'charging'] },
		{ id: 'energy', name: 'Energía registrada', detail: 'kWh y coste de los últimos 30 días', fields: ['energy_kwh', 'energy_cost_eur'] }
	];
	const connectionStatusLabels: Record<string, string> = {
		missing_material: 'Material de conexión pendiente',
		not_connected: 'Proveedor sin conexión',
		connected: 'Proveedor disponible',
		disconnected: 'Sin conexión al proveedor',
		demo: 'Modo demostración'
	};
	let selectedWidgetProfileId = $derived(widgetProfiles.find((profile) => profile.id === widgetProfileSelectedId)?.id ?? widgetProfiles[0]?.id ?? null);
	let selectedWidgetProfile = $derived(widgetProfiles.find((profile) => profile.id === selectedWidgetProfileId) ?? null);
	let displayedWidgetGrants = $derived(widgetGrantProfileId === selectedWidgetProfileId ? widgetGrants : []);
	let grantsForSelectedProfile = $derived(selectedWidgetProfileId !== null && widgetGrantProfileId === selectedWidgetProfileId && widgetGrantsLoaded);
	let grantsLoadingForSelectedProfile = $derived(selectedWidgetProfileId !== null && widgetGrantsLoading && widgetGrantsLoadingProfileId === selectedWidgetProfileId);
	let grantsErrorForSelectedProfile = $derived(selectedWidgetProfileId !== null && widgetGrantsErrorProfileId === selectedWidgetProfileId ? widgetGrantsError : '');
	let pairingAtCapacity = $derived(grantsForSelectedProfile && nativeCapabilities !== null && displayedWidgetGrants.length >= nativeCapabilities.max_devices_per_profile);
	let pairingDisabled = $derived(!online || busy || widgetProfileBusy || widgetProfileFormOpen || widgetPairingBusy || nativeCapabilitiesLoading || Boolean(nativeCapabilitiesError) || !nativeCapabilities?.enabled || !selectedWidgetProfileId || !grantsForSelectedProfile || grantsLoadingForSelectedProfile || Boolean(grantsErrorForSelectedProfile) || pairingAtCapacity || widgetPairingResult !== null);
	let widgetPreviewProfile = $derived(widgetProfileFormOpen ? widgetProfileDraft : selectedWidgetProfile);
	let widgetPreviewIsEnergy = $derived(Boolean(widgetPreviewProfile?.fields.some(isEnergyField)));
	let widgetDraftIsEnergy = $derived(widgetProfileDraft.fields.some(isEnergyField));
	let widgetDraftTemplate = $derived(widgetTemplates.find((template) => template.fields.length === widgetProfileDraft.fields.length && template.fields.every((field) => widgetProfileDraft.fields.includes(field)))?.id ?? null);
	let widgetAvailableFields = $derived((Object.keys(widgetFieldLabels) as WidgetProfileField[]).filter((field) => isEnergyField(field) === widgetDraftIsEnergy));
	let hasLatestTelemetry = $derived(pulseState.mode === 'telemetry_read_only' && pulseState.telemetry?.latest !== null && pulseState.telemetry?.latest !== undefined);

	function connectionHeadline(): string {
		if (pulseState.mode === 'demo') return 'Demostración activa; coche sin conectar';
		if (hasLatestTelemetry) return 'Lectura sin controles';
		if (pulseState.mode === 'telemetry_read_only') return 'Preparación de lectura';
		return 'Integración del vehículo pendiente';
	}

	function connectionDetail(): string {
		if (hasLatestTelemetry) return pulseState.connection.reason || 'Esta integración proporciona lecturas y no habilita controles del vehículo.';
		if (pulseState.mode === 'telemetry_read_only') return pulseState.connection.reason || 'Aún no hay una observación disponible del proveedor.';
		return pulseState.connection.reason || 'Aún no hay un proveedor de vehículo habilitado.';
	}

	function connectionLabel(): string {
		if (pulseState.mode === 'demo') return 'Modo demostración';
		if (hasLatestTelemetry) return 'Lectura sin controles';
		if (pulseState.mode === 'telemetry_read_only') return 'Preparación';
		return connectionStatusLabels[pulseState.connection.status] ?? 'Estado de conexión no disponible';
	}

	function telemetryQualityLabel(): string {
		const status = pulseState.telemetry?.quality.temporal_status;
		const labels: Record<string, string> = {
			fresh: 'Antigüedad temporal dentro del umbral informado',
			stale: 'Antigüedad temporal fuera del umbral informado',
			unknown: 'Antigüedad temporal desconocida',
			unavailable: 'Antigüedad temporal no disponible'
		};
		return `${labels[status ?? 'unknown'] ?? 'Antigüedad temporal desconocida'}. Esta referencia no acredita frescura física.`;
	}

	function newWidgetProfileDraft(): WidgetProfileInput {
		return {
			name: '',
			fields: ['battery_pct', 'range_km'],
			appearance: 'system',
			privacy_mode: 'masked',
			max_age_minutes: 60
		};
	}

	function isEnergyField(field: WidgetProfileField): boolean {
		return field === 'energy_kwh' || field === 'energy_cost_eur';
	}

	function invalidateWidgetEnergy() {
		widgetEnergyRequest += 1;
		widgetEnergyController?.abort();
		widgetEnergyController = null;
		widgetEnergy = null;
		widgetEnergyLoading = false;
		widgetEnergyError = '';
	}

	async function loadWidgetEnergy() {
		invalidateWidgetEnergy();
		if (!online || !widgetPreviewIsEnergy || settingsDisposed) return;
		const request = widgetEnergyRequest;
		const controller = new AbortController();
		widgetEnergyController = controller;
		widgetEnergyLoading = true;
		try {
			const result = await getEnergyAnalytics(30, controller.signal);
			if (request !== widgetEnergyRequest || settingsDisposed || !online || !widgetPreviewIsEnergy) return;
			widgetEnergy = result;
		} catch (error) {
			if (request !== widgetEnergyRequest || settingsDisposed || !online || controller.signal.aborted) return;
			widgetEnergyError = error instanceof ApiError ? error.message : 'No se pudo calcular el resumen. Vuelve a intentarlo.';
		} finally {
			if (request === widgetEnergyRequest) { widgetEnergyLoading = false; widgetEnergyController = null; }
		}
	}

	$effect(() => {
		const needed = widgetPreviewIsEnergy;
		const reachable = online;
		const currentRevision = revision;
		queueMicrotask(() => {
			void currentRevision;
			if (settingsDisposed) return;
			if (!needed || !reachable) invalidateWidgetEnergy();
			else void loadWidgetEnergy();
		});
	});

	$effect(() => {
		const currentRevision = revision;
		const canReachServer = online;
		queueMicrotask(() => {
			void currentRevision;
			if (!canReachServer) {
				invalidateNativeCapabilities();
				invalidateWidgetGrants();
				clearWidgetPairing();
				if (!widgetProfilesLoaded) widgetProfilesError = 'Sin conexión. Conéctate para cargar los perfiles guardados en el servidor.';
				return;
			}
			void loadWidgetProfiles();
			void loadNativeCapabilities(currentRevision);
		});
	});

	$effect(() => {
		const profileId = selectedWidgetProfileId;
		const canReachServer = online;
		const currentRevision = revision;
		if (previousWidgetProfileId !== profileId) {
			previousWidgetProfileId = profileId;
			clearWidgetPairing();
			invalidateWidgetGrants();
			widgetGrants = [];
			widgetGrantProfileId = null;
			widgetGrantsLoaded = false;
			widgetGrantsError = '';
			widgetGrantsErrorProfileId = null;
		}
		queueMicrotask(() => {
			void currentRevision;
			if (!canReachServer || !profileId) {
				invalidateWidgetGrants();
				if (!canReachServer && profileId) {
					widgetGrantsErrorProfileId = profileId;
					widgetGrantsError = 'Sin conexión. Conéctate para consultar los dispositivos enlazados.';
				}
				return;
			}
			void loadWidgetGrants(profileId, currentRevision);
		});
	});

	$effect(() => {
		const incoming: PreferencesDraft = {
			vehicleName: pulseState.settings.vehicle_name,
			model: pulseState.settings.model,
			vehicleColor: pulseState.settings.vehicle_color ?? null,
			homeLabel: pulseState.settings.home_label,
			tariff: String(pulseState.settings.tariff_eur_kwh),
			pricingMethod: pulseState.settings.pricing_method ?? 'manual'
		};
		// Reconcile only when a server snapshot changes, never when an edit or
		// a completed write releases its flag against an older snapshot.
		untrack(() => {
			const previous = preferencesBaseline;
			const current = currentPreferences();
			if (!previous) {
				applyPreferences(incoming);
			} else if (!preferencesSaving) {
				if (current.vehicleName === previous.vehicleName && current.vehicleName !== incoming.vehicleName) vehicleName = incoming.vehicleName;
				if (current.model === previous.model && current.model !== incoming.model) model = incoming.model;
				if (current.vehicleColor === previous.vehicleColor && current.vehicleColor !== incoming.vehicleColor) vehicleColor = incoming.vehicleColor;
				if (current.homeLabel === previous.homeLabel && current.homeLabel !== incoming.homeLabel) homeLabel = incoming.homeLabel;
				// Method and rate form one editable choice: never hide a pending rate.
				const pricingDirty = current.pricingMethod !== previous.pricingMethod || (current.pricingMethod === 'manual' && current.tariff !== previous.tariff);
				if (!pricingDirty) {
					if (current.pricingMethod !== incoming.pricingMethod) pricingMethod = incoming.pricingMethod;
					if (current.tariff !== incoming.tariff) tariff = incoming.tariff;
				}
			}
			preferencesBaseline = incoming;
		});
	});

	async function loadWidgetProfiles() {
		const request = ++widgetProfileRequest;
		if (!online) {
			widgetProfilesLoading = false;
			widgetProfilesError = widgetProfilesLoaded
				? 'Sin conexión. Los perfiles mostrados pertenecen a esta sesión y pueden estar desactualizados.'
				: 'Sin conexión. Conéctate para cargar los perfiles guardados en el servidor.';
			return;
		}
		widgetProfilesLoading = true;
		widgetProfilesError = '';
		try {
			const profiles = await getWidgetProfiles();
			if (request !== widgetProfileRequest || settingsDisposed) return;
			const firstLoad = !widgetProfilesLoaded;
			if (widgetPairingResult && profiles.find((profile) => profile.id === widgetPairingProfileId)?.revision !== widgetPairingProfileRevision) clearWidgetPairing();
			widgetProfiles = profiles;
			widgetProfilesLoaded = true;
			if (!profiles.some((profile) => profile.id === widgetProfileSelectedId)) {
				widgetProfileSelectedId = profiles[0]?.id ?? null;
			}
			if (firstLoad && profiles.length === 0) openNewWidgetProfile();
		} catch (error) {
			if (request !== widgetProfileRequest || settingsDisposed) return;
			widgetProfilesError = error instanceof ApiError
				? error.status === 0 ? 'No se pudo contactar con el servidor. Comprueba la conexión e inténtalo de nuevo.' : error.message
				: 'No se pudieron cargar los perfiles de widget.';
		} finally {
			if (!settingsDisposed && request === widgetProfileRequest) widgetProfilesLoading = false;
		}
	}

	function invalidateNativeCapabilities() {
		nativeCapabilitiesRequest += 1;
		nativeCapabilitiesController?.abort();
		nativeCapabilitiesController = null;
		nativeCapabilitiesLoading = false;
	}

	async function loadNativeCapabilities(expectedRevision = revision) {
		if (!online || settingsDisposed) return;
		invalidateNativeCapabilities();
		const request = ++nativeCapabilitiesRequest;
		const controller = new AbortController();
		nativeCapabilitiesController = controller;
		nativeCapabilitiesLoading = true;
		nativeCapabilitiesError = '';
		try {
			const capabilities = await getNativeCapabilities(controller.signal);
			if (request !== nativeCapabilitiesRequest || controller.signal.aborted || settingsDisposed || !online || revision !== expectedRevision) return;
			nativeCapabilities = capabilities;
			if (!capabilities.enabled) clearWidgetPairing();
		} catch {
			if (request !== nativeCapabilitiesRequest || controller.signal.aborted || settingsDisposed || revision !== expectedRevision) return;
			nativeCapabilities = null;
			nativeCapabilitiesError = 'No se pudo consultar la disponibilidad del enlace.';
		} finally {
			if (request === nativeCapabilitiesRequest) {
				nativeCapabilitiesLoading = false;
				nativeCapabilitiesController = null;
			}
		}
	}

	function invalidateWidgetGrants() {
		widgetGrantsRequest += 1;
		widgetGrantsController?.abort();
		widgetGrantsController = null;
		widgetGrantsLoading = false;
		widgetGrantsLoadingProfileId = null;
	}

	async function loadWidgetGrants(profileId: string, expectedRevision = revision) {
		if (!online || settingsDisposed || selectedWidgetProfileId !== profileId) return;
		invalidateWidgetGrants();
		const request = ++widgetGrantsRequest;
		const controller = new AbortController();
		widgetGrantsController = controller;
		widgetGrantsLoading = true;
		widgetGrantsLoadingProfileId = profileId;
		widgetGrantsError = '';
		widgetGrantsErrorProfileId = null;
		try {
			const result = await getWidgetGrants(profileId, controller.signal);
			if (request !== widgetGrantsRequest || controller.signal.aborted || settingsDisposed || !online || selectedWidgetProfileId !== profileId || revision !== expectedRevision) return;
			const knownGrantIds = new Set(widgetGrantProfileId === profileId ? widgetGrants.map((grant) => grant.id) : []);
			const newGrantAppeared = result.items.some((grant) => grant.profile_id === profileId && !knownGrantIds.has(grant.id));
			widgetGrants = result.items.filter((grant) => grant.profile_id === profileId);
			widgetGrantProfileId = profileId;
			widgetGrantsLoaded = true;
			if (newGrantAppeared && widgetPairingProfileId === profileId && widgetPairingResult) clearWidgetPairing();
		} catch (error) {
			if (request !== widgetGrantsRequest || controller.signal.aborted || settingsDisposed || selectedWidgetProfileId !== profileId || revision !== expectedRevision) return;
			widgetGrantsError = error instanceof ApiError && error.status === 0
				? 'No se pudo cargar la lista de dispositivos. Comprueba la conexión e inténtalo de nuevo.'
				: 'No se pudo cargar la lista de dispositivos.';
			widgetGrantsErrorProfileId = profileId;
		} finally {
			if (request === widgetGrantsRequest) {
				widgetGrantsLoading = false;
				widgetGrantsLoadingProfileId = null;
				widgetGrantsController = null;
			}
		}
	}

	function clearWidgetPairing() {
		widgetPairingRequest += 1;
		if (widgetPairingTimer) clearTimeout(widgetPairingTimer);
		widgetPairingTimer = undefined;
		widgetPairingBusy = false;
		widgetPairingResult = null;
		widgetPairingProfileRevision = null;
		widgetPairingProfileId = null;
		widgetPairingError = '';
		widgetPairingVisible = false;
		widgetPairingCopyState = 'idle';
	}

	async function startWidgetPairing() {
		const profileId = selectedWidgetProfileId;
		const profileRevision = selectedWidgetProfile?.revision;
		if (pairingDisabled || !profileId || !createWidgetPairing) return;
		clearWidgetPairing();
		const request = ++widgetPairingRequest;
		widgetPairingBusy = true;
		try {
			const result = await createWidgetPairing(profileId);
			if (request !== widgetPairingRequest || settingsDisposed || !online || selectedWidgetProfileId !== profileId || selectedWidgetProfile?.revision !== profileRevision) return;
			const expiresAt = Date.parse(result.expires_at);
			if (result.pairing_code.length !== 43 || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
				widgetPairingError = 'El servidor devolvió un código de enlace no válido o caducado. Inténtalo de nuevo.';
				return;
			}
			widgetPairingResult = result;
			widgetPairingProfileId = profileId;
			widgetPairingProfileRevision = profileRevision ?? null;
			widgetPairingVisible = false;
			widgetPairingTimer = setTimeout(() => {
				if (request !== widgetPairingRequest) return;
				clearWidgetPairing();
				widgetPairingError = 'El código ha caducado. Solicita uno nuevo para continuar.';
			}, Math.max(0, expiresAt - Date.now()));
		} catch (error) {
			if (request === widgetPairingRequest && !settingsDisposed && selectedWidgetProfileId === profileId) {
				widgetPairingError = error instanceof ApiError ? error.message : 'No se pudo crear el enlace. Comprueba la conexión e inténtalo de nuevo.';
			}
		} finally {
			if (request === widgetPairingRequest) widgetPairingBusy = false;
		}
	}

	async function copyWidgetPairingCode() {
		const currentPairing = widgetPairingResult;
		const code = currentPairing?.pairing_code;
		const request = widgetPairingRequest;
		if (!code || widgetPairingProfileId !== selectedWidgetProfileId) return;
		try {
			await navigator.clipboard.writeText(code);
			if (request === widgetPairingRequest && widgetPairingResult === currentPairing) widgetPairingCopyState = 'copied';
		} catch {
			if (request === widgetPairingRequest && widgetPairingResult === currentPairing) widgetPairingCopyState = 'error';
		}
	}

	async function revokeWidgetGrant(grant: WidgetGrant) {
		if (!online || busy || widgetProfileBusy || widgetPairingBusy || !grantsForSelectedProfile || grantsLoadingForSelectedProfile || grantsErrorForSelectedProfile || grant.profile_id !== selectedWidgetProfileId) return;
		const previousFocus = captureSettingsActionFocus(widgetSection);
		widgetProfileBusy = true;
		const errorMessage = `No se ha podido confirmar la revocación de «${grant.device_name}». Revisa los dispositivos enlazados antes de reintentarlo.`;
		let revoked = false;
		try {
			const accepted = await confirmAction(
				'Revocar dispositivo enlazado',
				'La siguiente consulta quedará bloqueada; el widget sin conexión ocultará los datos al vencer su lectura.',
				'Revocar dispositivo'
			);
			if (!accepted || !online || busy || settingsDisposed || widgetPairingBusy || !grantsForSelectedProfile || grantsLoadingForSelectedProfile || grantsErrorForSelectedProfile || grant.profile_id !== selectedWidgetProfileId || !displayedWidgetGrants.some((item) => item.id === grant.id)) return;
			widgetActionError = '';
			revoked = await mutate(
				`/api/widget-profiles/${encodeURIComponent(grant.profile_id)}/native-grants/${encodeURIComponent(grant.id)}`,
				'DELETE', {}, 'El dispositivo se ha revocado.'
			);
			if (!settingsDisposed && !revoked) widgetActionError = errorMessage;
		} catch {
			if (!settingsDisposed) widgetActionError = errorMessage;
		} finally {
			if (!settingsDisposed) {
				widgetProfileBusy = false;
				await restoreSettingsActionFocus(previousFocus, widgetProfilesHeading, revoked, 'data');
			}
		}
	}

	function captureSettingsActionFocus(section: HTMLElement | undefined): HTMLElement | null {
		const active = document.activeElement;
		return section && active instanceof HTMLElement && section.contains(active) ? active : null;
	}

	async function restoreSettingsActionFocus(previous: HTMLElement | null, fallback: HTMLElement | undefined, preferFallback = false, section?: SettingsSection) {
		await tick();
		if (!previous || settingsDisposed || !document.hasFocus() || document.activeElement !== document.body) return;
		// A confirmed removal can reach the list after this tick. Its trigger is not a stable focus target.
		const target = !preferFallback && previous.isConnected && !previous.matches(':disabled') ? previous : fallback;
		if (!target?.isConnected || target.matches(':disabled')) return;
		if (section) {
			selectSettingsSection(section);
			await tick();
		}
		if (!settingsDisposed && document.hasFocus() && document.activeElement === document.body && target.isConnected && !target.matches(':disabled')) target.focus();
	}

	function selectWidgetProfile(profileId: string) {
		if (widgetProfileSelectedId === profileId) return;
		clearWidgetPairing();
		widgetProfileSelectedId = profileId;
	}

	onDestroy(() => {
		settingsDisposed = true;
		invalidateWidgetEnergy();
		invalidateNativeCapabilities();
		invalidateWidgetGrants();
		clearWidgetPairing();
	});

	async function focusWidgetProfileName(request: number, trigger: HTMLElement | null) {
		await tick();
		if (settingsDisposed || !widgetProfileFormOpen || request !== widgetProfileSwitchRequest) return;
		const active = document.activeElement;
		if (active !== document.body && active !== trigger) return;
		selectSettingsSection('data');
		await tick();
		if (settingsDisposed || !widgetProfileFormOpen || request !== widgetProfileSwitchRequest) return;
		document.getElementById('widget-profile-name')?.focus();
	}

	async function returnWidgetProfileFocus(request: number, previousFocus: Element | null) {
		await tick();
		if (settingsDisposed || widgetProfileFormOpen || request !== widgetProfileSwitchRequest) return;
		const active = document.activeElement;
		if (active !== document.body && active !== previousFocus) return;
		selectSettingsSection('data');
		await tick();
		if (settingsDisposed || widgetProfileFormOpen || request !== widgetProfileSwitchRequest) return;
		widgetProfilesHeading?.focus();
	}

	async function openNewWidgetProfile(event?: MouseEvent) {
		const request = ++widgetProfileSwitchRequest;
		const trigger = event ? event.currentTarget as HTMLElement : null;
		if (settingsDisposed || busy || widgetProfileBusy) return;
		if (widgetProfileFormOpen && widgetProfileEditingId === null) return;
		if (widgetProfileFormOpen && isWidgetProfileDirty()) {
			const accepted = await confirmAction('Descartar cambios del perfil', 'Se perderán los cambios sin guardar del perfil actual.', 'Descartar cambios');
			if (!accepted || settingsDisposed || busy || widgetProfileBusy || request !== widgetProfileSwitchRequest) return;
		}
		if (settingsDisposed || busy || widgetProfileBusy || request !== widgetProfileSwitchRequest || !widgetProfilesLoaded || widgetProfiles.length >= 5 || !online) return;
		widgetProfileEditingId = null;
		const baseline = widgetProfileSnapshot(newWidgetProfileDraft());
		widgetProfileBaseline = baseline;
		widgetProfileDraft = widgetProfileSnapshot(baseline);
		widgetProfileFormError = '';
		widgetProfileFormErrorField = null;
		widgetProfileFormOpen = true;
		if (event) await focusWidgetProfileName(request, trigger);
	}

	async function editWidgetProfile(profile: WidgetProfile, event?: MouseEvent) {
		const request = ++widgetProfileSwitchRequest;
		const trigger = event ? event.currentTarget as HTMLElement : null;
		if (settingsDisposed || busy || widgetProfileBusy) return;
		if (widgetProfileFormOpen && widgetProfileEditingId === profile.id && isWidgetProfileDirty()) return;
		if (widgetProfileFormOpen && isWidgetProfileDirty()) {
			const accepted = await confirmAction('Descartar cambios del perfil', 'Se perderán los cambios sin guardar del perfil actual.', 'Descartar cambios');
			if (!accepted || settingsDisposed || busy || widgetProfileBusy || request !== widgetProfileSwitchRequest) return;
		}
		if (settingsDisposed || busy || widgetProfileBusy || request !== widgetProfileSwitchRequest || !online) return;
		const currentProfile = widgetProfiles.find((item) => item.id === profile.id);
		if (!currentProfile) return;
		widgetProfileEditingId = currentProfile.id;
		selectWidgetProfile(currentProfile.id);
		const baseline: WidgetProfileInput = {
			name: currentProfile.name,
			fields: [...currentProfile.fields],
			appearance: currentProfile.appearance,
			privacy_mode: currentProfile.privacy_mode,
			max_age_minutes: currentProfile.max_age_minutes
		};
		widgetProfileBaseline = baseline;
		widgetProfileDraft = widgetProfileSnapshot(baseline);
		widgetProfileFormError = '';
		widgetProfileFormErrorField = null;
		widgetProfileFormOpen = true;
		if (event) await focusWidgetProfileName(request, trigger);
	}

	function cancelWidgetProfileEdit(event?: MouseEvent) {
		const focusOrigin = event ? event.currentTarget as HTMLElement : document.activeElement;
		const shouldReturnFocus = Boolean(event) || document.activeElement === document.body || Boolean(widgetProfileForm?.contains(document.activeElement));
		const request = ++widgetProfileSwitchRequest;
		widgetProfileFormOpen = false;
		widgetProfileEditingId = null;
		widgetProfileFormError = '';
		widgetProfileFormErrorField = null;
		widgetProfileBaseline = null;
		if (shouldReturnFocus) void returnWidgetProfileFocus(request, focusOrigin);
	}

	async function showWidgetProfileError(field: 'name' | 'fields' | 'age', message: string) {
		widgetProfileFormError = message;
		widgetProfileFormErrorField = field;
		await tick();
		if (settingsDisposed) return;
		selectSettingsSection('data');
		await tick();
		if (settingsDisposed) return;
		const targetId = field === 'name'
			? 'widget-profile-name'
			: field === 'age'
				? 'widget-age'
				: `widget-profile-field-${widgetAvailableFields[0]}`;
		document.getElementById(targetId)?.focus();
	}

	function setWidgetField(field: WidgetProfileField, enabled: boolean) {
		if (widgetProfileFormErrorField === 'fields') {
			widgetProfileFormError = '';
			widgetProfileFormErrorField = null;
		}
		const fields = new Set(widgetProfileDraft.fields);
		if (enabled) { if (fields.size >= 3) return; fields.add(field); }
		else fields.delete(field);
		widgetProfileDraft.fields = [...fields];
	}

	function applyWidgetTemplate(fields: WidgetProfileField[]) {
		widgetProfileDraft.fields = [...fields];
		widgetProfileFormError = '';
		widgetProfileFormErrorField = null;
	}

	function fieldsLabel(fields: WidgetProfileField[]): string {
		return fields.map((field) => widgetFieldLabels[field]).join(' · ');
	}

	function widgetProfileSummary(profile: WidgetProfile): string {
		const privacy = profile.privacy_mode === 'masked' ? 'Valores ocultos' : 'Valores visibles';
		const appearance = profile.appearance === 'system' ? 'Sistema' : profile.appearance === 'light' ? 'Clara' : 'Oscura';
		return `${fieldsLabel(profile.fields) || 'Sin datos'} · ${privacy} · ${appearance} · máx. ${profile.max_age_minutes} min`;
	}

	async function saveWidgetProfile(event: SubmitEvent) {
		event.preventDefault();
		widgetProfileSwitchRequest += 1;
		const request = widgetProfileSwitchRequest;
		widgetProfileFormError = '';
		widgetProfileFormErrorField = null;
		const name = widgetProfileDraft.name.trim();
		const maxAge = Number(widgetProfileDraft.max_age_minutes);
		if (!name || name.length > 60) {
			await showWidgetProfileError('name', 'Escribe un nombre de hasta 60 caracteres.');
			return;
		}
		if (!widgetProfileDraft.fields.length || widgetProfileDraft.fields.length > 3) {
			await showWidgetProfileError('fields', 'Selecciona de uno a tres datos para el widget.');
			return;
		}
		if (!Number.isInteger(maxAge) || maxAge < 15 || maxAge > 120) {
			await showWidgetProfileError('age', 'La antigüedad máxima debe estar entre 15 y 120 minutos.');
			return;
		}
		if (!online) {
			widgetProfileFormError = 'Conéctate para guardar este perfil en el servidor.';
			return;
		}
		if (busy || widgetProfileBusy) return;
		const formAtSubmit = widgetProfileForm;
		const focusOrigin = document.activeElement as HTMLElement | null;
		const focusWasInForm = Boolean(formAtSubmit?.contains(focusOrigin));
		widgetProfileBusy = true;
		const payload: WidgetProfileInput = { ...widgetProfileDraft, name, max_age_minutes: maxAge, fields: [...widgetProfileDraft.fields] };
		const editingId = widgetProfileEditingId;
		let saved = false;
		try {
			saved = await mutate(
				editingId ? `/api/widget-profiles/${encodeURIComponent(editingId)}` : '/api/widget-profiles',
				editingId ? 'PUT' : 'POST',
				payload,
				editingId ? 'El perfil del widget se ha actualizado.' : 'El perfil del widget se ha creado.'
			);
		} catch {
			// Keep the draft if the caller cannot confirm the write.
		} finally {
			if (!settingsDisposed) widgetProfileBusy = false;
		}
		if (settingsDisposed) return;
		const sameEditor = request === widgetProfileSwitchRequest
			&& widgetProfileFormOpen
			&& widgetProfileForm === formAtSubmit
			&& widgetProfileEditingId === editingId;
		const activeAtResponse = document.activeElement;
		const shouldReturnFocus = focusWasInForm
			&& (activeAtResponse === document.body || Boolean(formAtSubmit?.contains(activeAtResponse)));
		if (saved && editingId === widgetPairingProfileId) clearWidgetPairing();
		if (!sameEditor) return;
		if (saved) {
			widgetProfileFormOpen = false;
			widgetProfileEditingId = null;
			widgetProfileFormError = '';
			widgetProfileFormErrorField = null;
			widgetProfileBaseline = null;
			if (shouldReturnFocus) void returnWidgetProfileFocus(request, focusOrigin);
			return;
		}
		widgetProfileFormError = 'No se ha podido confirmar el guardado del perfil. Revisa los perfiles guardados antes de reintentarlo.';
		if (!focusWasInForm || activeAtResponse !== document.body) return;
		await tick();
		if (
			settingsDisposed
			|| request !== widgetProfileSwitchRequest
			|| !widgetProfileFormOpen
			|| widgetProfileForm !== formAtSubmit
			|| widgetProfileEditingId !== editingId
			|| !document.hasFocus()
			|| document.activeElement !== document.body
		) return;
		const namedTarget = document.getElementById('widget-profile-name');
		const focusTarget = focusOrigin && formAtSubmit?.contains(focusOrigin) && !focusOrigin.matches(':disabled')
			? focusOrigin
			: namedTarget && !namedTarget.matches(':disabled') ? namedTarget : null;
		if (focusTarget) {
			selectSettingsSection('data');
			await tick();
			if (!settingsDisposed && document.hasFocus() && document.activeElement === document.body && focusTarget.isConnected) focusTarget.focus();
		}
	}

	async function deleteWidgetProfile(profile: WidgetProfile) {
		if (!online || busy || widgetProfileBusy || widgetPairingBusy) return;
		const previousFocus = captureSettingsActionFocus(widgetSection);
		widgetProfileBusy = true;
		const errorMessage = `No se ha podido confirmar la eliminación de «${profile.name}». Revisa los perfiles guardados antes de reintentarlo.`;
		let deleted = false;
		try {
			const accepted = await confirmAction(
				'Eliminar perfil de widget',
				`Se eliminará «${profile.name}» del servidor. Esta acción no se puede deshacer.`,
				'Eliminar perfil'
			);
			if (!accepted || settingsDisposed || !online || busy || widgetPairingBusy || !widgetProfiles.some((item) => item.id === profile.id && item.revision === profile.revision)) return;
			widgetActionError = '';
			deleted = await mutate(
				`/api/widget-profiles/${encodeURIComponent(profile.id)}`,
				'DELETE', {}, 'El perfil del widget se ha eliminado.'
			);
			if (settingsDisposed) return;
			if (!deleted) widgetActionError = errorMessage;
			if (deleted && widgetProfileEditingId === profile.id) cancelWidgetProfileEdit();
		} catch {
			if (!settingsDisposed) widgetActionError = errorMessage;
		} finally {
			if (!settingsDisposed) {
				widgetProfileBusy = false;
		await restoreSettingsActionFocus(previousFocus, widgetProfilesHeading, deleted, 'data');
			}
		}
	}

	async function saveSettings(event: SubmitEvent) {
		event.preventDefault();
		if (preferencesSaving || busy) return;
		const form = event.currentTarget as HTMLFormElement;
		const previousFocus = document.activeElement instanceof HTMLElement && form.contains(document.activeElement)
			? document.activeElement : null;
		settingsError = '';
		settingsErrorField = null;
		const submittedDraft = currentPreferences();
		const cleanVehicle = vehicleName.trim();
		const cleanModel = model.trim();
		const cleanHome = homeLabel.trim();
		const rate = Number(tariff);
		if (!cleanVehicle) {
			await showSettingsError('vehicleName', 'Escribe un nombre para el vehículo.');
			return;
		}
		if (!cleanModel) {
			await showSettingsError('model', 'Escribe el modelo o acabado del vehículo.');
			return;
		}
		if (!cleanHome) {
			await showSettingsError('homeLabel', 'Escribe el punto de carga habitual.');
			return;
		}
		if (pricingMethod === 'manual' && (!Number.isFinite(rate) || rate < 0 || rate > 5)) {
			await showSettingsError('tariff', 'La tarifa debe estar entre 0 y 5 €/kWh.');
			return;
		}
		const payload = {
			vehicle_name: cleanVehicle,
			model: cleanModel,
			vehicle_color: submittedDraft.vehicleColor,
			home_label: cleanHome,
			pricing_method: pricingMethod,
			...(pricingMethod === 'manual' ? { tariff_eur_kwh: rate } : {})
		};
		const savedDraft: PreferencesDraft = {
			vehicleName: cleanVehicle,
			model: cleanModel,
			vehicleColor: submittedDraft.vehicleColor,
			homeLabel: cleanHome,
			tariff: submittedDraft.pricingMethod === 'manual' ? String(rate) : submittedDraft.tariff,
			pricingMethod: submittedDraft.pricingMethod
		};
		preferencesSaving = true;
		try {
			const saved = await mutate('/api/settings', 'PUT', payload, 'Tus ajustes se han guardado.');
			if (settingsDisposed) return;
			if (!saved) {
				settingsError = 'No se ha podido confirmar el guardado de los ajustes. Tus cambios siguen en el formulario.';
				return;
			}
			preferencesBaseline = savedDraft;
			if (samePreferences(currentPreferences(), submittedDraft)) applyPreferences(savedDraft);
		} catch {
			if (!settingsDisposed) settingsError = 'No se ha podido confirmar el guardado de los ajustes. Tus cambios siguen en el formulario.';
		} finally {
			if (!settingsDisposed) {
				preferencesSaving = false;
				await tick();
				if (!settingsDisposed && settingsError && document.hasFocus() && document.activeElement === document.body && previousFocus?.isConnected) {
					selectSettingsSection('vehicle');
					await tick();
					if (!settingsDisposed && document.hasFocus() && document.activeElement === document.body && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
				}
			}
		}
	}

	async function showSettingsError(field: 'vehicleName' | 'model' | 'homeLabel' | 'tariff', message: string) {
		settingsError = message;
		settingsErrorField = field;
		await tick();
		if (!settingsDisposed) {
			selectSettingsSection('vehicle');
			await tick();
			if (!settingsDisposed) document.getElementById(field === 'vehicleName' ? 'vehicle-name' : field === 'model' ? 'vehicle-model' : field === 'homeLabel' ? 'home-label' : 'tariff')?.focus();
		}
	}

	function clearSettingsError(field: 'vehicleName' | 'model' | 'homeLabel' | 'tariff') {
		if (settingsErrorField !== field) return;
		settingsError = '';
		settingsErrorField = null;
	}

	async function toggleDemo() {
		if (!online || demoToggleBusy || busy) return;
		const previousFocus = captureSettingsActionFocus(connectionSection);
		const enabling = pulseState.mode !== 'demo';
		demoToggleBusy = true;
		try {
			const accepted = await confirmAction(
				enabling ? 'Activar la demostración' : 'Salir de la demostración',
				enabling ? 'Pulse añadirá datos ficticios y habilitará acciones simuladas. La conexión física seguirá pendiente.' : 'Pulse eliminará los datos ficticios de la demo. Tus cargas y tareas manuales se conservarán.',
				enabling ? 'Activar demo' : 'Salir de demo'
			);
			if (!accepted || settingsDisposed || !online || busy || (pulseState.mode === 'demo') === enabling) return;
			demoToggleError = '';
			const saved = await mutate('/api/demo', 'POST', { enabled: enabling }, enabling ? 'La demostración está activa.' : 'La demo se ha desactivado.');
			if (!settingsDisposed && !saved) demoToggleError = 'No se ha podido confirmar el cambio de modo. Revisa si Pulse está en demo antes de reintentarlo.';
		} catch {
			if (!settingsDisposed) demoToggleError = 'No se ha podido confirmar el cambio de modo. Revisa si Pulse está en demo antes de reintentarlo.';
		} finally {
			if (!settingsDisposed) {
				demoToggleBusy = false;
				await restoreSettingsActionFocus(previousFocus, connectionHeading, false, 'connections');
			}
		}
	}

	function openImportPicker() {
		if (busy || backupBusy || !fileInput) return;
		importPickerRequest += 1;
		fileInput.click();
	}

	async function returnImportFocus(request: number) {
		await tick();
		if (settingsDisposed || request !== importPickerRequest || !document.hasFocus()) return;
		const button = importPickerButton;
		if (!button?.isConnected || button.disabled) return;
		const active = document.activeElement;
		const activeInClosedDialog = Boolean(active?.closest('dialog:not([open])'));
		if (active !== document.body && active !== fileInput && active !== button && !activeInClosedDialog) return;
		selectSettingsSection('data');
		await tick();
		if (settingsDisposed || request !== importPickerRequest || !document.hasFocus() || !button.isConnected || button.disabled) return;
		button.focus();
	}

	function cancelImportPicker() {
		const request = importPickerRequest;
		void returnImportFocus(request);
	}

	async function chooseImport(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		const request = importPickerRequest;
		if (!file || backupBusy) {
			input.value = '';
			await returnImportFocus(request);
			return;
		}
		backupBusy = true;
		backupError = '';
		try {
			const result = await onImport(file);
			if (!settingsDisposed && result.status === 'error') backupError = result.message;
		} catch {
			if (!settingsDisposed) backupError = 'No se ha podido confirmar la restauración. Revisa los datos actuales antes de reintentarlo.';
		} finally {
			input.value = '';
			if (!settingsDisposed) backupBusy = false;
			await returnImportFocus(request);
		}
	}

	async function downloadBackup() {
		if (busy || backupBusy) return;
		const previousFocus = document.activeElement as HTMLElement | null;
		backupBusy = true;
		backupError = '';
		try {
			const result = await onExport();
			if (!settingsDisposed && result.status === 'error') backupError = result.message;
		} catch {
			if (!settingsDisposed) backupError = 'No se pudo crear la copia. Revisa la conexión con Pulse antes de reintentarlo.';
		} finally {
			if (!settingsDisposed) {
				backupBusy = false;
				await tick();
				if (!settingsDisposed && document.hasFocus() && document.activeElement === document.body && previousFocus?.isConnected) {
					selectSettingsSection('data');
					await tick();
					if (!settingsDisposed && document.hasFocus() && document.activeElement === document.body && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
				}
			}
		}
	}

</script>

<section class="page-intro premium-heading settings-intro">
	<div>
		<h2>Tu perfil</h2>
		<p>Vehículo, conexiones y preferencias de Pulse.</p>
	</div>
	<span class="status-pill"><span class="status-dot"></span>{user || 'Cuenta personal'}</span>
</section>

<section class="settings-profile" aria-label="Configuración del perfil">
	<div class="section-switcher settings-section-switcher" role="group" aria-label="Secciones del perfil">
		<button type="button" aria-pressed={activeSettingsSection === 'vehicle'} aria-controls="settings-panel-vehicle" onclick={() => selectSettingsSection('vehicle')}>Vehículo</button>
		<button type="button" aria-pressed={activeSettingsSection === 'connections'} aria-controls="settings-panel-connections" onclick={() => selectSettingsSection('connections')}>Conexiones</button>
		<button type="button" aria-pressed={activeSettingsSection === 'preferences'} aria-controls="settings-panel-preferences" onclick={() => selectSettingsSection('preferences')}>Preferencias</button>
		<button type="button" aria-pressed={activeSettingsSection === 'data'} aria-controls="settings-panel-data" onclick={() => selectSettingsSection('data')}>Datos</button>
	</div>

	<section id="settings-panel-vehicle" class="workspace-panel settings-section-panel" aria-labelledby="settings-panel-vehicle-heading" hidden={activeSettingsSection !== 'vehicle'}>
		<h2 id="settings-panel-vehicle-heading" class="sr-only">Vehículo</h2>
		<form class="settings-form vehicle-settings-form" onsubmit={saveSettings}>
			<div class="vehicle-profile-header">
				<div class="vehicle-profile-art"><VehicleArtwork color={vehicleColor} /></div>
				<div class="vehicle-identity-fields">
					<div class="field"><label for="vehicle-name">Nombre del vehículo</label><input id="vehicle-name" maxlength="80" bind:value={vehicleName} oninput={() => clearSettingsError('vehicleName')} required aria-invalid={settingsErrorField === 'vehicleName' ? 'true' : undefined} aria-describedby={settingsErrorField === 'vehicleName' ? 'settings-vehicle-name-error' : undefined} />{#if settingsError && settingsErrorField === 'vehicleName'}<p id="settings-vehicle-name-error" class="form-error" role="alert">{settingsError}</p>{/if}</div>
					<div class="field"><label for="vehicle-model">Modelo o acabado</label><input id="vehicle-model" list="vehicle-models" maxlength="80" bind:value={model} oninput={() => clearSettingsError('model')} required aria-invalid={settingsErrorField === 'model' ? 'true' : undefined} aria-describedby={settingsErrorField === 'model' ? 'settings-model-error' : undefined} /><datalist id="vehicle-models"><option value="Leapmotor B10 Pro Max"></option><option value="Leapmotor B10 Pro"></option></datalist><p class="field-hint">Elige o escribe tu modelo y acabado. Pulse no lo deduce.</p>{#if settingsError && settingsErrorField === 'model'}<p id="settings-model-error" class="form-error" role="alert">{settingsError}</p>{/if}</div>
				</div>
			</div>
			<fieldset class="vehicle-color-field" aria-describedby="vehicle-color-hint">
				<legend>Color del B10</legend>
				<p id="vehicle-color-hint" class="field-hint">Aspecto orientativo en Pulse; no cambia el vehículo.</p>
				<div class="vehicle-color-options">
					{#each vehicleColors as option (option.id)}
						<label class="vehicle-color-option" class:selected={vehicleColor === option.id}>
							<input class="sr-only" type="radio" name="vehicle-color" value={option.id} bind:group={vehicleColor} />
							<span class="paint-swatch" style:background={option.swatch} aria-hidden="true"></span><span>{option.label}</span>
							{#if vehicleColor === option.id}<Glyph name="check" size={16} />{/if}
						</label>
					{/each}
					<label class="vehicle-color-option" class:selected={vehicleColor === null}><input class="sr-only" type="radio" name="vehicle-color" value={null} bind:group={vehicleColor} /><span>Sin elegir</span>{#if vehicleColor === null}<Glyph name="check" size={16} />{/if}</label>
				</div>
			</fieldset>
			<div class="form-grid vehicle-configuration-grid">
					<div class="field"><label for="home-label">Punto de carga habitual</label><input id="home-label" maxlength="80" bind:value={homeLabel} oninput={() => clearSettingsError('homeLabel')} required aria-invalid={settingsErrorField === 'homeLabel' ? 'true' : undefined} aria-describedby={settingsErrorField === 'homeLabel' ? 'settings-home-label-error' : undefined} />{#if settingsError && settingsErrorField === 'homeLabel'}<p id="settings-home-label-error" class="form-error" role="alert">{settingsError}</p>{/if}</div>
					<div class="field"><label for="pricing-method">Método de cálculo</label><select id="pricing-method" value={pricingMethod} onchange={(event) => { const value = event.currentTarget.value; if (value === 'manual' || value === 'pvpc') pricingMethod = value; clearSettingsError('tariff'); }}><option value="manual">Precio fijo manual</option><option value="pvpc">PVPC automático · Península</option></select><p class="field-hint">El método se aplica a las nuevas cargas; las anteriores conservan su cálculo.</p></div>
					{#if pricingMethod === 'manual'}
						<div class="field"><label for="tariff">Tarifa eléctrica fija</label><div class="input-with-unit"><input id="tariff" type="number" min="0" max="5" step="0.001" inputmode="decimal" bind:value={tariff} oninput={() => clearSettingsError('tariff')} required aria-invalid={settingsErrorField === 'tariff' ? 'true' : undefined} aria-describedby={settingsErrorField === 'tariff' ? 'settings-tariff-error' : undefined} /><span>€/kWh</span></div><p class="field-hint">Se aplica a las nuevas cargas manuales. Las cargas existentes conservan su coste.</p>{#if settingsError && settingsErrorField === 'tariff'}<p id="settings-tariff-error" class="form-error" role="alert">{settingsError}</p>{/if}</div>
					{:else}
						<div class="field pvpc-setting-note"><strong>PVPC automático</strong><p class="field-hint">Península · precio por hora de Red Eléctrica · energía sin impuestos ni potencia fija</p><p class="field-hint">Pulse consultará los precios oficiales al calcular cada sesión. Esta selección no cambia tus cargas anteriores.</p></div>
					{/if}
				</div>

				{#if settingsError && settingsErrorField === null}<p id="settings-general-error" class="form-error" role="alert">{settingsError}</p>{/if}
				<div class="form-actions"><button class="button button-primary" type="submit" disabled={busy}>{busy ? 'Guardando…' : 'Guardar ajustes'}</button>{#if pricingMethod === 'manual'}<span class="settings-current-rate">Tarifa fija actual: {euros(pulseState.settings.tariff_eur_kwh)} / kWh</span>{:else}<span class="settings-current-rate">Método seleccionado: PVPC automático · Península</span>{/if}</div>
		</form>
	</section>

	<section id="settings-panel-connections" class="workspace-panel settings-section-panel" aria-labelledby="settings-panel-connections-heading" hidden={activeSettingsSection !== 'connections'}>
		<h2 id="settings-panel-connections-heading" class="sr-only">Conexiones</h2>
				<section bind:this={connectionSection} class="settings-card" aria-labelledby="connection-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="plug" size={18} /></span><div><h2 bind:this={connectionHeading} id="connection-heading" tabindex="-1">Vehículo y servicios</h2><p>El estado procede de la API de Pulse.</p></div></div>
			<div class="connection-status-card">
				<span class="connection-status-mark"><Glyph name="plug" size={19} /></span>
				<div><strong>{connectionHeadline()}</strong><p>{connectionDetail()}</p></div>
				<span class="badge" class:badge-demo={pulseState.mode === 'demo'}>{connectionLabel()}</span>
			</div>
			{#if hasLatestTelemetry}
				<div class="notice telemetry-quality-note"><Glyph name="activity" size={17} /><span><strong>Frescura no cualificada.</strong> {telemetryQualityLabel()} {pulseState.telemetry?.quality.observed_at ? `Observado: ${dateTime(pulseState.telemetry.quality.observed_at)}.` : 'El servidor no informa una hora de observación.'}</span></div>
			{/if}
			<div class="notice connection-notice"><Glyph name="protection" size={17} /><span>{hasLatestTelemetry ? 'Lectura sin controles. La frescura física no está cualificada y los valores requieren validación. No hay un formulario para credenciales del fabricante.' : 'No hay un formulario para credenciales del fabricante. La integración dependerá de un proveedor autorizado y validado.'}</span></div>
			<div class="demo-setting-row">
				<div><strong>{pulseState.mode === 'demo' ? 'Salir de la demostración' : 'Explorar con datos ficticios'}</strong><span>{pulseState.mode === 'demo' ? 'Se eliminan los datos de demo; tus registros manuales siguen disponibles.' : 'La demo identifica todos sus datos y acciones como simulados.'}</span></div>
				<button class="button button-secondary button-small" type="button" disabled={!online || busy || demoToggleBusy} onclick={toggleDemo}>{demoToggleBusy ? 'Un momento…' : pulseState.mode === 'demo' ? 'Salir de demo' : 'Activar demo'}</button>
			</div>
			{#if demoToggleError}<p class="form-error" role="alert">{demoToggleError}</p>{/if}
		</section>

		<SetupPanel {pulseState} {online} expanded={settingsSection === 'preparation'} />

		<section class="settings-card" aria-labelledby="home-integration-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="overview" size={18} /></span><div><h2 id="home-integration-heading">Home Assistant</h2><p>Consulta las lecturas de Pulse desde tu sistema de casa.</p></div></div>
			<HomeIntegrationPanel mqtt={pulseState.integrations?.mqtt} {online} />
		</section>

		<section class="settings-card" aria-labelledby="abrp-integration-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="navigation" size={18} /></span><div><h2 id="abrp-integration-heading">ABRP</h2><p>Comparte solo una lectura válida de batería y su hora de observación.</p></div></div>
			<AbrpIntegrationPanel abrp={pulseState.integrations?.abrp} mode={pulseState.mode} {online} {busy} {mutate} {confirmAction} />
		</section>
	</section>

	<section id="settings-panel-preferences" class="workspace-panel settings-section-panel" aria-labelledby="settings-panel-preferences-heading" hidden={activeSettingsSection !== 'preferences'}>
		<h2 id="settings-panel-preferences-heading" class="sr-only">Preferencias</h2>
				<section class="settings-card" aria-labelledby="appearance-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="sun" size={18} /></span><div><h2 id="appearance-heading">Apariencia</h2></div></div>
			<fieldset class="theme-field theme-choices" aria-describedby="theme-hint">
				<legend class="sr-only">Tema de la interfaz</legend>
				<div class="theme-options">
					{#each [{ id: 'light', label: 'Claro' }, { id: 'dark', label: 'Oscuro' }, { id: 'system', label: 'Sistema' }] as option (option.id)}
						<button class="theme-option" class:selected={theme === option.id} type="button" aria-pressed={theme === option.id} onclick={() => setTheme(option.id as ThemePreference)}>
							<span class="theme-sample" class:sample-dark={option.id === 'dark'} class:sample-system={option.id === 'system'} aria-hidden="true"><span class="sample-rail"></span><span class="sample-content"><i></i><i></i><i></i></span></span>
							<span class="theme-option-label">{option.label}<span class="theme-check" aria-hidden="true">{#if theme === option.id}<Glyph name="check" size={14} />{/if}</span></span>
						</button>
					{/each}
				</div>
				<p id="theme-hint" class="field-hint">Sistema acompaña la apariencia de tu dispositivo. La preferencia se guarda en este navegador.</p>
			</fieldset>
		</section>

		{#if !isNativeApp()}
		<section class="settings-card" aria-labelledby="pwa-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="overview" size={18} /></span><div><h2 id="pwa-heading">Instala Pulse</h2></div></div>
			<p class="muted-copy pwa-intro">La PWA puede abrirse como una aplicación y mostrar una pantalla genérica sin conexión. Los datos siguen en el servidor.</p>
			{#if canInstall}<button class="button button-primary button-block" type="button" onclick={onInstall}>Instalar Pulse</button>{/if}
			<div class="install-guide"><strong>iPhone o iPad</strong><span>En Safari, abre Compartir y elige «Añadir a pantalla de inicio».</span></div>
			<div class="install-guide"><strong>Android</strong><span>En el menú de Chrome, selecciona «Instalar aplicación» o «Añadir a pantalla de inicio».</span></div>
			<p class="fine-print">La disponibilidad de instalación depende del navegador y de la configuración del dispositivo.</p>
		</section>
		{/if}
				<PushNotificationsPanel {online} {revision} {busy} native={isNativeApp()} {notifications} {runNotificationAction} {confirmAction} {drafts} />
	</section>

	<section id="settings-panel-data" class="workspace-panel settings-section-panel" aria-labelledby="settings-panel-data-heading" hidden={activeSettingsSection !== 'data'}>
		<h2 id="settings-panel-data-heading" class="sr-only">Datos guardados</h2>
				<section class="settings-card" aria-labelledby="backup-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="download" size={18} /></span><div><h2 id="backup-heading">Exportar o restaurar</h2><p>Los archivos se procesan en esta sesión y se envían al servidor solo al restaurar.</p></div></div>
			<div class="backup-actions">
				<div><strong>Descargar una copia</strong><span>El servidor prepara un JSON versionado con tus datos de Pulse.</span></div>
				<button class="button button-secondary" type="button" disabled={busy || backupBusy} onclick={downloadBackup}><Glyph name="download" size={16} /> Exportar JSON</button>
			</div>
			<div class="backup-actions backup-restore">
				<div><strong>Restaurar una copia JSON</strong><span>Pulse valida el archivo y pide confirmación antes de sustituir los datos.</span></div>
				<input hidden bind:this={fileInput} id="backup-file" type="file" accept="application/json,.json" disabled={busy || backupBusy} onchange={chooseImport} oncancel={cancelImportPicker} />
				<button bind:this={importPickerButton} class="button button-secondary" type="button" disabled={busy || backupBusy} onclick={openImportPicker}><Glyph name="upload" size={16} /> Elegir archivo</button>
			</div>
			{#if backupBusy}<p class="fine-print" role="status">Procesando la copia…</p>{/if}
			{#if backupError}<p class="form-error" role="alert">{backupError}</p>{/if}
			<p class="fine-print">No cargues archivos que contengan claves, contraseñas o datos que no quieras importar. No es posible restaurar un archivo SQL.</p>
		</section>
				<section bind:this={widgetSection} class="settings-card" aria-labelledby="widgets-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="activity" size={18} /></span><div><h2 id="widgets-heading" tabindex="-1" bind:this={widgetProfilesHeading}>Perfiles de widget</h2><p>{isNativeApp() ? 'Prepara vistas compactas para el widget de esta app.' : 'Prepara vistas compactas para una futura extensión móvil.'}</p></div></div>
			<p class="muted-copy widget-intro">El editor guarda tus perfiles en el servidor. El widget consulta los campos que selecciones, sin conectar Pulse con el vehículo.</p>
			{#if widgetActionError}<p class="form-error" role="alert">{widgetActionError}</p>{/if}
			{#if isNativeApp()}<NativeWidgetPanel {online} {revision} {confirmAction} {drafts} />{/if}
			<div class="widget-count"><span>Perfiles guardados</span><strong>{widgetProfilesLoaded ? `${count(widgetProfiles.length)} / 5` : '— / 5'}</strong></div>
			{#if pulseState.mode === 'demo' && !widgetPreviewIsEnergy}<div class="widget-demo-note" role="status"><span class="widget-demo-dot"></span>Vista previa del coche con datos simulados.</div>{/if}
			{#if !online}<div class="notice widget-offline" role="status"><Glyph name="plug" size={16} /><span>{widgetProfilesLoaded ? 'Sin conexión. No podrás guardar cambios; los perfiles de esta sesión pueden estar desactualizados.' : 'Sin conexión. Conéctate para cargar los perfiles guardados en el servidor.'}</span></div>{/if}
			{#if widgetProfilesError}
				<div class="widget-load-error" role="alert"><span>{widgetProfilesError}</span><button class="button button-small button-secondary" type="button" disabled={!online || widgetProfilesLoading} onclick={() => void loadWidgetProfiles()}>{widgetProfilesLoading ? 'Cargando…' : online ? 'Reintentar' : 'Sin conexión'}</button></div>
			{/if}

			{#if widgetProfilesLoading && !widgetProfilesLoaded}
				<div class="widget-loading" role="status"><span class="loading-dot"></span> Cargando perfiles…</div>
			{:else if widgetProfilesLoaded}
				{#if widgetProfiles.length}
					<ul class="widget-profile-list" aria-label="Perfiles de widget guardados">
						{#each widgetProfiles as profile (profile.id)}
							<li class="widget-profile-row">
								<button class="widget-profile-choice" class:widget-profile-active={selectedWidgetProfile?.id === profile.id} type="button" aria-pressed={selectedWidgetProfile?.id === profile.id} onclick={() => selectWidgetProfile(profile.id)}>
									<strong>{profile.name}</strong><span>{widgetProfileSummary(profile)}</span>
								</button>
								<div class="widget-profile-actions"><button class="button button-small button-quiet" type="button" disabled={!online || busy || widgetProfileBusy} onclick={(event) => editWidgetProfile(profile, event)}>Editar</button><button class="button button-small button-quiet widget-delete" type="button" aria-label={`Eliminar perfil ${profile.name}`} disabled={!online || busy || widgetProfileBusy || widgetPairingBusy} onclick={() => void deleteWidgetProfile(profile)}>Eliminar</button></div>
							</li>
						{/each}
					</ul>
				{:else if !widgetProfileFormOpen}
					<div class="widget-empty-state"><strong>Aún no hay perfiles</strong><span>Crea una vista privada para consultar los datos que elijas.</span></div>
				{/if}

				{#if widgetProfileFormOpen}
					<form bind:this={widgetProfileForm} class="widget-profile-form" onsubmit={saveWidgetProfile}>
						<div class="widget-form-title"><div><h3>{widgetProfileEditingId ? 'Ajusta esta vista' : 'Diseña una vista compacta'}</h3></div><button class="button button-small button-quiet" type="button" onclick={cancelWidgetProfileEdit} aria-label="Cerrar editor">Cerrar</button></div>
						<div class="field"><label for="widget-profile-name">Nombre del perfil</label><input id="widget-profile-name" maxlength="60" autocomplete="off" bind:value={widgetProfileDraft.name} oninput={() => { if (widgetProfileFormErrorField === 'name') { widgetProfileFormError = ''; widgetProfileFormErrorField = null; } }} placeholder="Por ejemplo, Mi mañana" required disabled={busy || widgetProfileBusy} aria-invalid={widgetProfileFormErrorField === 'name' ? 'true' : undefined} aria-describedby={widgetProfileFormErrorField === 'name' ? 'widget-profile-name-error' : undefined} />{#if widgetProfileFormError && widgetProfileFormErrorField === 'name'}<p id="widget-profile-name-error" class="form-error" role="alert">{widgetProfileFormError}</p>{/if}</div>
						<fieldset class="widget-templates"><legend>Empieza con una plantilla</legend>
							{#each widgetTemplates as template (template.id)}<button type="button" aria-pressed={widgetDraftTemplate === template.id} disabled={busy || widgetProfileBusy} onclick={() => applyWidgetTemplate(template.fields)}><strong>{template.name}</strong><span>{template.detail}</span></button>{/each}
						</fieldset>
						<fieldset class="widget-fields"><legend>Elige de uno a tres datos</legend>
							{#each widgetAvailableFields as field (field)}<label><input id={`widget-profile-field-${field}`} type="checkbox" checked={widgetProfileDraft.fields.includes(field)} onchange={(event) => setWidgetField(field, event.currentTarget.checked)} disabled={busy || widgetProfileBusy || (!widgetProfileDraft.fields.includes(field) && widgetProfileDraft.fields.length >= 3) || (widgetProfileDraft.fields.includes(field) && widgetProfileDraft.fields.length === 1)} aria-invalid={widgetProfileFormErrorField === 'fields' ? 'true' : undefined} aria-describedby={widgetProfileFormErrorField === 'fields' ? 'widget-profile-fields-error' : undefined} /> {widgetFieldLabels[field]}</label>{/each}
							{#if widgetProfileFormError && widgetProfileFormErrorField === 'fields'}<p id="widget-profile-fields-error" class="form-error" role="alert">{widgetProfileFormError}</p>{/if}
						</fieldset>
						<div class="widget-preview-wrap"><span class="widget-preview-label">VISTA PREVIA</span><WidgetPreview profile={widgetProfileDraft} {pulseState} energy={widgetEnergy} energyLoading={widgetEnergyLoading} energyError={widgetEnergyError} {online} />
							{#if widgetDraftIsEnergy}<p class="widget-energy-note">Cargas guardadas de los últimos 30 días, sin demostración ni evcc. Cada registro conserva su coste fijo o estimación PVPC.</p>{/if}
							{#if widgetDraftIsEnergy && widgetEnergyError}<div class="widget-load-error" role="alert"><span>{widgetEnergyError}</span><button class="button button-secondary" type="button" disabled={!online || widgetEnergyLoading} onclick={() => void loadWidgetEnergy()}>Reintentar cálculo</button></div>{/if}
						</div>
						<div class="widget-form-grid">
							<div class="field"><label for="widget-appearance">Apariencia</label><select id="widget-appearance" bind:value={widgetProfileDraft.appearance} disabled={busy || widgetProfileBusy}><option value="system">Sistema</option><option value="light">Clara</option><option value="dark">Oscura</option></select></div>
							<div class="field"><label for="widget-privacy">Privacidad</label><select id="widget-privacy" bind:value={widgetProfileDraft.privacy_mode} disabled={busy || widgetProfileBusy}><option value="masked">Ocultar valores</option><option value="visible">Mostrar valores</option></select></div>
							<div class="field widget-age-field"><label for="widget-age">Antigüedad máxima</label><div class="input-with-unit"><input id="widget-age" type="number" min="15" max="120" step="1" inputmode="numeric" bind:value={widgetProfileDraft.max_age_minutes} oninput={() => { if (widgetProfileFormErrorField === 'age') { widgetProfileFormError = ''; widgetProfileFormErrorField = null; } }} required disabled={busy || widgetProfileBusy} aria-invalid={widgetProfileFormErrorField === 'age' ? 'true' : undefined} aria-describedby={widgetProfileFormErrorField === 'age' ? 'widget-profile-age-error' : undefined} /><span>min</span></div><p class="field-hint">{widgetDraftIsEnergy ? 'Caduca desde la fecha de cálculo del resumen.' : 'Caduca desde la fecha de observación del coche.'}</p>{#if widgetProfileFormError && widgetProfileFormErrorField === 'age'}<p id="widget-profile-age-error" class="form-error" role="alert">{widgetProfileFormError}</p>{/if}</div>
						</div>
						{#if widgetProfileFormError && widgetProfileFormErrorField === null}<p id="widget-profile-general-error" class="form-error" role="alert">{widgetProfileFormError}</p>{/if}
						<div class="widget-form-actions"><button class="button button-primary button-block" type="submit" disabled={!online || busy || widgetProfileBusy}>{widgetProfileBusy ? 'Guardando…' : widgetProfileEditingId ? 'Guardar cambios' : 'Crear perfil'}</button><button class="button button-quiet button-block" type="button" disabled={widgetProfileBusy} onclick={cancelWidgetProfileEdit}>Cancelar</button></div>
					</form>
				{:else if widgetProfiles.length < 5}
					<button class="button button-secondary button-block widget-new-profile" type="button" disabled={!online || busy || widgetProfileBusy} onclick={openNewWidgetProfile}><Glyph name="plus" size={16} /> Nuevo perfil</button>
				{/if}

				{#if widgetPreviewProfile && !widgetProfileFormOpen}
					<div class="widget-preview-wrap"><span class="widget-preview-label">VISTA PREVIA</span><WidgetPreview profile={widgetPreviewProfile} {pulseState} energy={widgetEnergy} energyLoading={widgetEnergyLoading} energyError={widgetEnergyError} {online} />
						{#if widgetPreviewIsEnergy}
							<p class="widget-energy-note">Suma las cargas que hayas guardado en los últimos 30 días. Excluye demostración y sesiones de evcc; el coste conserva la tarifa o estimación PVPC de cada registro.</p>
							{#if widgetEnergyError}<div class="widget-load-error" role="alert"><span>{widgetEnergyError}</span><button class="button button-secondary" type="button" disabled={!online || widgetEnergyLoading} onclick={() => void loadWidgetEnergy()}>Reintentar cálculo</button></div>{/if}
						{/if}
					</div>
				{/if}

				<section class="native-device-panel" aria-labelledby="native-devices-heading">
					<div class="native-device-heading"><div><h3 id="native-devices-heading">Enlazar dispositivo</h3></div><span class="native-device-limit">{nativeCapabilities?.max_devices_per_profile ?? 5} máx.</span></div>
					<p class="native-device-intro">Los dispositivos enlazados pueden consultar la vista de este perfil durante un máximo de 30 días. Esto no conecta Pulse con el vehículo.</p>
					{#if !online}
						<div class="native-device-notice" role="status">Sin conexión. Los datos mostrados pueden estar desactualizados; no se pueden crear códigos ni revocar dispositivos.</div>
					{/if}
					{#if !selectedWidgetProfileId}
						<div class="native-device-empty">Guarda un perfil para poder gestionar sus dispositivos enlazados.</div>
					{:else}
						{#if nativeCapabilitiesLoading && !nativeCapabilities}
							<div class="native-device-loading" role="status"><span class="loading-dot"></span> Consultando disponibilidad…</div>
						{/if}
						{#if nativeCapabilitiesError}
							<div class="native-device-error" role="alert"><span>{nativeCapabilitiesError}</span><button class="button button-small button-secondary" type="button" disabled={!online || nativeCapabilitiesLoading} onclick={() => void loadNativeCapabilities()}>{nativeCapabilitiesLoading ? 'Consultando…' : 'Reintentar'}</button></div>
						{/if}
						{#if nativeCapabilities && !nativeCapabilities.enabled}
							<div class="native-device-notice" role="status">El enlace con la app móvil aún no está configurado.</div>
						{/if}
						{#if grantsLoadingForSelectedProfile && !grantsForSelectedProfile}
							<div class="native-device-loading" role="status"><span class="loading-dot"></span> Cargando dispositivos…</div>
						{/if}
						{#if grantsErrorForSelectedProfile}
							<div class="native-device-error" role="alert"><span>{grantsErrorForSelectedProfile}</span><button class="button button-small button-secondary" type="button" disabled={!online || grantsLoadingForSelectedProfile} onclick={() => selectedWidgetProfileId && void loadWidgetGrants(selectedWidgetProfileId)}>{grantsLoadingForSelectedProfile ? 'Cargando…' : 'Reintentar'}</button></div>
						{/if}
						{#if grantsForSelectedProfile}
							{#if displayedWidgetGrants.length}
								<ul class="native-grant-list" aria-label={`Dispositivos enlazados a ${selectedWidgetProfile?.name ?? 'este perfil'}`}>
									{#each displayedWidgetGrants as grant (grant.id)}
										<li class="native-grant-row">
											<div class="native-grant-details"><strong>{grant.device_name}</strong><span>Enlazado · {dateTime(grant.created_at)}</span><span>Caduca · {dateTime(grant.expires_at)}</span><span>Última consulta · {grant.last_seen_at ? dateTime(grant.last_seen_at) : 'No registrada'}</span></div>
												<button class="button button-small button-quiet native-revoke" type="button" disabled={!online || busy || widgetProfileBusy || widgetPairingBusy || !grantsForSelectedProfile || grantsLoadingForSelectedProfile || Boolean(grantsErrorForSelectedProfile)} onclick={() => void revokeWidgetGrant(grant)}>Revocar</button>
										</li>
									{/each}
								</ul>
							{:else if !grantsLoadingForSelectedProfile && !grantsErrorForSelectedProfile}
								<p class="native-device-empty">No hay dispositivos enlazados a este perfil.</p>
							{/if}
						{/if}
						<div class="native-pairing-actions">
							<button class="button button-secondary button-block" type="button" disabled={pairingDisabled} onclick={() => void startWidgetPairing()}>{widgetPairingBusy ? 'Creando código…' : 'Crear código de enlace'}</button>
							{#if nativeCapabilities && !nativeCapabilities.enabled}<p class="field-hint">El enlace con la app móvil aún no está configurado.</p>
							{:else if nativeCapabilitiesError}<p class="field-hint">Consulta la disponibilidad antes de crear un código.</p>
							{:else if widgetProfileFormOpen}<p class="field-hint">Guarda o cierra la edición antes de crear un código para el perfil.</p>
							{:else if pairingAtCapacity}<p class="field-hint">Este perfil ya tiene el máximo de dispositivos enlazados.</p>
							{:else if !grantsForSelectedProfile || grantsLoadingForSelectedProfile}<p class="field-hint">Espera a que termine la consulta de dispositivos.</p>
							{:else if !nativeCapabilitiesLoading && !nativeCapabilities}<p class="field-hint">La disponibilidad del enlace aún no se ha podido confirmar.</p>{/if}
						</div>
						{#if widgetPairingError}<p class="native-pairing-error" role="alert">{widgetPairingError}</p>{/if}
						{#if widgetPairingResult && widgetPairingProfileId === selectedWidgetProfileId}
							<div class="native-pairing-result" aria-labelledby="pairing-code-instructions">
								<p id="pairing-code-instructions">Abre la app Pulse e introduce este código. Caduca en 5 min y solo puede usarse una vez.</p>
								<div class="native-code-row"><input type={widgetPairingVisible ? 'text' : 'password'} value={widgetPairingResult.pairing_code} readonly autocomplete="off" spellcheck="false" aria-label="Código de enlace" /><button class="button button-small button-quiet" type="button" aria-pressed={widgetPairingVisible} onclick={() => { widgetPairingVisible = !widgetPairingVisible; }}>{widgetPairingVisible ? 'Ocultar' : 'Mostrar'}</button></div>
								<div class="native-pairing-footer"><span>Caduca · {dateTime(widgetPairingResult.expires_at)}</span><div><button class="button button-small button-secondary" type="button" disabled={widgetPairingCopyState === 'copied'} onclick={() => void copyWidgetPairingCode()}>{widgetPairingCopyState === 'copied' ? 'Copiado' : 'Copiar'}</button><button class="button button-small button-quiet" type="button" onclick={clearWidgetPairing}>Cerrar</button></div></div>
								{#if widgetPairingCopyState === 'error'}<span class="native-code-status" role="status">No se pudo copiar. Puedes mostrar el código y copiarlo manualmente.</span>{/if}
							</div>
						{/if}
					{/if}
				</section>


			{:else if !widgetProfilesLoading && !widgetProfilesError}
				<div class="widget-loading" role="status">Preparando los perfiles…</div>
			{/if}
			<p class="fine-print widget-native-note">El editor guarda preferencias en Pulse. El widget consulta únicamente los campos del perfil elegido; esto no inicia una vinculación del vehículo.</p>
		</section>
				<section class="settings-card account-card" aria-labelledby="account-heading">
			<div class="settings-card-heading"><span class="settings-icon"><Glyph name="protection" size={18} /></span><div><h2 id="account-heading">Acceso privado</h2></div></div>
			<p class="muted-copy">Sesión: <strong>{user || 'Desarrollo local'}</strong>. La autenticación la gestiona el servidor.</p>
			<button class="button button-quiet button-block" type="button" disabled={busy} onclick={onLogout}>Cerrar sesión</button>
		</section>
	</section>
</section>

<style>
	.settings-card { padding: 19px; }
	.settings-card-heading { display: flex; align-items: flex-start; gap: 11px; }
	.settings-icon { display: grid; width: 37px; height: 37px; flex: 0 0 37px; place-items: center; border-radius: 10px; background: var(--forest-soft); color: var(--forest); }
	.settings-card-heading h2 { margin: 5px 0 0; font-size: 1rem; font-weight: 640; letter-spacing: -.025em; }
	#widgets-heading:focus-visible { outline: 3px solid var(--focus); outline-offset: 4px; border-radius: 2px; }
	.settings-card-heading p:not(.section-label) { margin: 5px 0 0; color: var(--muted); font-size: 1rem; line-height: 1.5; }
	.settings-form { margin-top: 20px; }
	.settings-form .field-hint { font-size: .875rem; }
	.settings-form .form-actions { justify-content: flex-start; }
	.pvpc-setting-note { align-content: start; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
	.pvpc-setting-note > strong { font-size: .875rem; }
	.pvpc-setting-note .field-hint { margin: 0; line-height: 1.5; }
	.settings-current-rate { color: var(--muted); font-size: .875rem; }
	.settings-form .form-error { margin-top: 12px; }
	.connection-status-card { display: grid; grid-template-columns: 36px minmax(0, 1fr) auto; align-items: center; gap: 10px; margin-top: 17px; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
	.connection-status-mark { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 9px; background: var(--surface); color: var(--muted); }
	.connection-status-card > div strong { font-size: .875rem; }
	.connection-status-card > div p { margin: 4px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.connection-notice { margin-top: 11px; font-size: .875rem; }
	.connection-notice :global(svg) { flex: 0 0 17px; color: var(--forest); }
	.telemetry-quality-note { margin-top: 11px; align-items: flex-start; font-size: .875rem; line-height: 1.5; }
	.telemetry-quality-note :global(svg) { flex: 0 0 17px; color: var(--muted); }
	.demo-setting-row, .backup-actions { display: flex; align-items: center; justify-content: space-between; gap: 13px; margin-top: 16px; padding-top: 15px; border-top: 1px solid var(--line); }
	.demo-setting-row > div, .backup-actions > div { display: grid; gap: 4px; }
	.demo-setting-row strong, .backup-actions strong { font-size: .875rem; }
	.demo-setting-row span, .backup-actions span { max-width: 420px; color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.backup-actions { margin-top: 13px; }
	.backup-actions .button { min-width: 130px; flex: 0 0 auto; }
	.backup-restore { margin-top: 10px; padding-top: 13px; }
	.settings-card > .fine-print { margin: 15px 0 0; }
	.theme-field { margin-top: 17px; }
	.theme-choices { min-width: 0; padding: 0; border: 0; }
	.theme-choices legend { margin-bottom: 12px; color: var(--muted-strong); font-size: .875rem; }
	.theme-options { display: grid; grid-template-columns: minmax(0, 1fr); margin-bottom: 12px; border-top: 1px solid var(--line); }
	.theme-option { display: grid; grid-template-columns: 54px minmax(0, 1fr); align-items: center; gap: 13px; min-width: 0; min-height: 60px; padding: 9px 6px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; color: var(--ink); text-align: left; cursor: pointer; transition: background-color var(--motion-fast) ease, color var(--motion-fast) ease; }
	.theme-option:hover { background: var(--surface-soft); }
	.theme-option.selected { color: var(--forest); box-shadow: inset 3px 0 var(--forest); }
	.theme-option-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: .9375rem; font-weight: 650; }
	.theme-check { display: grid; flex: 0 0 16px; width: 16px; height: 16px; place-items: center; }
	.theme-sample { display: flex; width: 50px; height: 34px; overflow: hidden; border: 1px solid #e3e4e9; border-radius: 7px; background: #f5f5f7; }
	.sample-rail { flex: 0 0 22%; border-right: 1px solid #e3e4e9; background: #ffffff; }
	.sample-content { display: grid; flex: 1; align-content: center; gap: 3px; padding: 5px; }
	.sample-content i { height: 3px; border-radius: 4px; background: #edeef2; }
	.sample-content i:first-child { width: 55%; height: 5px; background: #235cf3; }
	.sample-content i:last-child { width: 70%; }
	.sample-dark { border-color: #303640; background: #0b0d11; }
	.sample-dark .sample-rail { border-color: #303640; background: #16191f; }
	.sample-dark .sample-content i { background: #20242c; }
	.sample-dark .sample-content i:first-child { background: #98b9ff; }
	.sample-system { background: linear-gradient(115deg, #f5f5f7 48%, #0b0d11 49%); }
	.sample-system .sample-rail { border-color: transparent; background: linear-gradient(115deg, #ffffff 48%, #16191f 49%); }
	.sample-system .sample-content i { background: #edeef2; }
	.sample-system .sample-content i:first-child { background: linear-gradient(90deg, #235cf3 48%, #98b9ff 49%); }
	.pwa-intro { margin: 13px 0 14px; font-size: 1rem; }
	.install-guide { display: grid; gap: 4px; padding: 11px 0; border-top: 1px solid var(--line); }
	.install-guide strong { font-size: .875rem; }
	.install-guide span { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.widget-intro { margin: 12px 0 14px; font-size: 1rem; line-height: 1.55; }
	.widget-count { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); color: var(--muted); font-size: .875rem; }
	.widget-count strong { color: var(--ink); font-size: .875rem; font-variant-numeric: tabular-nums; }
	.widget-demo-note { display: flex; align-items: center; gap: 7px; margin-top: 11px; color: var(--accent); font-size: .875rem; }
	.widget-demo-dot { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #c88437; }
	.widget-offline { margin-top: 11px; align-items: flex-start; font-size: .875rem; line-height: 1.5; }
	.widget-offline :global(svg) { flex: 0 0 16px; }
	.widget-load-error { display: grid; gap: 8px; margin-top: 11px; padding: 11px; border: 1px solid color-mix(in srgb, var(--danger) 24%, var(--line)); border-radius: 10px; background: color-mix(in srgb, var(--danger) 6%, var(--surface)); color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.widget-load-error .button { justify-self: start; }
	.widget-loading, .widget-empty-state { display: grid; justify-items: start; gap: 5px; margin-top: 12px; padding: 13px; border: 1px dashed var(--line-strong); border-radius: 11px; color: var(--muted); font-size: .875rem; }
	.widget-empty-state strong { color: var(--ink); font-size: .875rem; }
	.widget-empty-state span { line-height: 1.5; }
	.loading-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--forest); box-shadow: 0 0 0 4px var(--forest-soft); }
	.widget-profile-list { display: grid; gap: 7px; margin: 12px 0 0; padding: 0; list-style: none; }
	.widget-profile-row { display: flex; align-items: center; gap: 6px; min-width: 0; padding: 5px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
	.widget-profile-choice { display: grid; flex: 1 1 auto; gap: 3px; min-width: 0; min-height: 48px; padding: 5px 7px; border: 0; border-radius: 7px; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
	.widget-profile-choice:hover, .widget-profile-active { background: var(--surface); }
	.widget-profile-choice strong { overflow: hidden; font-size: .875rem; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
	.widget-profile-choice span { color: var(--muted); font-size: .875rem; line-height: 1.5; overflow-wrap: anywhere; }
	.widget-profile-actions { display: flex; flex: 0 0 auto; gap: 1px; }
	.widget-profile-actions .button { min-height: 48px; padding-inline: 8px; font-size: .875rem; }
	.widget-delete { color: var(--danger); }
	.widget-preview-wrap { display: grid; gap: 7px; margin-top: 13px; }
	.widget-preview-label { color: var(--muted); font-size: 0.75rem; font-weight: 700; letter-spacing: .1em; }
	.native-device-panel { display: grid; gap: 10px; margin-top: 14px; padding: 13px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); }
	.native-device-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
	.native-device-heading h3 { margin: 4px 0 0; font-size: .875rem; font-weight: 650; letter-spacing: -.02em; }
	.native-device-limit { flex: 0 0 auto; padding: 5px 8px; border-radius: 999px; background: var(--surface); color: var(--muted); font-size: 0.75rem; font-variant-numeric: tabular-nums; }
	.native-device-intro { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.55; }
	.input-with-unit { display: flex; align-items: center; gap: 8px; }
	.input-with-unit input { min-width: 0; flex: 1; }
	.input-with-unit > span { flex: 0 0 auto; color: var(--muted); font-size: .875rem; white-space: nowrap; }
	.native-device-notice, .native-device-empty, .native-device-loading { display: grid; justify-items: start; gap: 5px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.native-device-loading { display: flex; align-items: center; gap: 9px; }
	.native-device-error { display: grid; gap: 8px; padding: 10px; border: 1px solid color-mix(in srgb, var(--danger) 24%, var(--line)); border-radius: 9px; background: color-mix(in srgb, var(--danger) 6%, var(--surface)); color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.native-device-error .button { justify-self: start; }
	.native-grant-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
	.native-grant-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); }
	.native-grant-details { display: grid; gap: 3px; min-width: 0; color: var(--muted); font-size: 0.75rem; line-height: 1.4; }
	.native-grant-details strong { overflow-wrap: anywhere; color: var(--ink); font-size: .875rem; font-weight: 640; }
	.native-revoke { min-height: 48px; flex: 0 0 auto; color: var(--danger); }
	.native-pairing-actions { display: grid; gap: 5px; }
	.native-pairing-actions .button { min-height: 48px; }
	.native-pairing-actions .field-hint { margin: 0; font-size: .875rem; line-height: 1.5; }
	.native-pairing-error { margin: 0; color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.native-pairing-result { display: grid; gap: 9px; padding: 11px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface); }
	.native-pairing-result > p { margin: 0; color: var(--ink); font-size: .875rem; line-height: 1.5; }
	.native-code-row { display: flex; align-items: center; gap: 5px; min-width: 0; }
	.native-code-row input { width: 0; min-width: 0; min-height: 48px; flex: 1 1 auto; padding: 8px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface-soft); color: var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 1rem; letter-spacing: .04em; }
	.native-code-row .button { min-height: 48px; flex: 0 0 auto; }
	.native-pairing-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 0.75rem; }
	.native-pairing-footer > div { display: flex; gap: 4px; }
	.native-pairing-footer .button { min-height: 48px; }
	.native-code-status { color: var(--danger); font-size: .875rem; line-height: 1.5; }
	.native-grant-details, .native-pairing-footer { font-size: .75rem; }
	.widget-new-profile { margin-top: 12px; }
	.widget-profile-form { display: grid; gap: 13px; margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--line); }
	.widget-form-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 9px; }
	.widget-form-title h3 { margin: 4px 0 0; font-size: .875rem; font-weight: 650; letter-spacing: -.025em; }
	.widget-form-title .button { flex: 0 0 auto; }
	.widget-fields { display: grid; gap: 3px; min-width: 0; margin: 0; padding: 9px; border: 1px solid var(--line); border-radius: 10px; }
	.widget-fields legend { padding: 0 4px; color: var(--ink); font-size: .875rem; font-weight: 600; }
	.widget-fields label { display: flex; align-items: center; gap: 8px; min-height: 48px; color: var(--ink); font-size: .875rem; cursor: pointer; }
	.widget-fields input { width: 17px; height: 17px; margin: 0; accent-color: var(--forest); }
	.widget-form-grid { display: grid; gap: 11px; }
	.widget-form-grid .field label { font-size: .875rem; }
	.widget-form-grid select, .widget-profile-form input:not([type='checkbox']) { min-height: 48px; font-size: 1rem; }
	.widget-age-field .field-hint { margin-top: 5px; font-size: .875rem; }
	.widget-profile-form .form-error { margin: -4px 0 0; font-size: .875rem; }
	.widget-form-actions { display: grid; gap: 4px; }
	.widget-form-actions .button { min-height: 48px; }
	.widget-native-note { line-height: 1.55; }
	.widget-energy-note { margin: 2px 0 0; color: var(--muted); font-size: .875rem; line-height: 1.55; }
	.widget-templates { display: grid; gap: 8px; min-width: 0; margin: 0; padding: 0; border: 0; }
	.widget-templates legend { margin-bottom: 8px; color: var(--ink); font-size: .875rem; font-weight: 600; }
	.widget-templates button { display: grid; gap: 4px; min-height: 60px; padding: 12px; border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface); color: var(--ink); text-align: left; cursor: pointer; }
	.widget-templates button[aria-pressed='true'] { border-color: var(--forest); background: var(--forest-soft); }
	.widget-templates strong { font-size: .875rem; font-weight: 650; }
	.widget-templates span { color: var(--muted); font-size: .875rem; line-height: 1.5; }
	.widget-profile-form > .field label { font-size: .875rem; }
	.widget-profile-form .button { font-size: .875rem; }
	.account-card .muted-copy { margin: 12px 0; font-size: .875rem; }
	.account-card .muted-copy strong { color: var(--ink); font-weight: 650; }
	@media (max-width: 430px) { .connection-status-card { grid-template-columns: 34px minmax(0, 1fr); } .connection-status-card > .badge { grid-column: 2; justify-self: start; } .demo-setting-row, .backup-actions { align-items: flex-start; flex-direction: column; } .backup-actions .button { width: 100%; } .native-grant-row { align-items: flex-start; flex-direction: column; } .native-revoke { width: 100%; } .native-pairing-footer { align-items: flex-start; flex-direction: column; } .native-pairing-footer > div { width: 100%; } .native-pairing-footer .button { flex: 1 1 0; } }

	@media (max-width: 360px) {
		.widget-count { align-items: flex-start; flex-wrap: wrap; gap: 6px; }
		.widget-profile-actions { flex-wrap: wrap; }
		.widget-profile-actions .button { flex: 1 1 100%; }
	}

	.vehicle-color-field { min-width: 0; margin: 0; padding: 0; border: 0; }
	.vehicle-color-field legend { margin-bottom: 4px; font-size: 1rem; font-weight: 650; }
	.vehicle-color-options { display: grid; gap: 7px; margin-top: 12px; }
	.vehicle-color-option { display: flex; min-height: 48px; align-items: center; gap: 11px; padding: 9px 12px; border: 1px solid var(--line-strong); border-radius: 10px; color: var(--ink); background: var(--surface); cursor: pointer; font-size: .875rem; line-height: 1.4; }
	.vehicle-color-option.selected { border-color: var(--forest); background: var(--forest-soft); }
	.vehicle-color-option:focus-within { outline: 2px solid var(--focus); outline-offset: 3px; }
	.vehicle-color-option > span:not(.paint-swatch) { flex: 1; }
	.paint-swatch { width: 24px; height: 24px; flex: 0 0 24px; border: 1px solid #7d838b; border-radius: 50%; }

	.settings-card { padding: 24px 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.settings-card-heading h2 { margin-top: 0; font-size: 1.125rem; }
	.settings-card-heading p:not(.section-label) { font-size: .875rem; }
	.settings-icon { width: 24px; height: 24px; flex-basis: 24px; border-radius: 0; background: transparent; }
	.settings-form { margin-top: 12px; }
	.vehicle-color-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
	.vehicle-color-option { min-height: 48px; padding: 8px; border-color: transparent; border-radius: 8px; background: transparent; font-size: .8125rem; }
	.vehicle-color-option.selected { border-color: var(--forest); }
	.vehicle-color-option:hover { background: var(--surface-soft); }
	.paint-swatch { border-color: var(--line-strong); }
	.connection-status-card { padding: 14px 0; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; }
	.pvpc-setting-note { border: 0; background: var(--forest-soft); color: var(--forest); }
	.form-grid { gap: 20px 16px; }
	.settings-current-rate { font-size: .8125rem; }
	@media (max-width: 700px) { .vehicle-color-options { gap: 4px; } .settings-form .field-hint { font-size: .8125rem; } }
	@media(max-width:359px){.vehicle-color-options{grid-template-columns:minmax(0,1fr)}}

	.settings-intro { align-items: center; margin-bottom: 16px; }
	.settings-profile { display: grid; gap: 16px; min-width: 0; }
	.settings-section-switcher { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); width: 100%; margin-bottom: 0; }
	.settings-section-switcher button { min-width: 0; overflow-wrap: anywhere; }
	.settings-section-panel { display: grid; gap: 0; min-width: 0; padding: clamp(20px, 3vw, 30px); border: 1px solid var(--line); border-radius: 26px; background: var(--surface); box-shadow: var(--shadow); }
	.settings-section-panel > .settings-card { min-width: 0; margin: 0; padding: 22px 0; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; box-shadow: none; }
	.settings-section-panel > .settings-card:last-child { padding-bottom: 0; border-bottom: 0; }
	.settings-section-panel .settings-card-heading h2 { margin-top: 0; }
	.settings-section-panel .settings-card-heading p:not(.section-label) { font-size: .875rem; }
	.settings-section-panel .settings-icon { width: 24px; height: 24px; flex-basis: 24px; border-radius: 0; background: transparent; }
	.settings-section-panel .settings-form { margin-top: 16px; }
	.vehicle-settings-form { display: grid; gap: 20px; }
	.vehicle-profile-header { display: grid; grid-template-columns: minmax(0, .88fr) minmax(0, 1.12fr); align-items: center; gap: clamp(20px, 3vw, 42px); min-width: 0; padding-bottom: 20px; border-bottom: 1px solid var(--line); }
	.vehicle-profile-art { min-width: 0; --vehicle-stage-height: 200px; }
	.vehicle-profile-art :global(.vehicle-artwork) { width: 100%; }
	.vehicle-profile-art :global(.vehicle-artwork figcaption) { margin-top: -3px; }
	.vehicle-identity-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 16px; min-width: 0; }
	.vehicle-color-field { padding-bottom: 18px; border-bottom: 1px solid var(--line); }
	.vehicle-color-field legend { margin-bottom: 4px; font-size: .9375rem; font-weight: 700; }
	.vehicle-color-options { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 12px; margin-top: 8px; }
	.vehicle-color-option { min-width: 0; min-height: 44px; padding: 6px 3px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; font-size: .8125rem; overflow-wrap: anywhere; }
	.vehicle-color-option.selected { border-color: var(--forest); background: transparent; color: var(--forest); }
	.vehicle-color-option:hover { background: var(--surface-soft); }
	.vehicle-color-option:focus-within { outline-offset: 2px; }
	.vehicle-configuration-grid { margin-top: 0; }
	@media (max-width: 620px) {
		.settings-section-switcher { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.settings-section-panel { padding: 18px; border-radius: 24px; }
		.settings-section-panel > .settings-card { padding: 19px 0; }
		.settings-section-panel .settings-form .form-actions { align-items: stretch; flex-direction: column; }
		.settings-section-panel .settings-form .form-actions > .button { width: 100%; }
		.vehicle-profile-header { grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: 14px; }
		.vehicle-profile-art { --vehicle-stage-height: 140px; }
		.vehicle-identity-fields { grid-template-columns: minmax(0, 1fr); gap: 11px; }
		.vehicle-color-options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	}
	@media (max-width: 430px) {
		.vehicle-profile-header { grid-template-columns: minmax(96px, .85fr) minmax(0, 1.35fr); gap: 12px; }
		.vehicle-profile-art { --vehicle-stage-height: 140px; }
		.vehicle-identity-fields { grid-template-columns: minmax(0, 1fr); gap: 10px; }
	}

</style>
