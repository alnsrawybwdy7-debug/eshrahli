/* اشرحلي service worker.
   - Pages are never cached (they hold per-user data and form tokens): always from the network,
     with a friendly offline page when there is no connection.
   - /static/ files are versioned (?v=...), so they are cached and reused safely.
   - Videos, uploads, live calls and other sites are not touched at all. */
"use strict";
var VERSION = "v1";
var STATIC_CACHE = "eshrahli-static-" + VERSION;
var OFFLINE_URL = "/offline";
var MAX_STATIC = 80;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(function (cache) { return cache.add(new Request(OFFLINE_URL, { cache: "reload" })); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== STATIC_CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function trim(cache) {
  return cache.keys().then(function (keys) {
    if (keys.length <= MAX_STATIC) return;
    return Promise.all(keys.slice(0, keys.length - MAX_STATIC).map(function (k) {
      return new URL(k.url).pathname === OFFLINE_URL ? null : cache.delete(k);
    }));
  });
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(function () {
      return caches.match(OFFLINE_URL);
    }));
    return;
  }

  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(caches.open(STATIC_CACHE).then(function (cache) {
      return cache.match(req).then(function (hit) {
        if (hit) return hit;
        return fetch(req).then(function (res) {
          if (res.ok && res.type === "basic") {
            cache.put(req, res.clone()).then(function () { return trim(cache); });
          }
          return res;
        });
      });
    }));
  }
});
