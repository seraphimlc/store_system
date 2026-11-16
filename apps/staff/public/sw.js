/* Employee PWA: no application shell, API, map or account data is cached. */
const VERSION = '__STAFF_PWA_VERSION__';
const CACHE_PREFIX = 'staff-pwa-offline-';
const CACHE = CACHE_PREFIX + VERSION;
const OFFLINE = '/pwa/offline.html';
const employeePage = path => /^(?:\/|\/login\/?|\/messages\/?|\/my\/(?:tasks|password|confirm|appeal|perf|performance|plan|attendance|team-attendance|report(?:\/feedback)?|self-report)\/?|\/tasks\/\d+\/?|\/my\/work\/?|\/my\/work\/(?:batch-grids|grids|regions)\/\d+\/?|\/my\/work\/batches\/\d+(?:\/(?:logs|new-stores)|\/regions\/\d+)?\/?)$/.test(path);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const response = await fetch(OFFLINE, { cache: 'reload', credentials: 'omit', redirect: 'error' });
    if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw new Error('Offline page unavailable');
    const body = await response.text();
    if (!body.includes('data-staff-offline')) throw new Error('Unexpected offline page');
    const cache = await caches.open(CACHE);
    await cache.put(OFFLINE, new Response(body, { headers: { 'Content-Type': 'text/html; charset=utf-8' } }));
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith(CACHE_PREFIX) && key !== CACHE) await caches.delete(key);
    // No clients.claim(): other open tabs keep their lifecycle and are never reloaded.
  })());
});
self.addEventListener('message', event => {
  // Only the registration's own same-origin window may explicitly accept an update.
  if (event.data?.type === 'STAFF_PWA_ACTIVATE' && event.source?.type === 'window' && new URL(event.source.url).origin === self.location.origin) {
    event.waitUntil(self.skipWaiting());
  }
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || request.mode !== 'navigate' || url.origin !== self.location.origin || !employeePage(url.pathname)) return;
  event.respondWith((async () => {
    try { return await fetch(request); } // HTTP errors remain errors; never substitute a login/API response.
    catch {
      const cache = await caches.open(CACHE);
      return await cache.match(OFFLINE) || Response.error();
    }
  })());
});
