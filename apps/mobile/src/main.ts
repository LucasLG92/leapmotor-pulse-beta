import { Capacitor, registerPlugin } from '@capacitor/core';
import { installNativeBridge } from '../../web/src/lib/native';
import { createNativeBridge, type NativePlugin } from './bridge';

const plugin = registerPlugin<NativePlugin>('PulseNative');
installNativeBridge(createNativeBridge(plugin), Capacitor.getPlatform() === 'ios' ? 'ios' : 'android');

// Bundle local de la misma UI. El puente queda instalado antes de iniciar lecturas.
await import('../../web/src/main');
