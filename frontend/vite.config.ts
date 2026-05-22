import { sveltekit } from '@sveltejs/kit/vite';
import { SvelteKitPWA } from '@vite-pwa/sveltekit';
import { defineConfig } from 'vitest/config';

export default defineConfig({
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
				globPatterns: ['client/**/*.{js,css,ico,png,svg,webp,woff,woff2,html}']
			},
			devOptions: { enabled: true, type: 'module', navigateFallback: '/' }
		})
	],
	test: {
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./vitest-setup.ts'],
		include: ['src/**/*.{test,spec}.{js,ts}']
	},
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined
});
