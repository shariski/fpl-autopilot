// SPA: no SSR (data is fetched client-side from /api). Prerender the static app
// shell so there's a real index.html (faster first paint + a matched precache
// entry, so the PWA service worker has no empty-glob warning).
export const ssr = false;
export const prerender = true;
