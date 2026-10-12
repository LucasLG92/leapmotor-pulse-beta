export type PushNotice = {
	title: 'Leapmotor Pulse';
	body: string;
	tag: string;
	path: '/' | '/settings' | '/energy';
};

export const genericPushNotice: PushNotice = {
	title: 'Leapmotor Pulse',
	body: 'Hay una novedad disponible en Pulse.',
	tag: 'pulse-generic',
	path: '/'
};

const notices = {
	test: {
		title: 'Leapmotor Pulse', body: 'Aviso de prueba de Pulse. Comprueba este dispositivo.',
		tag: 'pulse-test', path: '/settings'
	},
	charging_observation: {
		title: 'Leapmotor Pulse', body: 'Hay una observación de carga disponible.',
		tag: 'pulse-charging-observation', path: '/energy'
	},
	backup_alert: {
		title: 'Leapmotor Pulse', body: 'Una comprobación de copias requiere revisión.',
		tag: 'pulse-backup-alert', path: '/settings'
	},
	backup_recovery: {
		title: 'Leapmotor Pulse', body: 'Una comprobación de copias dejó de registrar avisos.',
		tag: 'pulse-backup-recovery', path: '/settings'
	}
} as const satisfies Record<string, PushNotice>;

function toBytes(data: string | ArrayBuffer | Uint8Array | null): Uint8Array | null {
	if (data === null) return null;
	if (typeof data === 'string') return new TextEncoder().encode(data);
	return data instanceof Uint8Array ? data : new Uint8Array(data);
}

/** Parses only the stable public discriminator. Payload text and extra fields are never reflected. */
export function parsePushNotice(data: string | ArrayBuffer | Uint8Array | null): PushNotice {
	try {
		const bytes = toBytes(data);
		if (!bytes || bytes.byteLength > 4096) return genericPushNotice;
		const payload: unknown = JSON.parse(new TextDecoder().decode(bytes));
		if (!payload || typeof payload !== 'object' || !('version' in payload) || payload.version !== 1 || !('kind' in payload)) {
			return genericPushNotice;
		}
		if (typeof payload.kind !== 'string' || !Object.hasOwn(notices, payload.kind)) return genericPushNotice;
		return notices[payload.kind as keyof typeof notices];
	} catch {
		return genericPushNotice;
	}
}
