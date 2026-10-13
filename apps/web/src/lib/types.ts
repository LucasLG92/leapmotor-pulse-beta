import type { components } from './api.generated';
import type { DraftGuard } from './draft-guard';

export type Vehicle = components['schemas']['Vehicle'];
export type NavigationStatus = components['schemas']['NavigationStatus'];
export type NavigationPlace = components['schemas']['NavigationPlace'];
export type NavigationStation = components['schemas']['NavigationStation'];
export type NavigationSearch = components['schemas']['NavigationSearch'];
export type NavigationChargers = components['schemas']['NavigationChargers'];
export type NavigationTile = components['schemas']['NavigationTile'];
export type PricingMethod = components['schemas']['Settings']['pricing_method'];
export type PricingInterval = components['schemas']['PriceInterval'];
export type PricingSnapshot = components['schemas']['PricingSnapshot'];
export type ChargeQuote = components['schemas']['ChargeQuote'];
export type ChargerStatus = components['schemas']['ChargerStatus'];
export type ChargerSession = components['schemas']['ChargerSession'];
export type ChargerSessionPage = components['schemas']['ChargerSessionPage'];
export type ChargerPricingEvent = components['schemas']['ChargerPricingEvent'];
export type ChargerPricingHistoryPage = components['schemas']['ChargerPricingHistoryPage'];
export type ChargePayload = components['schemas']['ChargePayload'];
export type ChargeCreationRequest = components['schemas']['ChargeCreationRequest'];
export type ChargeQuotePayload = Omit<ChargePayload, 'pricing_method' | 'ended_at' | 'request_id'> & {
	ended_at: string;
	pricing_method: 'pvpc';
};
export type ChargeEditPayload = components['schemas']['ChargeEditPayload'];
export type Settings = components['schemas']['Settings'];
export type SettingsPayload = components['schemas']['SettingsPayload'];
export type ChargeSession = components['schemas']['Charge'];
export type EnergySummary = components['schemas']['Energy'];
export type ChargePage = components['schemas']['ChargePage'];
export type ChargeAggregate = components['schemas']['ChargeAggregate'];
export type EnergyAnalytics = components['schemas']['EnergyAnalytics'];
export type EnergyMonth = components['schemas']['EnergyMonth'];
export type ObservationCoverage = components['schemas']['ObservationCoverage'];
export type EnergyEstimates = components['schemas']['EnergyEstimates'];
export type Journey = components['schemas']['Journey'];
export type ManualJourney = components['schemas']['ManualJourney'];
export type MaintenancePlan = components['schemas']['MaintenancePlanResult'];
export type MaintenancePlanPage = components['schemas']['MaintenancePlanPage'];
export type MaintenancePlanInput = components['schemas']['MaintenancePlanPayload'];
export type ChargingPlanInput = components['schemas']['ChargingPlanPayload'];
export type ChargingPlan = components['schemas']['ChargingPlanResult'];
export type ChargingComparison = components['schemas']['ChargeComparisonResult'];
export type NavigationFavorite = components['schemas']['FavoriteResult'];
export type NavigationFavoritePage = components['schemas']['FavoritePage'];

