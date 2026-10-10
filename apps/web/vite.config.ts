import { defineConfig, loadEnv } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig(({ mode }) => {
	const apiTarget = loadEnv(mode, '.', '').PULSE_API_ORIGIN || 'http://127.0.0.1:8780';
	return {
		server: {
			host: '127.0.0.1',
			proxy: {
				'/api': { target: apiTarget, changeOrigin: false },
				'/healthz': { target: apiTarget, changeOrigin: false }
			}
		},
		plugins: [
			svelte(),
			VitePWA({
				strategies: 'injectManifest',
				srcDir: 'src',
				filename: 'sw.ts',
				registerType: 'prompt',
				injectRegister: null,
				includeAssets: ['icon.svg', 'icon-180.png', 'icon-192.png', 'icon-512.png'],
				manifest: {
					id: '/',
					name: 'Leapmotor Pulse',
					short_name: 'Pulse',
					lang: 'es',
					description: 'Un espacio privado para preparar y cuidar tu movilidad.',
					start_url: '/',
					scope: '/',
					display: 'standalone',
					orientation: 'any',
					background_color: '#f6f7f2',
					theme_color: '#18352b',
					icons: [
						{ src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
						{ src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' }
					]
				},
				injectManifest: {
					globPatterns: ['**/*.{html,js,css,svg,png,webmanifest}'],
					globIgnores: ['**/*.map', 'sw.js', 'service-worker.js'],
					maximumFileSizeToCacheInBytes: 300_000
				},
				devOptions: { enabled: false }
			})
		]
	};
});
