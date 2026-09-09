/* The service worker's fetch handling, driven in a stubbed worker environment.
 *
 * offline.js covers what a stale payload may SAY. This covers what the worker
 * DOES, which is where the dangerous failures live: answering a POST from a
 * cache, serving an unrelated API response stale, or returning an empty 200
 * when there is genuinely nothing to show. None of those would be visible in
 * a browser until the day the box is unreachable.
 *
 * All fixture data is synthetic — the repo is public.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "static");

function makeWorker({ online = true, cached = {} } = {}) {
  const store = new Map(Object.entries(cached));
  const seen = { fetched: [], put: [], respondedWith: [] };
  let onPut;
  const putHappened = new Promise((r) => { onPut = r; });
  const listeners = {};

  // The real Headers accepts either a plain object or another Headers. An
  // earlier version of this stub re-wrapped a Headers instance and silently
  // dropped every entry, which failed the stamping test and looked exactly
  // like a bug in sw.js. The stub has to be faithful or it tests itself.
  class H {
    constructor(init) {
      this.m = init instanceof H ? new Map(init.m) : new Map(Object.entries(init || {}));
    }
    get(k) { const v = this.m.get(k); return v === undefined ? null : v; }
    set(k, v) { this.m.set(k, v); }
  }
  class R {
    constructor(body, init) {
      this.body = body;
      this.status = (init && init.status) || 200;
      this.statusText = (init && init.statusText) || "";
      this.headers = new H(init && init.headers);
      this.ok = this.status >= 200 && this.status < 300;
    }
    clone() {
      const c = new R(this.body, { status: this.status, headers: {} });
      this.headers.m.forEach((v, k) => c.headers.set(k, v));
      return c;
    }
    blob() { return Promise.resolve(this.body); }
  }

  const cacheApi = {
    open: (name) => Promise.resolve({
      add: () => Promise.resolve(),
      put: (req, res) => {
        seen.put.push(String(req.url || req));
        store.set(String(req.url || req), res);
        onPut();
        return Promise.resolve();
      },
    }),
    match: (req) => Promise.resolve(store.get(String(req.url || req)) || undefined),
    keys: () => Promise.resolve([]),
    delete: () => Promise.resolve(true),
  };

  const sandbox = {
    self: null, URL, Headers: H, Response: R, console,
    caches: cacheApi,
    importScripts: (p) => {
      const src = fs.readFileSync(path.join(STATIC, path.basename(p)), "utf8");
      vm.runInContext(src, ctx);
    },
    fetch: (req) => {
      seen.fetched.push(String(req.url || req));
      if (!online) return Promise.reject(new Error("offline"));
      return Promise.resolve(new R(JSON.stringify({ live: true }), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    },
    Promise, Date, JSON, Error, Map, Set, Object, Array, String, isNaN,
  };
  sandbox.self = sandbox;
  sandbox.location = { origin: "https://box.local" };
  sandbox.addEventListener = (name, fn) => { listeners[name] = fn; };
  sandbox.skipWaiting = () => Promise.resolve();
  sandbox.clients = { claim: () => Promise.resolve() };

  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(STATIC, "sw.js"), "utf8"), ctx);

  return {
    seen, store, sandbox, putHappened,
    // Drive a fetch event and return what the worker chose to respond with,
    // or undefined when it declined to handle it (= straight to the network).
    async go(url, method = "GET") {
      let responded;
      const e = {
        request: { url, method },
        respondWith: (p) => { responded = p; },
        waitUntil: () => {},
      };
      listeners.fetch(e);
      return responded ? await responded : undefined;
    },
  };
}

const HOME = "https://box.local/api/assistant/home";

test("online, the home payload comes from the network and is cached", async () => {
  const w = makeWorker({ online: true });
  const res = await w.go(HOME);
  assert.equal(res.status, 200);
  assert.ok(w.seen.fetched.includes(HOME), "it did not go to the network");
});

test("offline, the home payload is served from the cache", async () => {
  const w = makeWorker({
    online: false,
    cached: { [HOME]: { status: 200, ok: true, body: '{"cached":true}' } },
  });
  const res = await w.go(HOME);
  assert.equal(res.status, 200, "the phone got nothing to render");
  assert.equal(res.body, '{"cached":true}');
});

test("offline with nothing cached is a visible failure, not an empty dashboard", async () => {
  const w = makeWorker({ online: false });
  const res = await w.go(HOME);
  assert.equal(res.status, 503, "an empty 200 would render as a blank, healthy hub");
  assert.ok(String(res.body).includes("offline"));
});

test("a mutation is never answered by the worker, online or off", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    for (const online of [true, false]) {
      const w = makeWorker({
        online,
        cached: { [HOME]: { status: 200, ok: true, body: "{}" } },
      });
      const res = await w.go(HOME, method);
      assert.equal(res, undefined,
        `${method} while ${online ? "online" : "offline"} was handled by the worker`);
    }
  }
});

test("a mutation to a NON-api path is not handled either", async () => {
  /* This is the case the explicit isMutation guard actually earns its keep on.
     A POST to an /api/ path already falls through on the API rule, so removing
     the guard entirely still passes the test above — mutation testing caught
     that. A POST to a page path would otherwise reach the static-shell branch
     and be answered from, and written to, a cache. */
  const page = "https://box.local/";
  const w = makeWorker({
    online: false,
    cached: { [page]: { status: 200, ok: true, body: "<html>hub</html>" } },
  });
  assert.equal(await w.go(page, "POST"), undefined,
    "a POST was answered from the shell cache");
  assert.equal(w.seen.put.length, 0, "a POST response was written to a cache");
});

test("another API call is never served from the cache", async () => {
  const url = "https://box.local/api/firefly/summary";
  const w = makeWorker({
    online: false,
    cached: { [url]: { status: 200, ok: true, body: '{"stale":true}' } },
  });
  assert.equal(await w.go(url), undefined,
    "a stale money figure was served from a cache");
});

test("a third-party request is left entirely alone", async () => {
  const w = makeWorker({ online: true });
  assert.equal(await w.go("https://accounts.google.com/o/oauth2/v2/auth"), undefined);
});

test("offline, a navigation still gets the cached shell", async () => {
  const page = "https://box.local/";
  const w = makeWorker({
    online: false,
    cached: { [page]: { status: 200, ok: true, body: "<html>hub</html>" } },
  });
  const res = await w.go(page);
  assert.ok(res && String(res.body).includes("hub"), "a white page, which is the bug");
});

test("online, a static asset prefers the network so a deploy is picked up", async () => {
  const js = "https://box.local/home.js";
  const w = makeWorker({
    online: true,
    cached: { [js]: { status: 200, ok: true, body: "OLD" } },
  });
  const res = await w.go(js);
  assert.ok(w.seen.fetched.includes(js));
  assert.notEqual(String(res.body), "OLD", "week-old JS would run against a new page");
});

test("the cached home response is stamped so the page can say how old it is", async () => {
  const w = makeWorker({ online: true });
  await w.go(HOME);
  await w.putHappened;              // deterministic: the stub resolves on put
  const stored = w.store.get(HOME);
  assert.ok(stored, "nothing was cached, so offline would have nothing to show");
  assert.ok(stored.headers.get("X-FC-Cached-At"),
    "without a stamp the page cannot tell live from replayed");
});
