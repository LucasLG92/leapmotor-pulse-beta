/** A deadline for startup reads only; never use it to retry a mutation. */
export class StartupTimeoutError extends Error {
	constructor() {
		super('Pulse está tardando más de lo esperado. Comprueba la conexión y pulsa «Reintentar».');
		this.name = 'StartupTimeoutError';
	}
}

/** Also bounds bridges that cannot physically cancel their underlying operation. */
export function waitForRead<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
	return new Promise<T>((resolve, reject) => {
		let settled = false;
		function finish(action: () => void) {
			if (settled) return;
			settled = true;
			signal.removeEventListener('abort', abort);
			action();
		}
		const abort = () => finish(() => reject(signal.reason ?? new DOMException('La lectura se canceló.', 'AbortError')));
		signal.addEventListener('abort', abort, { once: true });
		operation.then(
			(value) => signal.aborted ? abort() : finish(() => resolve(value)),
			(error) => finish(() => reject(error))
		);
		if (signal.aborted) abort();
	});
}

export function createStartupAttempt(timeoutMs = 15_000) {
	if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new RangeError('Invalid startup deadline');
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(new StartupTimeoutError()), timeoutMs);
	return {
		signal: controller.signal,
		wait<T>(operation: Promise<T>): Promise<T> { return waitForRead(operation, controller.signal); },
		dispose() {
			clearTimeout(timer);
			controller.abort();
		}
	};
}
