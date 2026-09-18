/* The week grid must stay a card, not a page.
 *
 * Anthony, 2026-09-18: "could we now make the calendar not as fucking big and
 * long? i still want it to be visible but jeez like not taking up the whole
 * page."
 *
 * Two things made it tall, and only one of them was the CSS. Each column had
 * `min-height: 250px`, and — the real culprit — a day rendered EVERY event it
 * had, so one busy Tuesday set the height of all seven columns at once. A
 * floor can be lowered; an uncapped list cannot. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const JS_SRC = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
/* Comments stripped: these assert on declarations, and a comment that quotes
   the property it explains matches the same regex as the rule. */
const RULES = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

test("a day column has a floor, and it is a modest one", () => {
  const rule = RULES.match(/\.wk-day \{[^}]*\}/);
  assert.ok(rule, ".wk-day has no rule");
  const min = rule[0].match(/min-height:\s*(\d+)px/);
  assert.ok(min, "the column lost its floor entirely");
  assert.ok(Number(min[1]) <= 160,
    `a ${min[1]}px floor across seven columns is a page, not a card`);
});

test("a day shows a bounded number of events", () => {
  /* The height fix that CSS cannot make. Without this, the tallest day in the
   * week decides how tall the whole grid is. */
  assert.match(JS_SRC, /const DAY_EVENTS_SHOWN = (\d+);/);
  const n = Number(JS_SRC.match(/const DAY_EVENTS_SHOWN = (\d+);/)[1]);
  assert.ok(n >= 2 && n <= 5, `${n} events per column is not a cap worth having`);
  assert.match(JS_SRC, /\.slice\(0, DAY_EVENTS_SHOWN\)/);
});

test("the events beyond the cap are counted, never silently dropped", () => {
  /* A calendar that quietly hides an appointment is worse than a tall one. */
  assert.match(JS_SRC, /hiddenEvs = \(day\.events \|\| \[\]\)\.length - shownEvs\.length/);
  assert.match(JS_SRC, /hiddenEvs > 0/);
  assert.match(JS_SRC, /\+\$\{hiddenEvs\} more/);
});

test("the count is of the real list, not of the capped one", () => {
  /* `shownEvs.length - DAY_EVENTS_SHOWN` would always be 0 and the badge would
   * never appear — the failure mode that looks like it works. */
  assert.ok(!/hiddenEvs = shownEvs\.length/.test(JS_SRC));
});

test("+N more opens the schedule instead of expanding the column", () => {
  /* Expanding in place would re-create the height problem the cap exists to
   * solve. */
  const wire = JS_SRC.slice(JS_SRC.indexOf("function wireWeek()"));
  assert.match(wire, /\.wk-more/);
  assert.match(wire, /openAppKey\("schedule"\)/);
  assert.match(wire, /stopPropagation/, "the click also triggers the day card");
});

test("the more button is styled, so it does not read as a broken chip", () => {
  assert.match(RULES, /\.wk-more \{/);
});

test("a clear day still says Clear rather than showing nothing", () => {
  assert.match(JS_SRC, /wk-clear">Clear/);
});
