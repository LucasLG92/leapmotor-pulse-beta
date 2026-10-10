import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.lucas.pulse',
  appName: 'Leapmotor Pulse',
  webDir: 'dist',
  backgroundColor: '#f5f7f3',
  loggingBehavior: 'none',
  android: { allowMixedContent: false, webContentsDebuggingEnabled: false },
  server: { androidScheme: 'https', cleartext: false }
};

export default config;
