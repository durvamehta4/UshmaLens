// Service worker for the field view. Field workers are often on patchy 2G, so
// the app keeps working offline: API reads and pages are network-first with a
// cached fallback, so the last advisory a worker loaded is still there when
// the signal drops.
//
// Only content-hashed build assets (/assets/*) are served cache-first -- their
// file names change on every build, so a cached copy can never be stale. Pages
// must NOT be cache-first: an earlier version served "/" from cache forever,
// pinning users to an old build that referenced old asset hashes.

const VERSION = "v2";
const SHELL = `ushmalens-shell-${VERSION}`;
const DATA = `ushmalens-data-${VERSION}`;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/icon.svg", "/manifest.webmanifest"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

const networkFirst = (req, cacheName) =>
  fetch(req)
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(cacheName).then((c) => c.put(req, copy));
      }
      return res;
    })
    .catch(() => caches.match(req).then((hit) => hit ?? caches.match("/")));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/stream")) return;

  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(
      caches.match(e.request).then((hit) =>
        hit ??
        fetch(e.request).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put(e.request, copy));
          }
          return res;
        }),
      ),
    );
    return;
  }
  e.respondWith(networkFirst(e.request, url.pathname.startsWith("/api/") ? DATA : SHELL));
});
