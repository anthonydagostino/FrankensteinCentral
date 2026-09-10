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

test("the money cards share one row that stacks on a phone", () => {
  assert.match(HTML, /cc-money-row/);
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


/* --- the habit form gave way to the resale book (SCRUM-139) -------------- */

test("resale sits with the other money cards, not below the fold", () => {
  /* It is money, and it is the only card on the page carrying a deadline. */
  assert.ok(at('id="cc-resale"') > -1, "no resale card");
  assert.ok(at('id="cc-resale"') < at('class="cc-cols"'),
    "resale must sit in the top money row");
  assert.ok(at('id="cc-money"') < at('id="cc-resale"'));
});

test("the money row reflows instead of pinning a card count", () => {
  /* Three cards where there were two, without a breakpoint per arrangement. */
  assert.match(CSS, /\.cc-money-row\s*\{[^}]*repeat\(auto-fit,\s*minmax\(/);
});

test("the health card's score ring is gone", () => {
  /* It drew the same number as the score pill in the page header, a few
   * hundred pixels apart on one screen. The card heading still names it. */
  const health = HTML.indexOf('id="cc-health"');
  assert.ok(health > -1);
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderHealth\([\s\S]*?\n  \}/);
  assert.ok(fn, "renderHealth not found");
  assert.ok(!/score-ring/.test(fn[0]),
    "the health card still draws a score ring, duplicating the header pill");
});

test("every logging control is behind the disclosure", () => {
  /* A home screen carries what you monitor; a form belongs on a drill-down.
   * Nothing was removed — each control must still exist, inside the drawer. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderHealth\([\s\S]*?\n  \}/)[0];
  const drawer = fn.match(/<details class="hx-log"[\s\S]*?<\/details>/);
  assert.ok(drawer, "no logging drawer");
  for (const control of ["focusBtns", "waterBtns", "nutBtns", "sleepBtnsHtml", 'data-gym="1"']) {
    assert.ok(drawer[0].includes(control),
      `${control} is not inside the drawer — it was dropped, or left on the card`);
  }
  // ...and rendered nowhere else. The check is on the INTERPOLATION, not the
  // name: `const focusBtns = ...` legitimately sits at the top of the function,
  // and an earlier version of this test failed on those declarations while the
  // markup was already correct.
  const outside = fn.replace(drawer[0], "");
  for (const control of ["focusBtns", "waterBtns", "nutBtns", "sleepBtnsHtml"]) {
    assert.ok(!outside.includes("${" + control + "}"),
      `${control} is still rendered outside the drawer`);
  }
});

test("the drawer remembers whether it was left open", () => {
  /* If he does log from here daily it should simply stay open, and a private
   * window must not throw on the read. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  assert.match(js, /localStorage\.getItem\(HX_LOG_KEY\)/);
  assert.match(js, /catch\s*\(e\)\s*\{\s*return false;\s*\}/);
});


/* --- data safety (SCRUM-67) ---------------------------------------------- */

test("the home screen has a data-safety card", () => {
  /* Acceptance signal: "the home screen states how many days since the last
   * verified restore, and says 'never' until one happens." */
  assert.ok(at('id="cc-safety"') > -1, "no data-safety card on the home screen");
});

test("never and stale are loud; ok is not", () => {
  /* The asymmetry is the design. An infra card that looks the same whether or
   * not you are protected is one you stop reading. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderSafety\([\s\S]*?\n  \}/);
  assert.ok(fn, "renderSafety not found");
  const body = fn[0];
  assert.match(body, /never:\s*\{[^}]*cls:\s*"bad"/);
  assert.match(body, /stale:\s*\{[^}]*cls:\s*"bad"/);
  assert.match(body, /ok:\s*\{[^}]*cls:\s*"good"/);
  assert.match(body, /unknown:\s*\{[^}]*cls:\s*"muted"/);
  assert.match(CSS, /\.ds-lead\.bad\s*\{[^}]*var\(--imp\)/);
});

test("an unreadable record never renders as safe", () => {
  /* The assistant runs in a container and the record is written on the host,
   * so an absent mount is a normal failure. It must not read as protected. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderSafety\([\s\S]*?\n  \}/)[0];
  assert.match(fn, /BODY\[s\.state\]\s*\|\|\s*BODY\.unknown/,
    "an unrecognised state must fall back to unknown, not to ok");
  assert.match(fn, /state:\s*"unknown"/, "a missing payload must default to unknown");
});

test("the card names the command that fixes it", () => {
  /* "Never" with no way out is a complaint. The drill is the way out. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderSafety\([\s\S]*?\n  \}/)[0];
  assert.match(fn, /restore\.sh --drill/);
});

test("disk free is shown from the payload and never invented", () => {
  /* Fact 1 of the ticket, the half a bind mount exposes without host access.
   * When the mount is absent the assistant sends `unknown`, and the card must
   * omit the line rather than print a number about the container's own disk. */
  const js = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
  const fn = js.match(/function renderSafety\([\s\S]*?\n  \}/)[0];
  assert.match(fn, /d\.disk\.state !== "unknown"/, "an unknown disk state must render nothing");
  assert.match(fn, /d\.disk\.free_pct != null/, "a missing percentage must render nothing");
  assert.match(fn, /% free/, "the line states the percentage");
  assert.match(fn, /d\.disk\.state === "low" \? " warn"/, "a low disk is flagged, not just listed");
});
