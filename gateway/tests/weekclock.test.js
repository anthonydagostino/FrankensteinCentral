/* Sweeps the browser-side midnight rollover across a calendar, in the same
 * spirit as the Python date sweeps: never against "today".
 *
 * Run by scripts/test.sh via `node --test`. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");

process.env.TZ = "America/New_York";  // the deployment's LOCAL_TZ
const { msUntilNextMidnight, GRACE_MS } = require("../static/weekclock.js");

const HOUR = 3600000;

function sweep(fn) {
  // 800 days from 2026-01-01, four times a day, including the minute either
  // side of midnight where an off-by-an-hour is actually observable.
  const start = new Date(2026, 0, 1);
  for (let d = 0; d < 800; d++) {
    for (const [h, m] of [[0, 1], [9, 0], [17, 30], [23, 59]]) {
      const now = new Date(start.getFullYear(), start.getMonth(),
                           start.getDate() + d, h, m, 0, 0);
      fn(now);
    }
  }
}

test("always lands on the next calendar day, never the same one", () => {
  sweep((now) => {
    const fired = new Date(now.getTime() + msUntilNextMidnight(now));
    assert.notStrictEqual(fired.getDate(), now.getDate(),
      `stayed on ${now} -> ${fired}`);
    const expected = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    assert.strictEqual(fired.getFullYear(), expected.getFullYear(), String(now));
    assert.strictEqual(fired.getMonth(), expected.getMonth(), String(now));
    assert.strictEqual(fired.getDate(), expected.getDate(), String(now));
  });
});

test("fires just after midnight, never before it", () => {
  sweep((now) => {
    const fired = new Date(now.getTime() + msUntilNextMidnight(now));
    assert.ok(fired.getHours() === 0, `fired at ${fired.getHours()}h from ${now}`);
    assert.ok(fired.getTime() > now.getTime(), String(now));
    // STRICTLY past the boundary, so the server's date has certainly moved.
    // Asserted against a literal, not against GRACE_MS: comparing the output
    // to the very constant that produced it cannot fail, which makes it
    // decoration rather than a test.
    assert.ok(fired.getSeconds() > 0, `fired exactly on the boundary at ${now}`);
    assert.ok(fired.getSeconds() < 60, `grace margin too large at ${now}`);
  });
});

test("the delay is always positive and never more than a long day", () => {
  sweep((now) => {
    const ms = msUntilNextMidnight(now);
    assert.ok(ms > 0, `non-positive delay at ${now}`);
    // 25h covers the fall-back day; anything longer means we skipped a day.
    assert.ok(ms <= 25 * HOUR + GRACE_MS, `${ms}ms at ${now}`);
  });
});

test("DST days are still one day, not 23 or 25 hours of drift", () => {
  // 2026-03-08 is 23h long, 2026-11-01 is 25h long in America/New_York.
  for (const [y, mo, d] of [[2026, 2, 7], [2026, 9, 31], [2027, 2, 13], [2027, 10, 6]]) {
    for (let h = 0; h < 24; h++) {
      const now = new Date(y, mo, d, h, 30);
      const fired = new Date(now.getTime() + msUntilNextMidnight(now));
      const expected = new Date(y, mo, d + 1);
      assert.strictEqual(fired.getDate(), expected.getDate(),
        `${now} -> ${fired}`);
      assert.strictEqual(fired.getHours(), 0, `${now} -> ${fired}`);
    }
  }
});

test("month, year and leap-day boundaries roll over correctly", () => {
  const cases = [
    [new Date(2026, 0, 31, 23, 0), [2026, 1, 1]],   // Jan 31 -> Feb 1
    [new Date(2026, 11, 31, 23, 0), [2027, 0, 1]],  // New Year
    [new Date(2028, 1, 28, 23, 0), [2028, 1, 29]],  // into the leap day
    [new Date(2028, 1, 29, 23, 0), [2028, 2, 1]],   // leap day -> 1 March
    [new Date(2027, 1, 28, 23, 0), [2027, 2, 1]],   // non-leap Feb
  ];
  for (const [now, [y, mo, d]] of cases) {
    const fired = new Date(now.getTime() + msUntilNextMidnight(now));
    assert.strictEqual(fired.getFullYear(), y, String(now));
    assert.strictEqual(fired.getMonth(), mo, String(now));
    assert.strictEqual(fired.getDate(), d, String(now));
  }
});
