import { waitForRead } from './startup-read';

/** Serialize state reads and reconcile changes received while a read is pending. */
export function createStateRefresh<T>(read: (signal: AbortSignal) => Promise<T>, apply: (value: T) => void, timeoutMs = 30_000) {
	if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new RangeError('Invalid state read deadline');
	let generation = 0;
	let requested = 0;
	let flight: Promise<boolean> | null = null;
	let controller: AbortController | null = null;

	function invalidate() {
		generation += 1;
		controller?.abort();
		controller = null;
		flight = null;
	}

	/** Record an event immediately, even when starting the next read is debounced. */
	function markDirty(): boolean {
		requested += 1;
		return flight !== null;
	}

	function refresh(): Promise<boolean> {
		requested += 1;
		if (flight) return flight;
		const currentGeneration = generation;
		const currentController = new AbortController();
		controller = currentController;
		const timer = setTimeout(() => currentController.abort(new DOMException('La consulta de estado ha agotado su plazo.', 'TimeoutError')), timeoutMs);
		async function reconcile(): Promise<boolean> {
			while (currentGeneration === generation) {
				const target = requested;
				let value: T;
				try {
					value = await waitForRead(read(currentController.signal), currentController.signal);
				} catch (error) {
					if (currentGeneration !== generation) return false;
					// A newer event warrants another read; never retry without a request.
					if (!currentController.signal.aborted && target !== requested) continue;
					throw error;
				}
				if (currentGeneration !== generation) return false;
				if (target !== requested) continue;
				apply(value);
				return true;
			}
			return false;
		}
		const operation = reconcile().finally(() => {
			clearTimeout(timer);
			if (flight === operation) {
				flight = null;
				controller = null;
			}
		});
		flight = operation;
		return operation;
	}

	return { refresh, invalidate, markDirty };
}
