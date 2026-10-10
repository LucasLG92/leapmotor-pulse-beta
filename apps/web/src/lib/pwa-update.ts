/** Observe availability only. Activation stays behind the app's explicit action. */
export function observeServiceWorkerUpdate(
	registration: ServiceWorkerRegistration,
	hasController: () => boolean,
	onAvailable: () => void
): () => void {
	let current: ServiceWorker | null = null;
	let offered: ServiceWorker | null = null;
	let disposed = false;

	function offer(worker: ServiceWorker) {
		if (disposed || !hasController() || offered === worker) return;
		offered = worker;
		onAvailable();
	}

	function stateChanged() {
		if (!current || disposed) return;
		if (current.state === 'installed') offer(current);
		if (current.state === 'installed' || current.state === 'activated' || current.state === 'redundant') {
			current.removeEventListener('statechange', stateChanged);
			current = null;
		}
	}

	function watch() {
		if (disposed) return;
		const worker = registration.installing;
		if (worker && worker !== current) {
			current?.removeEventListener('statechange', stateChanged);
			current = worker;
			worker.addEventListener('statechange', stateChanged);
			stateChanged();
		}
		if (registration.waiting) offer(registration.waiting);
	}

	registration.addEventListener('updatefound', watch);
	// updatefound may have happened before register() resolved.
	watch();
	return () => {
		disposed = true;
		registration.removeEventListener('updatefound', watch);
		current?.removeEventListener('statechange', stateChanged);
		current = null;
	};
}
