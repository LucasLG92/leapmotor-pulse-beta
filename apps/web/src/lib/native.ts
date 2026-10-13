export interface NativeBridgeStatus {
	configured: boolean;
	authenticated: boolean;
	widgetLinked: boolean;
	widgetCacheEnabled: boolean;
	supportsRequestCancellation?: boolean;
}

export interface NativeBridgeResponse {
	status: number;
	contentType: string;
	body: string;
}

/** El selector informa su finalización; no verifica la copia externa. */
export interface NativeJourneyReportExportResult {
	status: 'completed' | 'cancelled';
}

export interface NativeBridge {
	status(): Promise<NativeBridgeStatus>;
	login(): Promise<void>;
	logout(): Promise<void>;
	request(input: { path: string; method: string; body?: string; request_id?: string }): Promise<NativeBridgeResponse>;
	cancelRequest?(input: { request_id: string }): Promise<void>;
	supportsRequestCancellation?(): boolean;
	exportJourneyReport?(input: { days: 30 | 90 | 365 }): Promise<NativeJourneyReportExportResult>;
	subscribe(onRevision: (revision: number) => void, onError: () => void): Promise<() => void>;
	linkWidget(input: { pairingCode: string; deviceName: string }): Promise<void>;
	setWidgetCache(input: { enabled: boolean }): Promise<void>;
	clearWidget(): Promise<void>;
	pinWidget(): Promise<{ supported: boolean }>;
}

export type NativePlatform = 'ios' | 'android';

let nativeBridge: NativeBridge | null = null;
let nativePlatform: NativePlatform | null = null;

/** Mensajes fijos: nunca mostrar detalles arbitrarios del transporte nativo. */
export function nativeRequestFailureMessage(error: unknown): string {
	if (error && typeof error === 'object' && 'code' in error && error.code === 'NATIVE_MUTATION_OUTCOME_UNKNOWN') {
		return 'No se pudo confirmar el resultado; la operación puede haberse guardado. Comprueba el resultado antes de repetirla. Si la sesión ha caducado, vuelve a iniciar sesión.';
	}
	return 'No se pudo contactar con el servidor. Comprueba la conexión e inténtalo de nuevo.';
}

/** Instalada por el entrypoint de Capacitor; la aplicación web no importa Capacitor. */
export function installNativeBridge(bridge: NativeBridge, platform?: NativePlatform): void {
	nativeBridge = bridge;
	nativePlatform = platform ?? null;
}

export function getNativePlatform(): NativePlatform | null {
	return nativePlatform;
}

export function isNativeApp(): boolean {
	return nativeBridge !== null;
}

export function getNativeBridge(): NativeBridge | null {
	return nativeBridge;
}
