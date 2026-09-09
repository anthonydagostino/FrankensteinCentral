/* What the dashboard may say when it is running on a cached payload.
 *
 * PRODUCT_IDEAS #7 asks for the hub to open from a phone home screen with the
 * box unreachable. Serving the last /home response makes that work — and
 * immediately creates the failure docs/BUDGETS.md exists to prevent: stale
 * figures rendered as current. "$65 spent today" on the wrong day is not
 * cosmetic staleness, it is a false statement about a number acted on.
 *
 * All fixture data is synthetic — the repo is public.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const Offline = require("../static/offline.js");

const T0 = "2026-09-07T20:00:00.000Z";
const at = (mins) => new Date(Date.parse(T0) + mins * 60000).toISOString();

function payload(over) {
  return Object.assign({
    score: { score: 74, parts: {} },
    do_next: { text: "Head to Acme — starts in 20 min" },
    since: { show: true, changes: [{ text: "1 new email" }] },
    deploy: { running: "abc1234" },
    health: { gym: { week: 2 } },
    money: { today: 65.0, month: 900.0, paycheck: { left: 420 } },
    inbox: { items: [{ id: "m1" }] },
    calendar: [{ id: "e1", title: "Acme", status: "confirmed" }],
    budget: { over_budget: [] },
  }, over || {});
}

// ── what gets cached at all ────────────────────────────────────────────────

test("the home payload is the one API response worth keeping", () => {
  assert.equal(Offline.isCacheableApi("/api/assistant/home", "GET"), true);
  assert.equal(Offline.isCacheableApi("/api/assistant/home?fresh=1", "GET"), true);
});

test("no other API response is cached", () => {
  for (const p of ["/api/health", "/api/core/settings", "/api/firefly/summary",
                   "/api/apps", "/api/core/seen"]) {
    assert.equal(Offline.isCacheableApi(p, "GET"), false, p);
  }
});

test("a mutation is never cacheable, whatever its path", () => {
  for (const m of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.equal(Offline.isCacheableApi("/api/assistant/home", m), false, m);
    assert.equal(Offline.isMutation(m), true, m);
  }
  assert.equal(Offline.isMutation("GET"), false);
});

test("a lookalike path is not the home payload", () => {
  assert.equal(Offline.isCacheableApi("/api/assistant/homework", "GET"), false);
});

// ── how old, in words ──────────────────────────────────────────────────────

test("age is stated in the units a person uses", () => {
  assert.equal(Offline.ageLabel(T0, at(1)), "moments ago");
  assert.equal(Offline.ageLabel(T0, at(20)), "20 min ago");
  assert.equal(Offline.ageLabel(T0, at(180)), "3h ago");
  assert.equal(Offline.ageLabel(T0, at(60 * 48)), "2d ago");
});

test("an unknown age says so rather than defaulting to just now", () => {
  // "just now" is the most reassuring reading of the least information.
  assert.equal(Offline.ageLabel(null, T0), "at an unknown time");
  assert.equal(Offline.ageLabel("not a date", T0), "at an unknown time");
  assert.equal(Offline.ageMinutes("nonsense", T0), null);
});

test("a cache stamped in the future is unknown, not fresh", () => {
  // Clock skew between a phone and the box is ordinary; guessing is not.
  assert.equal(Offline.ageLabel(at(30), T0), "at an unknown time");
});

// ── what a stale payload may claim ─────────────────────────────────────────

test("every volatile field is suppressed, not shown stale", () => {
  const v = Offline.staleView(payload(), T0, at(600));
  for (const k of ["score", "do_next", "since", "deploy", "health"]) {
    assert.equal(k in v, false, `${k} survived into the stale view`);
  }
});

test("a stale do_next can never say to head somewhere in 20 minutes", () => {
  // The worst one: acted on immediately, and wrong the moment it is old.
  const v = Offline.staleView(payload(), T0, at(600));
  assert.equal(JSON.stringify(v).includes("starts in 20 min"), false);
});

test("spent-today is blanked while the month figure survives", () => {
  const v = Offline.staleView(payload(), T0, at(600));
  assert.equal(v.money.today, null, "a stale 'spent today' is a false number");
  assert.equal(v.money.month, 900.0, "the month total is still broadly true");
  assert.deepEqual(v.money.paycheck, { left: 420 });
});

test("durable content is kept, because hiding it helps nobody", () => {
  const v = Offline.staleView(payload(), T0, at(600));
  assert.deepEqual(v.inbox, { items: [{ id: "m1" }] });
  assert.equal(v.calendar.length, 1);
});

test("the stale view is always labelled with when it was captured", () => {
  const v = Offline.staleView(payload(), T0, at(180));
  assert.equal(v.offline.stale, true);
  assert.equal(v.offline.cached_at, T0);
  assert.equal(v.offline.age_label, "3h ago");
  assert.ok(v.offline.suppressed.includes("score"));
  assert.ok(v.offline.suppressed.includes("money.today"));
});

test("the banner says both that it is stale and how stale", () => {
  const v = Offline.staleView(payload(), T0, at(180));
  const b = Offline.banner(v.offline);
  assert.ok(b.includes("Offline"), b);
  assert.ok(b.includes("3h ago"), b);
});

test("a live payload gets no banner", () => {
  assert.equal(Offline.banner(undefined), "");
  assert.equal(Offline.banner({ stale: false }), "");
});

test("the original payload is never mutated", () => {
  const p = payload();
  const before = JSON.stringify(p);
  Offline.staleView(p, T0, at(600));
  assert.equal(JSON.stringify(p), before, "staleView mutated its input");
});

test("a malformed cached payload degrades to a labelled empty view", () => {
  for (const bad of [null, undefined, "a string", 42]) {
    const v = Offline.staleView(bad, T0, at(60));
    assert.equal(v.offline.stale, true);
  }
});

test("a payload with no money section does not invent one", () => {
  const v = Offline.staleView({ inbox: { items: [] } }, T0, at(60));
  assert.equal("money" in v, false);
});

// ── the acceptance signal, as a single walkthrough ─────────────────────────

test("the box is unreachable and the phone still renders something true", () => {
  // Captured last night; opened this morning from the home screen.
  const cached = payload();
  const v = Offline.staleView(cached, T0, at(60 * 11));
  // It renders...
  assert.ok(v.inbox, "nothing was rendered at all");
  assert.ok(v.calendar);
  // ...it says it is old...
  assert.ok(Offline.banner(v.offline).includes("11h ago"));
  // ...and it makes no claim that depends on the moment.
  const blob = JSON.stringify(v);
  assert.equal(blob.includes("starts in 20 min"), false);
  assert.equal(v.money.today, null);
  assert.equal("score" in v, false);
});
