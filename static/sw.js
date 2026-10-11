
const CACHE_VERSION = "rrb-alp-v3";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGE_CACHE = `${CACHE_VERSION}-pages`;

const STATIC_ASSETS = [
  "/static/manifest.json",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
  "/static/css/style.css"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(async (cache) => {
      await Promise.all(
        STATIC_ASSETS.map(async (url) => {
          try {
            const response = await fetch(url, { cache: "reload" });
            if (response.ok) await cache.put(url, response);
          } catch (_) {
            // A missing asset must not block installation.
          }
        })
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) =>
            key.startsWith("rrb-alp-") &&
            ![STATIC_CACHE, PAGE_CACHE].includes(key)
          )
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache authentication, reset links, APIs, or student-specific pages.
  if (
    /^\/(login|register|logout|forgot-password|reset-password)(\/|$)/.test(url.pathname) ||
    url.pathname.startsWith("/api/") ||
    url.searchParams.has("token")
  ) {
    return;
  }

  // Static assets: use cache first, then network.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(async (cached) => {
        if (cached) return cached;

        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(STATIC_CACHE);
          await cache.put(request, response.clone());
        }
        return response;
      })
    );
    return;
  }

  // Never cache HTML pages or personalized content.
  if (request.mode === "navigate" || request.destination === "document") {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(
          "<!doctype html><html><meta name='viewport' content='width=device-width,initial-scale=1'><title>Offline</title><body><h2>You are offline</h2><p>Reconnect to the internet to load RRB ALP practice pages.</p></body></html>",
          {
            status: 503,
            headers: { "Content-Type": "text/html; charset=utf-8" }
          }
        )
      )
    );
  }
});
