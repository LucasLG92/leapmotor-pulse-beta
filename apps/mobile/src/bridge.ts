import type { NativeBridge } from '../../web/src/lib/native';

type Listener = { remove(): Promise<void> };
export type NativePlugin = Omit<NativeBridge, 'subscribe'> & {
  addListener(event: 'pulseRevision' | 'pulseStreamError', callback: (value: { revision?: number; subscriptionId?: string }) => void): Promise<Listener>;
  startEvents(input: { subscriptionId: string }): Promise<void>;
  stopEvents(input: { subscriptionId: string }): Promise<void>;
};

/** Cada suscripción posee su lease: limpiar una anterior nunca detiene la nueva. */
export function createNativeBridge(plugin: NativePlugin): NativeBridge {
  let latestSubscription = 0;
  return {
    status: () => plugin.status(),
    login: () => plugin.login(),
    logout: () => plugin.logout(),
    request: (request) => plugin.request(request),
    ...(typeof plugin.exportJourneyReport === 'function'
      ? { exportJourneyReport: (input: { days: 30 | 90 | 365 }) => plugin.exportJourneyReport!(input) }
      : {}),
    linkWidget: (request) => plugin.linkWidget(request),
    setWidgetCache: (request) => plugin.setWidgetCache(request),
    clearWidget: () => plugin.clearWidget(),
    pinWidget: () => plugin.pinWidget(),
    async subscribe(onRevision, onError) {
      const subscription = ++latestSubscription;
      const subscriptionId = String(subscription);
      let revision: Listener | undefined;
      let failure: Listener | undefined;
      const removeListeners = () => Promise.allSettled([
        Promise.resolve().then(() => revision?.remove()),
        Promise.resolve().then(() => failure?.remove())
      ]);
      const stopOwnedStream = async () => {
        if (subscription !== latestSubscription) return;
        latestSubscription += 1;
        try { await plugin.stopEvents({ subscriptionId }); } catch { /* El lease ya está invalidado. */ }
      };
      try {
        revision = await plugin.addListener('pulseRevision', (data) => {
          if (subscription === latestSubscription && data.subscriptionId === subscriptionId && Number.isSafeInteger(data.revision) && data.revision! >= 0) onRevision(data.revision!);
        });
        failure = await plugin.addListener('pulseStreamError', (data) => {
          if (subscription === latestSubscription && data.subscriptionId === subscriptionId) onError();
        });
        if (subscription !== latestSubscription) throw new Error('Suscripción reemplazada.');
        await plugin.startEvents({ subscriptionId });
      } catch {
        await Promise.all([removeListeners(), stopOwnedStream()]);
        throw new Error('No se pudo establecer el canal de actualizaciones.');
      }
      let removed = false;
      return () => {
        if (removed) return;
        removed = true;
        void Promise.all([removeListeners(), stopOwnedStream()]);
      };
    }
  };
}
