/* PRODUCT_IDEAS #24 — the footer may only claim a build it has established.
 *
 * The state that matters is `failed`: the box keeps serving the previous
 * build, so every other signal on the page looks perfect while the code you
 * are reading is not the code that is running. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { describe, ago } = require("../static/deploy.js");

const FAILED = {
  state: "failed",
  running: "e7adf8300000000000000000000000000000000a",
  attempted: "be294dd4a2ad192f6d3364949a346abd13a316a9",
  last_result: "tests_failed",
  age_seconds: 3 * 86400,
  attempt_age_seconds: 300,
};

test("a failed deploy is loud and names the build you are actually on", () => {
  const v = describe(FAILED);
  assert.strictEqual(v.tone, "bad");
  assert.match(v.text, /failed/i);
  assert.match(v.text, /e7adf83/, "must name the RUNNING build");
  assert.match(v.title, /be294dd/, "and the one that failed to replace it");
  assert.match(v.title, /NOT the latest/);
});

test("a current deploy names the running build and its age", () => {
  const v = describe({ state: "current", running: "be294dd4a2ad1", attempted: "be294dd4a2ad1",
                       last_result: "success", age_seconds: 7200 });
  assert.strictEqual(v.tone, "ok");
  assert.match(v.text, /be294dd/);
  assert.match(v.text, /2h ago/);
});

for (const bad of [null, undefined, {}, "nope", 42, [], { state: "" }, { state: "weird" }]) {
  test(`a payload we cannot read is unknown, never ok: ${JSON.stringify(bad)}`, () => {
    const v = describe(bad);
    assert.strictEqual(v.tone, "unknown");
    assert.notStrictEqual(v.tone, "ok");
    assert.match(v.text, /unknown/i);
  });
}

test("pending is not ok and does not claim containers are down", () => {
  const v = describe({ state: "pending", running: null, attempted: "abc1234",
                       last_result: "success" });
  assert.notStrictEqual(v.tone, "ok");
  assert.match(v.title, /says nothing about whether containers are up/i);
});

test("an unknown age is omitted, never rendered as 'just now'", () => {
  for (const s of [null, undefined, NaN, Infinity, "600", {}]) {
    assert.strictEqual(ago(s), null, `ago(${String(s)})`);
  }
  const v = describe({ state: "current", running: "abc1234", attempted: "abc1234",
                       last_result: "success", age_seconds: null });
  assert.ok(!/just now/.test(v.text), "must not invent recency: " + v.text);
  assert.ok(!/ago/.test(v.text), "no age claim at all: " + v.text);
});

test("a negative age is unknown, not a deploy in the future", () => {
  assert.strictEqual(ago(-1), null);
  assert.strictEqual(ago(-86400), null);
});

test("ago spans the units it claims", () => {
  assert.strictEqual(ago(0), "just now");
  assert.strictEqual(ago(59), "just now");
  assert.strictEqual(ago(60), "1m ago");
  assert.strictEqual(ago(3599), "59m ago");
  assert.strictEqual(ago(3600), "1h ago");
  assert.strictEqual(ago(86399), "23h ago");
  assert.strictEqual(ago(86400), "1d ago");
  assert.strictEqual(ago(9 * 86400), "9d ago");
});

test("a failed deploy still reads as failed when the running build is unknown", () => {
  /* deploy.sh can record a failure before any success was ever confirmed.
   * That is still a failure, and must not soften into `unknown`. */
  const v = describe({ ...FAILED, running: null, age_seconds: null });
  assert.strictEqual(v.tone, "bad");
  assert.match(v.text, /failed/i);
});

test("commits are shortened for display but never invented", () => {
  const v = describe({ state: "current", running: "0123456789abcdef",
                       attempted: "0123456789abcdef", last_result: "success" });
  assert.match(v.text, /0123456/);
  assert.ok(!/0123456789/.test(v.text), "should be short, not the full sha");
});
