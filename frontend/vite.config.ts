import { sveltekit } from '@sveltejs/kit/vite';
import { SvelteKitPWA } from '@vite-pwa/sveltekit';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => ({
	plugins: [
		sveltekit(),
		SvelteKitPWA({
			registerType: 'autoUpdate',
			manifest: {
				name: 'FPL Autopilot',
				short_name: 'Autopilot',
				description: 'Personal Fantasy Premier League decision dashboard',
				lang: 'en',
				theme_color: '#0b0f14',
				background_color: '#0b0f14',
				display: 'standalone',
				orientation: 'portrait',
				start_url: '/',
				icons: [
					{ src: '/icons/pwa-64x64.png', sizes: '64x64', type: 'image/png' },
					{ src: '/icons/pwa-192x192.png', sizes: '192x192', type: 'image/png' },
					{ src: '/icons/pwa-512x512.png', sizes: '512x512', type: 'image/png' },
					{
						src: '/icons/maskable-icon-512x512.png',
						sizes: '512x512',
						type: 'image/png',
						purpose: 'maskable'
					}
				]
			},
			workbox: {
				globPatterns: ['client/**/*.{js,css,ico,png,svg,webp,woff,woff2,html}'],
				// Network-first for the API: fresh when online, last-known data when offline.
				runtimeCaching: [
					{
						urlPattern: /\/api\//,
						handler: 'NetworkFirst',
						options: {
							cacheName: 'fpl-api',
							networkTimeoutSeconds: 5,
							expiration: { maxEntries: 16, maxAgeSeconds: 60 * 60 * 24 },
							cacheableResponse: { statuses: [0, 200] }
						}
					}
				]
			},
			devOptions: { enabled: true, type: 'module', navigateFallback: '/' }
		})
	],
	// Dev/preview: same-origin /api proxied to the FastAPI backend (`fpl-autopilot serve`).
	server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
	preview: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
	test: {
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./vitest-setup.ts'],
		include: ['src/**/*.{test,spec}.{js,ts}']
	},
	resolve: mode === 'test' ? { conditions: ['browser'] } : undefined
}));
