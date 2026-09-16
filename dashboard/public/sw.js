// sw.js — KILL SWITCH (orchestraos-killswitch-1)
//
// History: earlier versions cached the app shell. Combined with dashboard-proxy
// serving sw.js as `max-age=31536000, immutable`, browsers froze a stale service
// worker that served a cached shell while live /api/* calls never resolved — the
// "Agents / Loading… forever, survives refresh" bug. This worker intercepts NOTHING
// (no fetch handler => all requests go straight to the network), wipes every cache,
// and unregisters itself, healing any stuck client. It reloads each window exactly
// once (guarded by ?swhealed=1) so re-registration by an old bundle can't loop.

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 1. Drop all caches from previous SW versions.
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));

    // 2. Remove this registration entirely.
    try { await self.registration.unregister(); } catch (e) { /* noop */ }

    // 3. Take control, then reload each window ONCE to detach from the dead SW.
    await self.clients.claim();
    const clients = await self.clients.matchAll({ type: 'window' });
    for (const c of clients) {
      if (!c.url.includes('swhealed=1')) {
        const u = new URL(c.url);
        u.searchParams.set('swhealed', '1');
        c.navigate(u.toString());
      }
    }
  })());
});

// No 'fetch' listener on purpose: this SW does not intercept any request.
