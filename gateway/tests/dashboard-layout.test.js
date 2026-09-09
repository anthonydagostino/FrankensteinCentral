/* The main dashboard: ordered by what gets used, each fact said once.
 *
 * Anthony, 2026-09-09: "the only useful shit right now is the stocks, the
 * financial section, and the calendar, the rest of the main dashboard fucking
 * SUCKS." SCRUM-138. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
const Attention = require("../static/attention.js");

/* --- the dismiss menu was open on every card, permanently ---------------- */

test("a hidden snooze menu is actually hidden", () => {
  /* `.snz-menu { display: flex }` outranks the UA stylesheet's
   * `[hidden] { display: none }` — a class selector beats it — so every one of
   * these menus rendered OPEN, on every card, forever. The JS toggled `hidden`
   * correctly the whole time and it never once had an effect; three of them
   * hung over the money card. Verified in a browser before the fix: 4 menus,
   * all 4 carrying the attribute, all 4 still painted.
   *
   * So: any `display` this rule sets must be guarded by `:not([hidden])`. */
  const rules = [...CSS.matchAll(/\.snz-menu([^{,]*)\{([^}]*)\}/g)];
  assert.ok(rules.length, "no .snz-menu rule at all");
  for (const [, selectorTail, body] of rules) {
    if (!/(^|[;\s])display\s*:/.test(body)) continue;
    assert.match(selectorTail, /:not\(\[hidden\]\)/,
      `.snz-menu${selectorTail} sets display without a :not([hidden]) guard, ` +
      `which overrides the attribute and pins the menu open`);
  }
});

test("the snooze menu still has a way to be shown", () => {
  /* A guard that never matches would fix the bug by breaking the feature. */
  assert.match(CSS, /\.snz-menu:not\(\[hidden\]\)\s*\{[^}]*display:\s*flex/);
});

/* --- ordered by what actually gets opened -------------------------------- */

const at = (needle) => HTML.indexOf(needle);

test("the calendar, money and portfolio come before everything else", () => {
  /* The three things this dashboard is used for. Money used to start ~1141px
   * down, behind a weekly-review bar chart, the calendar and a 180px hero;
   * measured at 533px after. */
  const cal = at('id="cc-calendar"'), money = at('id="cc-money"');
  const port = at('id="cc-portfolio"'), cols = at('class="cc-cols"');
  for (const [name, i] of [["calendar", cal], ["money", money],
                           ["portfolio", port], ["columns", cols]]) {
    assert.ok(i > -1, `${name} is missing from the page`);
  }
  assert.ok(cal < money, "the calendar should lead");
  assert.ok(money < cols && port < cols,
    "money and the portfolio must sit above the two-column region, not inside it");
});

test("money and the portfolio share one row", () => {
  assert.match(HTML, /cc-money-row/);
  assert.match(CSS, /\.cc-money-row\s*\{[^}]*grid-template-columns:\s*1fr 1fr/);
  assert.match(CSS, /@media[^{]*max-width:\s*900px[^{]*\{\s*\.cc-money-row[^}]*1fr/,
    "the row must stack on a narrow screen");
});

test("the weekly review is out of the top slot", () => {
  /* It used to be the FIRST card on the page, above the calendar, restating
   * study-vs-goal and gym-vs-goal — both of which the Health & Discipline card
   * states in full. It now lives beside that card. */
  assert.ok(at('id="cc-weekly-slot"') > at('id="cc-calendar"'),
    "the weekly review should no longer precede the calendar");
  assert.ok(at('id="cc-weekly-slot"') > at('id="cc-money"'));
});

test("there is exactly one weekly-review element", () => {
  /* Sunday evening it moves to the top of the grid. Moving it — rather than
   * rendering a second copy — is deliberate: two elements with one id is
   * exactly how the previous weekly card became unreachable DOM, since
   * querySelector only ever returns the first. */
  assert.strictEqual((HTML.match(/id="cc-weekly"/g) || []).length, 1);
});

/* --- Do-Next stands down when the feed already has the item -------------- */

test("the hero is suppressed only when it is the same item", () => {
  const nudges = [{ key: "study" }, { key: "water" }];
  assert.strictEqual(Attention.isDuplicate({ key: "study" }, nudges), true);
  assert.strictEqual(Attention.isDuplicate({ key: "gym" }, nudges), false);
});

test("matching is by key, never by title", () => {
  /* Two emails can both say "Reply to Dana" and be different emails.
   * Suppressing on that coincidence would silently drop a real
   * recommendation. */
  const nudges = [{ key: "email:1", title: "Reply to Dana" }];
  assert.strictEqual(
    Attention.isDuplicate({ key: "email:2", title: "Reply to Dana" }, nudges), false);
  assert.strictEqual(
    Attention.isDuplicate({ key: "email:1", title: "Something else" }, nudges), true);
});

test("a keyless Do-Next is never suppressed", () => {
  /* The calm "You're on track" fallback carries no key. Suppressing it would
   * leave the page with no hero and nothing in the feed either. */
  assert.strictEqual(Attention.isDuplicate({ title: "You're on track" }, [{ key: "study" }]), false);
  assert.strictEqual(Attention.isDuplicate({ key: "" }, [{ key: "" }]), false);
});

test("nothing to compare against is not a duplicate", () => {
  for (const nudges of [[], null, undefined, "nonsense"]) {
    assert.strictEqual(Attention.isDuplicate({ key: "study" }, nudges), false);
  }
  for (const dn of [null, undefined, {}]) {
    assert.strictEqual(Attention.isDuplicate(dn, [{ key: "study" }]), false);
  }
});

test("a ragged nudge list does not throw", () => {
  /* The feed is built from core's payload and has carried nulls before. */
  assert.strictEqual(
    Attention.isDuplicate({ key: "study" }, [null, undefined, {}, { key: "study" }]), true);
});
