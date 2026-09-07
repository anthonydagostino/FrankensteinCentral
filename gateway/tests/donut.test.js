/* The spending donut says what it knows, and no more.
 *
 * Anthony asked for hover on the pie. Hover is the point at which a chart
 * makes a claim about one slice rather than a shape about all of them, so
 * these tests are mostly about what a slice is allowed to say.
 *
 * All fixture data is synthetic — the repo is public.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const Donut = require("../static/donut.js");

const cat = (name, amount) => ({ name, amount });

test("slices come back biggest first", () => {
  const s = Donut.slicesFor([cat("Gas", 50), cat("Food", 300), cat("Rent", 120)]);
  assert.deepStrictEqual(s.map((x) => x.name), ["Food", "Rent", "Gas"]);
});

test("percentages are real numbers, not pre-rounded", () => {
  // 33.4 and 32.6 must not both arrive as 33 — a caller comparing slices
  // would conclude they are equal when they are not.
  const s = Donut.slicesFor([cat("A", 334), cat("B", 326), cat("C", 340)]);
  assert.ok(Math.abs(s[0].pct - 34.0) < 0.01);
  assert.notStrictEqual(s[1].pct, s[2].pct);
});

test("everything past eight slices rolls into Other", () => {
  const many = Array.from({ length: 12 }, (_, i) => cat("C" + i, 100 - i));
  const s = Donut.slicesFor(many);
  assert.strictEqual(s.length, 9, "eight slices plus the roll-up");
  assert.strictEqual(s[8].name, "Other");
  assert.strictEqual(s[8].rolledUp, 4);
});

test("the roll-up says how many categories it hides", () => {
  const many = Array.from({ length: 12 }, (_, i) => cat("C" + i, 100 - i));
  const other = Donut.slicesFor(many)[8];
  const label = Donut.sliceLabel(other);
  assert.match(label, /4 smaller categories/,
    "'Other' must not read as one thing when it is four");
});

test("a single hidden category is not pluralised", () => {
  const nine = Array.from({ length: 9 }, (_, i) => cat("C" + i, 100 - i));
  const other = Donut.slicesFor(nine)[8];
  assert.match(Donut.sliceLabel(other), /1 smaller category\b/);
});

test("a slice label states exact dollars, not a rounded figure", () => {
  const s = Donut.slicesFor([cat("Food", 412.37), cat("Gas", 100)]);
  assert.match(Donut.sliceLabel(s[0]), /\$412\.37/,
    "an exact amount was rounded away in the thing the user reads");
});

test("zero and negative categories are dropped, not drawn", () => {
  const s = Donut.slicesFor([cat("Food", 100), cat("Nothing", 0), cat("Refund", -20)]);
  assert.deepStrictEqual(s.map((x) => x.name), ["Food"]);
});

test("an empty category list renders nothing at all", () => {
  assert.strictEqual(Donut.render([], "Spending"), "");
  assert.strictEqual(Donut.render(null, "Spending"), "");
});

test("one category fills the ring without breaking the path", () => {
  const html = Donut.render([cat("Food", 100)], "Spending");
  assert.match(html, /class="dn-slice"/);
  assert.doesNotMatch(html, /NaN/, "the full-circle case produced NaN geometry");
});

test("every wedge is reachable and announced without a mouse", () => {
  const html = Donut.render([cat("Food", 300), cat("Gas", 100)], "Spending");
  assert.strictEqual((html.match(/tabindex="0"/g) || []).length, 4,
    "two wedges and two legend rows are focusable");
  assert.strictEqual((html.match(/aria-label=/g) || []).length, 2);
  assert.match(html, /<title>Food \$300\.00 · 75%<\/title>/,
    "a native tooltip must work before any JavaScript runs");
});

test("the hover readout is carried in the markup, not computed on the fly", () => {
  const html = Donut.render([cat("Food", 300), cat("Gas", 100)], "Spending");
  assert.match(html, /data-label="Food \$300\.00 · 75%"/);
  assert.match(html, /data-i="0"/);
  assert.match(html, /data-i="1"/);
});

test("legend rows and wedges share one index, so highlighting can pair them", () => {
  const html = Donut.render([cat("Food", 300), cat("Gas", 100)], "Spending");
  const wedges = [...html.matchAll(/class="dn-slice"[^>]*data-i="(\d+)"/g)].map((m) => m[1]);
  const rows = [...html.matchAll(/class="row dn-row" data-i="(\d+)"/g)].map((m) => m[1]);
  assert.deepStrictEqual(wedges, rows);
});

test("titles are escaped", () => {
  const html = Donut.render([cat('<script>alert(1)</script>', 100)], "Spending");
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});

test("the centre holds the total at rest", () => {
  const html = Donut.render([cat("Food", 300), cat("Gas", 100)], "Spending");
  assert.match(html, /class="dn-total"[^>]*>\$400</,
    "the resting readout must be the total, not a slice");
});
