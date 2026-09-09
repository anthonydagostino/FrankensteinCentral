/* SCRUM-98 — the page side of the login: a 401 means "go sign in", once,
 * and an unconfigured gateway is said out loud rather than left quiet.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const A = require("../static/auth.js");

const ORIGIN = "http://box.local:8080";

test("only same-origin /api/ paths count, and the status probe never does", () => {
  assert.strictEqual(A.isApi("/api/assistant/home", ORIGIN), true);
  assert.strictEqual(A.isApi(ORIGIN + "/api/core/settings", ORIGIN), true);
  assert.strictEqual(A.isApi("/home.js", ORIGIN), false);
  assert.strictEqual(A.isApi("/", ORIGIN), false);
  assert.strictEqual(A.isApi("https://accounts.google.com/api/x", ORIGIN), false,
    "a 401 from a third party is not our login");
  assert.strictEqual(A.isApi("/api/auth/status", ORIGIN), false,
    "the probe that reports login state must never itself trigger a login");
});

test("needsLogin is exactly: 401 AND our API", () => {
  assert.strictEqual(A.needsLogin({ status: 401 }, "/api/x", ORIGIN), true);
  assert.strictEqual(A.needsLogin({ status: 403 }, "/api/x", ORIGIN), false);
  assert.strictEqual(A.needsLogin({ status: 500 }, "/api/x", ORIGIN), false);
  assert.strictEqual(A.needsLogin({ status: 401 }, "/home.js", ORIGIN), false);
  assert.strictEqual(A.needsLogin(null, "/api/x", ORIGIN), false);
});

test("loginUrl remembers where you were, safely encoded", () => {
  assert.strictEqual(A.loginUrl("/"), "/login?next=%2F");
  assert.strictEqual(A.loginUrl("/a?b=1&c=2"), "/login?next=%2Fa%3Fb%3D1%26c%3D2");
  assert.strictEqual(A.loginUrl(""), "/login?next=%2F");
});

test("the banner speaks only when the gateway says it is unconfigured", () => {
  assert.match(A.bannerText({ configured: false }), /No dashboard password is set/);
  assert.match(A.bannerText({ configured: false }), /GATEWAY_PASSWORD/);
  assert.strictEqual(A.bannerText({ configured: true }), null);
  assert.strictEqual(A.bannerText(null), null, "no status is not 'insecure' — say nothing");
  assert.strictEqual(A.bannerText({}), null);
});

function fakeWindow(responses) {
  // Minimal window: a fetch that returns queued responses, a location that
  // records where it was sent. Enough to drive install() end to end.
  const calls = [];
  const win = {
    location: { href: ORIGIN + "/", origin: ORIGIN, pathname: "/", search: "?fresh=1",
                assign(u) { calls.push(u); } },
    fetch(input) {
      const res = responses.shift() || { status: 200 };
      return Promise.resolve(res);
    },
  };
  win.__assigned = calls;
  return win;
}

test("a 401 from the API sends the page to /login with next set — once", async () => {
  const win = fakeWindow([{ status: 401 }, { status: 401 }, { status: 401 }]);
  A.install(win);
  assert.strictEqual(win.fetch.__fc_wrapped, true);
  await Promise.all([win.fetch("/api/a"), win.fetch("/api/b"), win.fetch("/api/c")]);
  assert.deepStrictEqual(win.__assigned, ["/login?next=%2F%3Ffresh%3D1"],
    "twenty parallel 401s must navigate exactly once");
});

test("a 401 from somewhere that is not our API does nothing", async () => {
  const win = fakeWindow([{ status: 401 }, { status: 401 }]);
  A.install(win);
  await win.fetch("/home.js");
  await win.fetch("https://accounts.google.com/x");
  assert.deepStrictEqual(win.__assigned, []);
});

test("the response is handed back untouched", async () => {
  const marker = { status: 401, body: "as sent" };
  const win = fakeWindow([marker]);
  A.install(win);
  const got = await win.fetch("/api/a");
  assert.strictEqual(got, marker, "callers must see exactly what the gateway sent");
});

test("install is idempotent", () => {
  const win = fakeWindow([]);
  A.install(win);
  const once = win.fetch;
  A.install(win);
  assert.strictEqual(win.fetch, once, "wrapping twice would redirect twice and confuse callers");
});

test("the banner is inserted once, at the top, and is an alert", () => {
  const inserted = [];
  const body = { firstChild: { tag: "existing" },
                 insertBefore(el, ref) { inserted.push([el, ref]); } };
  const doc = {
    body, documentElement: body,
    _ids: {},
    getElementById(id) { return this._ids[id] || null; },
    createElement() {
      const el = { style: {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
      return el;
    },
  };
  A.showBanner(doc, "warning text");
  assert.strictEqual(inserted.length, 1);
  const [el, ref] = inserted[0];
  assert.strictEqual(el.id, "fc-auth-banner");
  assert.strictEqual(el.attrs.role, "alert");
  assert.strictEqual(el.textContent, "warning text");
  assert.strictEqual(ref.tag, "existing", "goes above everything else");
  doc._ids["fc-auth-banner"] = el;
  A.showBanner(doc, "warning text");
  assert.strictEqual(inserted.length, 1, "a second call must not stack a second banner");
  A.showBanner(doc, null);
  assert.strictEqual(inserted.length, 1, "no text, no banner");
});

test("the sign-out control appears only for a real, held session", () => {
  const el = { hidden: true };
  const doc = { getElementById: (id) => (id === "fc-logout" ? el : null) };
  assert.strictEqual(A.showLogout(doc, { configured: true, authenticated: true }), true);
  assert.strictEqual(el.hidden, false);
  assert.strictEqual(A.showLogout(doc, { configured: false, authenticated: true }), false,
    "no password set: a sign-out button would do nothing, so it must not appear");
  assert.strictEqual(el.hidden, true);
  assert.strictEqual(A.showLogout(doc, { configured: true, authenticated: false }), false);
  assert.strictEqual(A.showLogout(doc, null), false);
  assert.strictEqual(A.showLogout({ getElementById: () => null }, { configured: true, authenticated: true }),
    false, "a page without the form (lounge, jobs) is fine — nothing to show");
});
