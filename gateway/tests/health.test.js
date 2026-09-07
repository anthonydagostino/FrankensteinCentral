/* PRODUCT_IDEAS #14 — the footer may only claim health it has established. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { summarize, line } = require("../static/health.js");

const up = (k) => ({ key: k, status: "up", detail: {} });
const down = (k) => ({ key: k, status: "down", detail: "boom" });

function agg(list) {
  const out = {};
  for (const e of list) out[e.key] = e;
  return out;
}

const FIFTEEN = ["core", "gmail", "firefly", "budget", "schedule", "stocks",
  "finance", "tasks", "networth", "vault", "deals", "plex", "powerbuy",
  "assistant", "fitness"];

test("all up is healthy, and says how many", () => {
  const s = summarize(agg(FIFTEEN.map(up)));
  assert.strictEqual(s.state, "healthy");
  assert.strictEqual(s.total, 15);
  assert.strictEqual(s.up, 15);
  assert.deepStrictEqual(s.down, []);
  assert.strictEqual(line(s), "● All 15 systems healthy");
});

test("a stopped container is named — idea #14's acceptance signal", () => {
  const s = summarize(agg([...FIFTEEN.filter((k) => k !== "firefly").map(up),
                           down("firefly")]));
  assert.strictEqual(s.state, "degraded");
  assert.deepStrictEqual(s.down, ["firefly"]);
  assert.match(line(s), /firefly/);
});

test("THE BUG: services other than core and gmail must count", () => {
  // Exactly the case the old footer got wrong: core and gmail fine, eleven
  // others down, footer said "Systems healthy".
  const others = FIFTEEN.filter((k) => k !== "core" && k !== "gmail");
  const s = summarize(agg([up("core"), up("gmail"), ...others.map(down)]));
  assert.strictEqual(s.state, "degraded");
  assert.strictEqual(s.down.length, 13);
  assert.ok(!line(s).includes("healthy"), line(s));
});

test("an unreachable gateway is unknown, never healthy", () => {
  for (const bad of [null, undefined, {}, [], "nope", 0]) {
    const s = summarize(bad);
    assert.strictEqual(s.state, "unknown", JSON.stringify(bad));
    assert.strictEqual(line(s), "◔ Couldn't check systems");
  }
});

test("a status that is neither up nor down does not inflate health", () => {
  const s = summarize(agg([up("core"), { key: "gmail", status: "weird" }]));
  assert.strictEqual(s.state, "unknown");
  assert.strictEqual(s.up, 1);
  assert.strictEqual(s.total, 2);
  assert.deepStrictEqual(s.down, []);
});

test("down services are listed in a stable order", () => {
  const s = summarize(agg([down("stocks"), down("budget"), down("assistant")]));
  assert.deepStrictEqual(s.down, ["assistant", "budget", "stocks"]);
});
