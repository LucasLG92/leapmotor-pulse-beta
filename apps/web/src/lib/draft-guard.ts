/** Live form flags only. No values, credentials, persistence or submission. */
export type DraftKey = 'maintenance-plan' | 'energy-plan' | 'navigation-favorite' | 'charge' | 'journey' | 'maintenance' | 'preferences' | 'widget-profile' | 'native-link' | 'push-device';

export class DraftGuard {
	private readonly entries = new Map<DraftKey, { lease: symbol; isDirty: () => boolean; unconfirmedSave?: () => boolean }>();

	register(key: DraftKey, isDirty: () => boolean, unconfirmedSave?: () => boolean): () => void {
		const lease = Symbol(key);
		this.entries.set(key, { lease, isDirty, unconfirmedSave });
		return () => {
			if (this.entries.get(key)?.lease === lease) this.entries.delete(key);
		};
	}

	hasDirty(): boolean {
		for (const entry of this.entries.values()) {
			try { if (entry.isDirty() || entry.unconfirmedSave?.()) return true; }
			catch { return true; }
		}
		return false;
	}

	hasUnconfirmedSave(): boolean {
		for (const entry of this.entries.values()) {
			try { if (entry.unconfirmedSave?.()) return true; }
			catch { return true; }
		}
		return false;
	}

	clear(): void { this.entries.clear(); }
}
