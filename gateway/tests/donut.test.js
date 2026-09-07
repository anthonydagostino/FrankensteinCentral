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

/* ---- the centre readout ------------------------------------------------
 *
 * These cover the seam between donut.js and the delegated listener in app.js.
 * The listener used to rebuild the readout by splitting sliceLabel()'s output
 * on " · " and " $". That coupling was invisible and unguarded: changing the
 * separator broke the middle of the chart, and the two tests that noticed
 * were pinning the label string, so updating their expectations — the obvious
 * response — put the suite back to green with the readout still broken.
 * centreFor() composes the two figures instead, and app.js only assigns them.
 */

test("the centre readout is composed, not parsed back out of the label", () => {
  const [s] = Donut.slicesFor([{ name: "Rent", amount: 600 },
                               { name: "Food", amount: 400 }]);
  assert.deepStrictEqual(Donut.centreFor(s),
    { value: "$600.00", caption: "Rent · 60%" });
});

test("the readout survives a change to the label's separator", () => {
  // The regression this replaced: the readout must not depend on how
  // sliceLabel() happens to join its parts today.
  const [s] = Donut.slicesFor([{ name: "Rent", amount: 600 },
                               { name: "Food", amount: 400 }]);
  const c = Donut.centreFor(s);
  assert.ok(!Donut.sliceLabel(s).includes(c.caption),
    "the caption is being lifted out of the label rather than composed");
  assert.strictEqual(c.value, "$600.00");
});

test("the wedge carries the readout, so the listener does no string work", () => {
  const html = Donut.render([{ name: "Rent", amount: 600 },
                             { name: "Food", amount: 400 }], "Spending");
  assert.match(html, /data-v="\$600\.00"/);
  assert.match(html, /data-cap="Rent · 60%"/);
});

test("a long category name is trimmed to fit inside the ring", () => {
  // The hole is ~100 units wide at font-size 11; an untrimmed caption is drawn
  // straight over the wedges it is supposed to be describing.
  const [s] = Donut.slicesFor([{ name: "Home & Garden Improvements", amount: 100 }]);
  const c = Donut.centreFor(s);
  assert.ok(c.caption.length <= Donut.MAX_CAPTION,
    `caption ${c.caption.length} chars: ${c.caption}`);
  assert.match(c.caption, /…/);
});

test("a cut that lands on a space does not leave a dangling ellipsis", () => {
  // "Healthcare Costs" at 40% cuts at exactly 11 characters — "Healthcare "
  // — so without the trim the ring reads "Healthcare …", which looks like a
  // rendering fault rather than a truncation.
  const s = Donut.slicesFor([{ name: "Healthcare Costs", amount: 400 },
                             { name: "Rent", amount: 600 }])
    .find((x) => x.name === "Healthcare Costs");     // slicesFor sorts by size
  const c = Donut.centreFor(s);
  assert.strictEqual(c.caption, "Healthcare… · 40%");
  assert.ok(!c.caption.includes(" …"), "trimmed on a space, leaving a gap");
});

test("the roll-up's caption drops the detail that will not fit", () => {
  // "Other · 32% (4 smaller categories)" is 34 characters. The count still
  // reaches the reader — it is on the aria-label, the <title> and the legend.
  const many = Array.from({ length: 12 }, (_, i) => ({ name: "Cat" + i, amount: 100 - i }));
  const other = Donut.slicesFor(many).find((s) => s.rolledUp);
  const c = Donut.centreFor(other);
  assert.ok(c.caption.length <= Donut.MAX_CAPTION, c.caption);
  assert.ok(!c.caption.includes("categor"), c.caption);
  assert.match(Donut.sliceLabel(other), /4 smaller categories/);
});

test("every caption fits, for every slice of a realistic ledger", () => {
  const cats = [
    { name: "Groceries", amount: 812.4 }, { name: "Restaurants", amount: 431.09 },
    { name: "Home & Garden Improvements", amount: 388 }, { name: "Transport", amount: 240 },
    { name: "Bills & Utilities", amount: 190 }, { name: "Health", amount: 88 },
    { name: "Subscriptions", amount: 61.47 }, { name: "Pets", amount: 40 },
    { name: "Gifts", amount: 30 }, { name: "Misc", amount: 12 },
  ];
  for (const s of Donut.slicesFor(cats)) {
    const c = Donut.centreFor(s);
    assert.ok(c.caption.length <= Donut.MAX_CAPTION, `${c.caption} (${c.caption.length})`);
    assert.match(c.value, /^\$[\d,]+\.\d\d$/);
  }
});
