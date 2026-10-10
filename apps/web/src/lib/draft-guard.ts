/** Live form flags only. No values, credentials, persistence or submission. */
export type DraftKey = 'charge' | 'maintenance' | 'preferences' | 'widget-profile' | 'native-link' | 'push-device';

export class DraftGuard {
	private readonly entries = new Map<DraftKey, { lease: symbol; isDirty: () => boolean }>();

	register(key: DraftKey, isDirty: () => boolean): () => void {
		const lease = Symbol(key);
		this.entries.set(key, { lease, isDirty });
		return () => {
			if (this.entries.get(key)?.lease === lease) this.entries.delete(key);
		};
	}

	hasDirty(): boolean {
		for (const entry of this.entries.values()) {
			try { if (entry.isDirty()) return true; }
			catch { return true; }
		}
		return false;
	}

	clear(): void { this.entries.clear(); }
}
