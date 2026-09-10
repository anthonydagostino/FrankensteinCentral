/* Service worker: the hub opens from the phone's home screen and renders
 * something true even with the box unreachable (PRODUCT_IDEAS #7).
 *
 * SCOPE, deliberately narrow. It caches the app shell and the last /home
 * response. It does NOT cache mutations, does not retry them, and does not
 * queue them for later: a "logged" toast for a workout that was never written
 * is worse than an error you can see.
 *
 * The decisions about what a cached payload may claim live in offline.js so
 * they can be unit-tested; this file is the plumbing.
 *
 * NOTE ON HTTPS. A service worker only registers in a secure context, so on
 * the LAN over plain HTTP this file is inert until Tailscale (SCRUM-48) puts
 * the hub behind HTTPS. That is a real dependency and home.js reports it
 * rather than pretending registration succeeded.
 */
"use strict";
importScripts("/offline.js");

var VERSION = "fc-v1";
var SHELL = VERSION + "-shell";
var DATA = VERSION + "-data";

// Everything needed to paint the page with no network at all.
var SHELL_FILES = [
  "/", "/index.html", "/styles.css", "/home.css",
  "/auth.js", "/app.js", "/home.js", "/offline.js", "/donut.js", "/weekclock.js",
  "/manifest.webmanifest", "/icon.svg",
];

self.addEventListener("install", function (e) {
  // addAll rejects the whole install if ANY file 404s, which would leave the
  // worker permanently uninstalled and the failure invisible. Cache them
  // individually and let a missing optional asset through.
  e.waitUntil(caches.open(SHELL).then(function (c) {
    return Promise.all(SHELL_FILES.map(function (f) {
      return c.add(f).catch(function () { return null; });
    }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.map(function (k) {
      return k.indexOf(VERSION) === 0 ? null : caches.delete(k);
    }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;

  // A mutation goes to the network or it fails, visibly. Never cached, never
  // replayed, never answered from a cache.
  if (self.Offline.isMutation(req.method)) return;

  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // never touch third parties

  // The home payload: network first so a reachable box always wins, cache
  // only as the fallback, and the cached copy is stamped with when it was
  // taken so the page can say how old it is.
  if (self.Offline.isCacheableApi(url.pathname, req.method)) {
    e.respondWith(
      fetch(req).then(function (res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(DATA).then(function (c) {
            return copy.blob().then(function (body) {
              var headers = new Headers(copy.headers);
              headers.set("X-FC-Cached-At", new Date().toISOString());
              return c.put(req, new Response(body, {
                status: copy.status, statusText: copy.statusText, headers: headers,
              }));
            });
          });
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          // No network AND nothing cached is a real failure, and is reported
          // as one rather than as an empty dashboard.
          return hit || new Response(
            JSON.stringify({ error: "offline", cached: false }),
            { status: 503, headers: { "Content-Type": "application/json" } });
        });
      })
    );
    return;
  }

  // Any other API call: network only. An unknown endpoint served from a stale
  // cache is exactly the class of lie this whole change is trying to avoid.
  if (url.pathname.indexOf("/api/") === 0) return;

  // Static shell: network first so a deploy is picked up (the gateway sends
  // Cache-Control: no-cache for precisely this reason), cache as the fallback.
  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && req.method === "GET") {
        var copy = res.clone();
        caches.open(SHELL).then(function (c) { c.put(req, copy); });
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (hit) {
        return hit || caches.match("/index.html");
      });
    })
  );
});
