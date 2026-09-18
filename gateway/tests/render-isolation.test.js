/* One bad card is one bad card.
 *
 * WHY THIS FILE EXISTS. `render(d)` was a bare sequence of eighteen calls, so
 * the FIRST renderer to throw silently erased every card below it. The page
 * painted down to that point and stopped — no error banner, nothing missing
 * that looked missing, just a shorter dashboard.
 *
 * Anthony reported the weather and amex cards absent twice. They are 14th and
 * 15th in that sequence. Both were present in the HTML and both had working
 * renderers; anything at all going wrong above them took them out, together
 * with the capture box, the "Updated" stamp and the deploy card. Two rounds
 * were spent moving cards that were never the problem.
 *
 * The console is not somewhere anyone looks at a dashboard from a phone, so
 * the failure is written into the card that failed. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");

/* Lift `paint` out of the IIFE and run it for real, rather than asserting on
 * its source text. This is the one behaviour in home.js worth executing: it is
 * a try/catch, and a try/catch is exactly the kind of thing that reads
 * correctly and is wired up wrong. */
function loadPaint(cards) {
  const start = SRC.indexOf("function paint(label, cardId, fn) {");
  assert.ok(start > -1, "paint() is gone — the render chain is unguarded again");
  const end = SRC.indexOf("\n  }", start) + 4;
  const errors = [];
  const sandbox = {
    q: (sel) => cards[sel] || null,
    esch: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    console: { error: (...a) => errors.push(a) },
  };
  vm.createContext(sandbox);
  const paint = vm.runInContext(
    `(function () { ${SRC.slice(start, end)} return paint; })()`, sandbox);
  return { paint, errors };
}

const makeCard = () => ({ innerHTML: "", removeAttribute() { this.hidden = false; }, hidden: true });

test("a card that renders fine is left alone", () => {
  const card = makeCard();
  const { paint, errors } = loadPaint({ "#cc-weather": card });
  paint("Weather", "#cc-weather", () => { card.innerHTML = "<h3>81°</h3>"; });
  assert.strictEqual(card.innerHTML, "<h3>81°</h3>");
  assert.strictEqual(errors.length, 0);
});

test("a throwing card does not stop the ones after it", () => {
  /* The whole point. Before this, `after` never ran. */
  const bad = makeCard();
  const { paint } = loadPaint({ "#cc-money": bad });
  let after = false;
  paint("Money", "#cc-money", () => { throw new TypeError("x is undefined"); });
  paint("Weather", "#cc-weather", () => { after = true; });
  assert.ok(after, "a failure in one card still blocks the rest");
});

test("the failure is written into the card, not only the console", () => {
  const card = makeCard();
  const { paint, errors } = loadPaint({ "#cc-amex": card });
  paint("Amex credits", "#cc-amex", () => { throw new TypeError("boom"); });
  assert.match(card.innerHTML, /failed to render/);
  assert.match(card.innerHTML, /boom/);
  assert.match(card.innerHTML, /Amex credits/);
  assert.strictEqual(errors.length, 1, "and it still reaches the console");
});

test("a card hidden when empty is un-hidden to show its failure", () => {
  /* Several cards carry `hidden` until they have something to say. A card that
   * just failed has something to say. */
  const card = makeCard();
  const { paint } = loadPaint({ "#cc-attention": card });
  paint("Attention", "#cc-attention", () => { throw new Error("nope"); });
  assert.strictEqual(card.hidden, false);
});

test("a failure with no card of its own is swallowed, not rethrown", () => {
  const { paint } = loadPaint({});
  assert.doesNotThrow(() => paint("Updated stamp", null, () => { throw new Error("x"); }));
  assert.doesNotThrow(() => paint("Missing", "#cc-nope", () => { throw new Error("x"); }));
});

test("the error message is escaped on its way into the card", () => {
  /* It comes from a thrown exception, which can carry anything. */
  const card = makeCard();
  const { paint } = loadPaint({ "#cc-today": card });
  paint("Today", "#cc-today", () => { throw new Error("<img src=x onerror=alert(1)>"); });
  assert.ok(!card.innerHTML.includes("<img"), "the message went in unescaped");
});

/* --- and nothing in the chain may go back to being unguarded ------------- */