export type ManualJourneyPage = components['schemas']['ManualJourneyPage'];
export type ManualJourneyReport = components['schemas']['ManualJourneyReport'];
export type ManualJourneyCreationRequest = components['schemas']['ManualJourneyCreationRequest'];
export type MaintenanceItem = components['schemas']['Maintenance'];
export type ProtectionEvent = components['schemas']['Event'];
export type ObservedEvent = components['schemas']['ObservedEvent'];
export type ObservedEventPage = components['schemas']['ObservedEventPage'];
export type TelemetrySnapshot = components['schemas']['TelemetrySnapshot'];
export type TelemetryPage = components['schemas']['TelemetryPage'];
export type TemporalQuality = components['schemas']['TemporalQuality'];
export type CommandHistoryEntry = components['schemas']['CommandResult'];
export type CommandHistoryResponse = components['schemas']['CommandHistory'];
export type Command = components['schemas']['Command'];
export type PulseState = components['schemas']['StateResponse'];
export type MqttStatus = components['schemas']['MqttStatus'];
export type AbrpStatus = components['schemas']['AbrpStatus'];
export type SessionState = components['schemas']['SessionResponse'];
export type WidgetProfile = components['schemas']['WidgetProfile'];
export type WidgetProfileList = components['schemas']['WidgetProfileList'];
export type WidgetProfileInput = components['schemas']['WidgetProfilePayload'];
export type WidgetProfileCreatePayload = components['schemas']['WidgetProfilePayload'];
export type WidgetProfileEditPayload = components['schemas']['WidgetProfileEditPayload'];
export type NativeCapabilities = components['schemas']['NativeCapabilities'];
export type WidgetGrant = components['schemas']['WidgetGrant'];
export type WidgetGrantList = components['schemas']['WidgetGrantList'];
export type WidgetPairingResult = components['schemas']['WidgetPairingResult'];
export type PushCapabilities = components['schemas']['PushCapabilities'] & {
	backup_alerts_available: boolean;
};
export type PushDevice = components['schemas']['PushDevice'] & {
	backup_alerts_enabled: boolean;
};
export type PushDeviceList = components['schemas']['PushDeviceList'];
export type PushTestResult = components['schemas']['PushTestResult'];
export type WebPushSubscriptionInput = components['schemas']['PushSubscription'];
export type WidgetProfileField = WidgetProfile['fields'][number];
export type WidgetProfileAppearance = WidgetProfile['appearance'];
export type WidgetProfilePrivacyMode = WidgetProfile['privacy_mode'];
export type Mode = PulseState['mode'];
export type MutationPayload =
	| SettingsPayload
	| components['schemas']['TogglePayload']
	| components['schemas']['CommandPayload']
	| ChargePayload
	| ChargeEditPayload
	| components['schemas']['ManualJourneyPayload']
	| components['schemas']['ManualJourneyEditPayload']
	| components['schemas']['ManualJourneyDeletePayload']
	| components['schemas']['MaintenancePlanPayload']
	| components['schemas']['MaintenancePlanEditPayload']
	| components['schemas']['MaintenanceCompletionPayload']
	| components['schemas']['RevisionPayload']
	| components['schemas']['FavoritePayload']
	| components['schemas']['MaintenancePayload']
	| components['schemas']['MaintenanceEditPayload']
	| components['schemas']['ImportPayload']
	| components['schemas']['WidgetProfilePayload']
	| components['schemas']['WidgetProfileEditPayload']
	| components['schemas']['Payload'];

export type HttpMethod = 'POST' | 'PUT' | 'DELETE';
export type Route = 'overview' | 'energy' | 'navigation' | 'activity' | 'protection' | 'settings';
export type ThemePreference = 'system' | 'light' | 'dark';

export interface PageProps {
	drafts: DraftGuard;
	pulseState: PulseState;
	busy: boolean;
	online: boolean;
	revision: number;
	settingsSection?: 'preparation' | null;
	user: string | null;
	theme: ThemePreference;
	canInstall: boolean;
	mutate: (path: string, method: HttpMethod, data?: MutationPayload, success?: string) => Promise<boolean>;
	quoteCharge: (payload: ChargeQuotePayload) => Promise<ChargeQuote>;
	reserveChargeCreation: () => Promise<ChargeCreationRequest>;
	reserveJourneyCreation: () => Promise<ManualJourneyCreationRequest>;
	onSessionExpired: () => void;
	navigation: {
		status: (signal?: AbortSignal) => Promise<NavigationStatus>;
		search: (query: string, signal?: AbortSignal) => Promise<NavigationSearch>;
		chargers: (latitude: number, longitude: number, signal?: AbortSignal) => Promise<NavigationChargers>;
		tile: (z: number, x: number, y: number, signal?: AbortSignal) => Promise<NavigationTile>;
	};
	createWidgetPairing: (profileId: string) => Promise<WidgetPairingResult>;
	notifications: {
		capabilities: () => Promise<PushCapabilities>;
		devices: () => Promise<PushDeviceList>;
		register: (name: string, subscription: WebPushSubscriptionInput) => Promise<PushDevice>;
		revoke: (id: string) => Promise<void>;
		test: (id: string) => Promise<PushTestResult>;
		setBackupAlerts: (id: string, enabled: boolean) => Promise<PushDevice>;
	};
	runNotificationAction: (operation: () => Promise<void>) => Promise<void>;
	confirmAction: (title: string, message: string, confirmLabel?: string) => Promise<boolean>;
	toast: (message: string, tone?: 'info' | 'success' | 'error') => void;
	setTheme: (theme: ThemePreference) => void;
	onLogout: () => Promise<void>;
	onExport: () => Promise<BackupActionResult>;
	onImport: (file: File) => Promise<BackupActionResult>;
	onInstall: () => Promise<void>;
}

export type BackupActionResult =
	| { status: 'success' | 'cancelled' }
	| { status: 'error'; message: string };
