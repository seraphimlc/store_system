/* Emergency artifact: publish these bytes at /sw.js (not at this helper URL).
   Keep /sw.js available until installed clients have fetched the replacement. */
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith('staff-pwa-offline-')) await caches.delete(name);
  await self.registration.unregister();
  // No navigation/reload: an open form must remain untouched.
})()));
