/* Where the job-hunt research lives (SCRUM-131).
 *
 * The page saved to localStorage and said "Saved just now (this browser)" the
 * instant setItem returned. The data now lives in core; these hold the
 * decisions the page makes around that — which keys are ours to migrate, what
 * a first load does with a leftover local copy, and what the saved stamp may
 * claim.
 *
 * All fixture data is synthetic — the repo is public.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const J = require("../static/jobhunt.js");

// A minimal Storage: length, key(i), getItem(k). Node has no localStorage.
function storage(obj) {
  const keys = Object.keys(obj);
  return { length: keys.length, key: (i) => keys[i],
           getItem: (k) => (k in obj ? obj[k] : null) };
}

test("only jobhunt_* keys are ours; the hub's own keys are left alone", () => {
  const got = J.localEntries(storage({
    "jobhunt_rank_weight_pay": "3",
    "jobhunt_acme_pros": '["a"]',
    "cc_theme": "dark",            // the hub's
    "cc_snooze_x": "1",            // the hub's
    "JOBHUNT_acme_cons": "x",      // wrong case is not ours
  }));
  assert.deepStrictEqual(got, { "jobhunt_rank_weight_pay": "3", "jobhunt_acme_pros": '["a"]' });
});

test("a missing or malformed storage yields nothing rather than throwing", () => {
  assert.deepStrictEqual(J.localEntries(null), {});
  assert.deepStrictEqual(J.localEntries({}), {});
});

test("first load with nothing local does nothing", () => {
  assert.deepStrictEqual(J.migrationPlan({ "jobhunt_a_pros": "x" }, {}), { action: "none" });
  assert.deepStrictEqual(J.migrationPlan({}, {}), { action: "none" });
});

test("THE MIGRATION: local data and an empty server is imported, never discarded", () => {
  const local = { "jobhunt_acme_notes": "call back Thu", "jobhunt_rank_weight_pay": "3" };
  const plan = J.migrationPlan({}, local);
  assert.strictEqual(plan.action, "import");
  assert.deepStrictEqual(plan.entries, local);
});

test("a local copy identical to the server is a leftover and is cleared", () => {
  const same = { "jobhunt_acme_notes": "x" };
  assert.deepStrictEqual(J.migrationPlan(same, { ...same }), { action: "clear" });
});

test("a local subset of the server is clear, not ask — nothing would be lost", () => {
  const server = { "jobhunt_a_pros": "x", "jobhunt_b_pros": "y" };
  assert.deepStrictEqual(J.migrationPlan(server, { "jobhunt_a_pros": "x" }), { action: "clear" });
});

test("a local copy that DIFFERS from a non-empty server is never merged silently", () => {
  const plan = J.migrationPlan(
    { "jobhunt_acme_notes": "server version" },
    { "jobhunt_acme_notes": "this browser's version", "jobhunt_acme_floor": "1" });
  assert.strictEqual(plan.action, "ask");
  assert.strictEqual(plan.local, 2);
  assert.strictEqual(plan.server, 1);
});

test("one differing value among many matching ones is still ask", () => {
  const server = { "jobhunt_a_pros": "x", "jobhunt_b_pros": "y" };
  const local = { "jobhunt_a_pros": "x", "jobhunt_b_pros": "DIFFERENT" };
  assert.strictEqual(J.migrationPlan(server, local).action, "ask");
});

test("the saved stamp only says saved when the server said so", () => {
  assert.match(J.savedLabel({ status: "saved" }), /^Saved/);
  assert.doesNotMatch(J.savedLabel({ status: "saving" }), /^Saved/);
  assert.doesNotMatch(J.savedLabel({ status: "failed" }), /^Saved/);
  assert.match(J.savedLabel({ status: "failed" }), /NOT SAVED/);
  assert.strictEqual(J.savedLabel(null), "Not edited yet");
  assert.strictEqual(J.savedLabel({ status: "idle" }), "Not edited yet");
});

test("the stamp never claims the browser as the place it was saved", () => {
  for (const s of ["saved", "saving", "failed", "idle"]) {
    assert.doesNotMatch(J.savedLabel({ status: s }), /this browser/i);
  }
});

test("an unreachable server is named, and the defaults are labelled as defaults", () => {
  const b = J.unreachableBanner();
  assert.match(b, /Could not reach the server/);
  assert.match(b, /NOT shown/);
  assert.match(b, /not be saved/);
});

test("a failed import says the local copy was kept", () => {
  assert.match(J.importFailedBanner(), /kept here/);
});

test("the ask banner names both counts and says it will not merge", () => {
  const b = J.askBanner({ action: "ask", local: 4, server: 9 });
  assert.match(b, /4 saved fields/);
  assert.match(b, /\(9\)/);
  assert.match(b, /not be merged/);
});