test("every renderer in the chain is painted, never called bare", () => {
  const start = SRC.indexOf("function render(d) {");
  const end = SRC.indexOf("\n  function ", start + 1);
  const chain = SRC.slice(start, end > -1 ? end : SRC.length);
  const bare = [];
  for (const line of chain.split("\n")) {
    const m = line.match(/^\s{4}(render[A-Za-z]+)\(/);
    if (m) bare.push(m[1]);
  }
  assert.deepStrictEqual(bare, [],
    `called outside paint(): ${bare}. One of these throwing erases every card ` +
    "below it, which is the defect this file exists for.");
  assert.ok(chain.split("paint(").length - 1 >= 15,
    "the chain lost its paint() wrappers");
});

/* --- a blank card must announce itself ----------------------------------- */

function loadBlank(cards) {
  const start = SRC.indexOf("const CARD_NAMES = {");
  assert.ok(start > -1, "CARD_NAMES is gone");
  const end = SRC.indexOf("\n  }\n", SRC.indexOf("function reportBlankCards()")) + 4;
  const sandbox = {
    esch: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    document: { querySelectorAll: () => Object.values(cards) },
    console: { error() {} },
  };
  vm.createContext(sandbox);
  return vm.runInContext(
    `(function () { ${SRC.slice(start, end)} return reportBlankCards; })()`, sandbox);
}

const blankCard = (id, cls = "cc-card") => ({
  id, innerHTML: "", _hidden: false,
  hasAttribute() { return this._hidden; },
  classList: { contains: (c) => c === cls },
});

test("an empty card says so instead of collapsing to a sliver", () => {
  /* The failure that cost three rounds: an empty `<section class="cc-card">`
   * is a line of border with no content, which from a screenshot is
   * indistinguishable from a card that was never added to the page. */
  const card = blankCard("cc-weather");
  loadBlank({ a: card })();
  assert.match(card.innerHTML, /Weather/);
  assert.match(card.innerHTML, /Rendered nothing this cycle/);
});

test("a card with content is left exactly as it was", () => {
  const card = blankCard("cc-money");
  card.innerHTML = "<h3>Money</h3><p>$4,210</p>";
  loadBlank({ a: card })();
  assert.strictEqual(card.innerHTML, "<h3>Money</h3><p>$4,210</p>");
});

test("whitespace-only counts as blank", () => {
  const card = blankCard("cc-amex");
  card.innerHTML = "\n   \n";
  loadBlank({ a: card })();
  assert.match(card.innerHTML, /Amex credits/);
});

test("a deliberately hidden card is left hidden", () => {
  /* Several cards hide themselves when they genuinely have nothing to say.
   * That is a decision, not a silence, and filling them would put noise on the
   * page every single render. */
  const card = blankCard("cc-attention");
  card._hidden = true;
  loadBlank({ a: card })();
  assert.strictEqual(card.innerHTML, "");
});

test("an unnamed card still reports, using its id", () => {
  const card = blankCard("cc-brand-new");
  loadBlank({ a: card })();
  assert.match(card.innerHTML, /cc-brand-new/);
});

test("the blank check runs last, after every card is painted", () => {
  const start = SRC.indexOf("function render(d) {");
  const chain = SRC.slice(start, SRC.indexOf("\n  function ", start + 1));
  assert.ok(chain.indexOf("reportBlankCards") > chain.lastIndexOf("renderDeploy"),
    "the blank check must see the finished page, so it goes last");
});

test("the header weather pill is covered by the blank check too", () => {
  /* It lives in the header rather than the grid, so it is not a `.cc-card`.
   * Dropping out of that selector is exactly how it would go back to failing
   * silently — which is the whole subject of this file. */
  const pill = blankCard("cc-weather", "cc-wx-pill");
  loadBlank({ a: pill })();
  assert.match(pill.innerHTML, /Weather/);
  assert.match(pill.innerHTML, /no data/);
});

test("the pill gets the short message, not a paragraph in the header", () => {
  const pill = blankCard("cc-weather", "cc-wx-pill");
  loadBlank({ a: pill })();
  assert.ok(!pill.innerHTML.includes("<h3>"), "a heading in the header bar");
  assert.ok(pill.innerHTML.length < 120, "too long for a header pill");
});

test("the blank check still selects both the cards and the pill", () => {
  const fn = SRC.slice(SRC.indexOf("function reportBlankCards()"));
  assert.match(fn, /querySelectorAll\("\.cc-card, \.cc-wx-pill"\)/);
});
