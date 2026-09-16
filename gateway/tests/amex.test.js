/* The Amex credits card: what it refuses to say.
 *
 * SCRUM-141. These credits expire on a calendar boundary and do not roll
 * over, so every failure mode of this card is the same failure: showing a
 * number that is not true, on a screen glanced at rather than read.
 *
 * WHAT THIS CAN AND CANNOT PROVE. `home.js` is an IIFE with no export, so
 * these are assertions about its source rather than about a rendered DOM.
 * They catch the specific regressions that would make the card lie — a
 * falsy check where a null check belongs, a figure printed on the
 * unreachable path — and they cannot catch a layout mistake. That is worth
 * having: both of those regressions are one character wide. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const JS_SRC = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");

const renderAmex = (() => {
  const start = JS_SRC.indexOf("function renderAmex(");
  assert.ok(start > -1, "renderAmex is gone");
  /* To the next top-level `function ` at the same indentation. */
  const end = JS_SRC.indexOf("\n  function ", start + 1);
  return JS_SRC.slice(start, end > -1 ? end : JS_SRC.length);
})();

test("the card has somewhere to render, and a consumer calls it", () => {
  assert.match(HTML, /id="cc-amex"/);
  assert.match(JS_SRC, /renderAmex\(d\.amex\)/);
});

test("an unreachable service prints no figure at all", () => {
  /* The one that costs money. A card that quietly reads $0 on the 31st is
   * indistinguishable from a card with nothing due, and one of those is a
   * credit dying at midnight. The unreachable branch must return before any
   * amount is interpolated. */
  const guard = renderAmex.indexOf('a.state !== "ok"');
  assert.ok(guard > -1, "the unreachable branch is gone");
  const ret = renderAmex.indexOf("return;", guard);
  assert.ok(ret > guard, "the unreachable branch no longer returns early");
  const branch = renderAmex.slice(guard, ret);
  for (const field of ["at_risk", "available", "ytd_net", "ytd_captured"]) {
    assert.ok(!branch.includes(field),
      `the unreachable branch interpolates ${field}, which it was not given`);
  }
});

test("the year-to-date line is gated on null, never on truthiness", () => {
  /* `!a.ytd_net` hides break-even. Having drawn exactly your fees back is a
   * real and interesting answer; "we have no figure" is a different one, and
   * collapsing them is the mistake docs/BUDGETS.md is written about. */
  assert.match(renderAmex, /a\.ytd_net\s*==\s*null/,
    "the ytd line must be gated on `== null`, so a net of 0 still renders");
  assert.ok(!/if\s*\(\s*!\s*a\.ytd_net/.test(renderAmex),
    "a falsy check here silently hides a net of exactly zero");
});

test("ahead and behind are told apart by a word, not only by a colour", () => {
  assert.match(renderAmex, /ahead by/);
  assert.match(renderAmex, /behind by/);
  assert.match(CSS, /\.ax-ahead\b/);
  assert.match(CSS, /\.ax-behind\b/);
});

test("the card badge says PLAT or GOLD rather than relying on its colour", () => {
  assert.match(renderAmex, /"PLAT"\s*:\s*"GOLD"/);
});

test("every value the card interpolates is escaped", () => {
  /* `name` and `where` come from the catalogue today, but the catalogue is
   * meant to be edited, and an unescaped edit is a script tag. */
  const interps = [...renderAmex.matchAll(/\$\{([^}]*)\}/g)].map((m) => m[1]);
  const risky = interps.filter((x) =>
    /\bc\.(name|where|key)\b/.test(x) && !x.includes("esch("));
  assert.deepStrictEqual(risky, [],
    `unescaped catalogue values interpolated into HTML: ${risky}`);
  /* `c.card` reaches the markup twice: as a class name, which must be
   * escaped, and through the badge ternary above, which only ever emits one
   * of two literals and so carries nothing to escape. */
  assert.match(renderAmex, /class="ax-card \$\{esch\(c\.card\)\}"/);
});

/* --- the full list behind the tile (app.js) ------------------------------ */

const APP_SRC = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

const amexRenderer = (() => {
  const start = APP_SRC.indexOf("  async amex(app, body) {");
  assert.ok(start > -1, "the amex renderer is gone — the tile falls back to " +
    "renderGeneric, which prints the description and nothing else");
  const end = APP_SRC.indexOf("\n  async ", start + 1);
  return APP_SRC.slice(start, end > -1 ? end : APP_SRC.length);
})();

test("an unknown net is not reported as being ahead", () => {
  /* `null >= 0` is true in JS. A missing year-to-date figure rendered through
   * a bare `net >= 0` prints "Ahead of fees" over a dash — the most flattering
   * possible reading of no data, which is the one failure mode this repo's
   * money rules exist to prevent. */
  const interps = [...amexRenderer.matchAll(/\$\{([^}]*)\}/g)].map((m) => m[1]);
  const reading = interps.filter((x) => x.includes("net >= 0"));
  assert.ok(reading.length, "nothing reads the sign of the net any more");
  for (const x of reading) {
    assert.ok(/net\s*[!=]=\s*null/.test(x),
      `this reads the sign of the net without answering null first: ${x.trim()}`);
  }
});

test("the list says when the catalogue was last checked", () => {
  /* Amex changes these terms — the catalogue is a snapshot, and a snapshot
   * that does not date itself gets believed forever. */
  assert.match(amexRenderer, /catalogue_checked/);
});

test("enrollment is shown, because an unenrolled credit pays nothing", () => {
  assert.match(amexRenderer, /r\.enroll \?/);
  assert.match(amexRenderer, /enroll<\/span>/);
});

test("a used credit can be un-marked", () => {
  /* One-way buttons make people stop pressing them. The service accepts
   * `used: false`, so the list has to offer it. */
  assert.match(amexRenderer, /data-used="\$\{r\.used \? "0" : "1"\}"/);
  assert.match(amexRenderer, /used: b\.dataset\.used === "1"/);
});
