// Cauldra Service Worker — app-shell + static-asset caching only.
//
// This worker is deliberately narrow in scope: it exists so the page itself
// (index.html) and its static vendor assets can still load when there is no
// network connection. It does NOT cache API responses — those are handled by
// the page's own IndexedDB-backed offline layer (see the OfflineDB/SyncEngine
// code in index.html), which already respects authentication/business
// boundaries and cache-eviction rules the Cache API knows nothing about.
// Caching private API responses here would risk serving one signed-in user's
// data to whoever opens the browser next, so every request that isn't the
// app shell or a known static asset is passed straight to the network.

// Bumped for the permanent startup/reachability fix (state machine +
// bounded timeouts in app.js). Every deploy that changes app shell code
// (app.js, payments.js, base.css, etc.) MUST bump this string — activate()
// below deletes every cache whose name doesn't match the current one, which
// is the only thing standing between a fixed startup bug and a browser
// silently keeping the old broken app.js around after the fix ships.
const SHELL_CACHE = "cauldra-shell-v8-offline-first";

// Precached at install time. Kept small and static-only — anything dynamic
// (products, sales, etc.) never belongs in this cache.
//
// SHELL_DOCUMENTS are the app's own code — not versioned by filename, so
// they're treated like "/" itself (network-first with a cached fallback,
// see isShellDocument()/the fetch handler below): always fresh when online,
// fully usable offline otherwise. SHELL_ASSETS are third-party/vendor files
// already versioned by filename, so they stay cache-first below.
const SHELL_DOCUMENTS = [
    "/js/app.js",
    "/js/payments.js",
    "/js/offline.js",
    "/css/payments.css",
    "/css/offline.css",
    "/js/heartbeat.js",
    "/css/base.css",
    "/css/dashboard-fixes.css",
    "/frontend/css/base.css",
];
const SHELL_ASSETS = [
    "/",
    "/assets/manifest.json",
    "/assets/favicon-32.png",
    "/assets/favicon-16.png",
    "/assets/apple-touch-icon.png",
    "/assets/icon-192.png",
    "/assets/icon-512.png",
    "/assets/vendor/tailwindcss-3.4.17.js",
    "/assets/vendor/fontawesome/css/all.min.css",
    "/assets/vendor/fontawesome/webfonts/fa-solid-900.woff2",
    "/assets/vendor/fontawesome/webfonts/fa-regular-400.woff2",
    "/assets/vendor/fontawesome/webfonts/fa-brands-400.woff2",
    "/assets/vendor/html5-qrcode-2.3.8.min.js",
    "/assets/vendor/zxing.umd.js",
    ...SHELL_DOCUMENTS,
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE).then((cache) =>
            // Best-effort: a single missing/renamed asset must not abort
            // install and leave the whole shell uncached.
            Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(url)))
        ).then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(names.filter((n) => n !== SHELL_CACHE).map((n) => caches.delete(n)))
        ).then(() => self.clients.claim())
    );
});

function isStaticAsset(url) {
    return url.pathname.startsWith("/assets/");
}

function isShellDocument(url) {
    return SHELL_DOCUMENTS.includes(url.pathname);
}

// Every non-GET request, and every GET that isn't the shell/a static asset/
// one of the app's own shell documents, is assumed to be a live API call and
// must go straight to the network — never served from or written into this
// cache.
function isApiRequest(request, url) {
    if (request.method !== "GET") return true;
    if (url.pathname === "/" || isStaticAsset(url) || isShellDocument(url)) return false;
    return true;
}

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return; // let POST/PUT/PATCH/DELETE pass through untouched
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return; // never intercept third-party requests

    if (isApiRequest(request, url)) return; // network-only, not our concern

    if (url.pathname === "/" || request.mode === "navigate" || isShellDocument(url)) {
        // Network-first for the shell itself (the HTML document, and the
        // app's own JS/CSS — see SHELL_DOCUMENTS), so signed-in users get the
        // latest app on every load while still having a cached fallback the
        // moment the network is unavailable.
        event.respondWith(
            fetch(request)
                .then((response) => {
                    const copy = response.clone();
                    caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
                    return response;
                })
                .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
        );
        return;
    }

    if (isStaticAsset(url)) {
        // Cache-first for static vendor assets — they're versioned by
        // filename already, so a stale cache entry is not a practical concern.
        event.respondWith(
            caches.match(request).then((cached) => {
                if (cached) return cached;
                return fetch(request).then((response) => {
                    const copy = response.clone();
                    caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
                    return response;
                });
            })
        );
    }
});

// -----------------------------------------------------------------------------
// EXTERNAL PUSH NOTIFICATIONS
//
// This is the ONE place in the whole app that knows anything about the Web
// Push wire format — main.py's notification engine (create_notification() /
// deliver_push_notification()) decides WHETHER and to WHOM a push goes out;
// this worker's only job is displaying whatever payload it's handed and
// routing a click back into the already-open (or newly opened) app at the
// notification's deep_link. See index.html's subscribeToPushNotifications()
// for how a subscription lands in main.py's push_subscriptions table in the
// first place.
// -----------------------------------------------------------------------------
self.addEventListener("push", (event) => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
    const title = data.title || "Cauldra";
    const options = {
        body: data.body || "",
        icon: "/assets/icon-192.png",
        badge: "/assets/icon-192.png",
        tag: data.notification_id ? `cauldra-notification-${data.notification_id}` : undefined, // a re-delivered/duplicate push for the same notification replaces the prior one instead of stacking
        data: { deep_link: data.deep_link || null, notification_id: data.notification_id || null },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const deepLink = (event.notification.data && event.notification.data.deep_link) || null;
    const targetUrl = deepLink ? `/?notification_deep_link=${encodeURIComponent(deepLink)}` : "/";
    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
            // Reuse an already-open Cauldra tab rather than opening a new one
            // whenever possible — post the deep link to it directly instead
            // of relying on it to parse its own URL, since it's already loaded.
            for (const client of clientList) {
                if ("focus" in client) {
                    client.postMessage({ type: "cauldra-notification-click", deep_link: deepLink });
                    return client.focus();
                }
            }
            if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
        })
    );
});
