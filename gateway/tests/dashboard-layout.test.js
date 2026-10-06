/* The main dashboard: the few things that get used, in the order they get used.
 *
 * Anthony, 2026-09-09: "the only useful shit right now is the stocks, the
 * financial section, and the calendar, the rest of the main dashboard fucking
 * SUCKS." SCRUM-138 reordered the page around that. On 2026-10-06 the rest
 * was removed outright (SCRUM-140): the daily score and habit card, the
 * Do-Next nudge and its attention feed, inbox triage, Big 3, quick capture,
 * deadlines, the weekly review, the "while you were away" strip, the command
 * palette, the focus timer and the seasonal decoration.
 *
 * These pin what is left and, just as deliberately, that the rest stays gone. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
const JS = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");

const at = (needle) => HTML.indexOf(needle);

/* --- what is on the page, in order ----------------------------------------- */

test("the page is the calendar, the money row and the amex credits", () => {
  const grid = HTML.slice(at('id="cc-grid"'), at("</main>"));
  const cards = [...grid.matchAll(/<section class="cc-card[^"]*" id="([^"]+)"/g)].map((m) => m[1]);
  assert.deepStrictEqual(cards,
    ["cc-calendar", "cc-money", "cc-portfolio", "cc-resale", "cc-amex"],
    "the grid carries exactly these cards, in this order");
});

test("the calendar leads, the money row follows, amex is under it", () => {
  const cal = at('id="cc-calendar"'), row = at('class="cc-money-row"'), ax = at('id="cc-amex"');
  assert.ok(cal > -1 && row > -1 && ax > -1);
  assert.ok(cal < row, "the calendar should lead");
  assert.ok(row < ax, "amex sits under the money row, where it gets seen");
});

test("the money cards share one row that stacks on a phone", () => {
  assert.match(HTML, /cc-money-row/);
  assert.match(CSS, /\.cc-money-row\s*\{[^}]*repeat\(auto-fit,\s*minmax\(/,
    "three cards reflow without a breakpoint per arrangement");
  assert.match(CSS, /@media[^{]*max-width:\s*900px[^{]*\{\s*\.cc-money-row[^}]*1fr/,
    "the row must stack on a narrow screen");
});

test("resale sits with the other money cards", () => {
  /* It is money, and it is the only card on the page carrying a deadline. */
  const start = at('class="cc-money-row"');
  const row = HTML.slice(start, HTML.indexOf("</div>", start));
  assert.ok(row.includes('id="cc-money"') && row.includes('id="cc-portfolio"')
    && row.includes('id="cc-resale"'));
});

/* --- the header is the greeting, the weather and five controls -------------- */

test("the header carries no score pill and no command palette", () => {
  const head = HTML.slice(at("<header"), at("</header>"));
  assert.ok(!head.includes("cc-score-pill"), "the daily score is back in the header");
  assert.ok(!head.includes("cc-open-palette"), "the command palette button is back");
  for (const id of ["cc-weather", "cc-plex", "cc-vault", "cc-apps", "cc-settings-btn", "fc-logout"]) {
    assert.ok(head.includes(`id="${id}"`), `${id} is missing from the header`);
  }
});

/* --- the footer is operator facts, one line each ---------------------------- */

test("data safety is a footer line, not a card", () => {
  /* SCRUM-67's acceptance signal still holds: the home screen states how many
   * days since the last verified restore and says "never" until one happens.
   * It just does so where the other operator facts live. */
  const foot = HTML.slice(at("<footer"), at("</footer>"));
  for (const id of ["cc-updated", "cc-systems", "cc-deploy", "cc-safety"]) {
    assert.ok(foot.includes(`id="${id}"`), `${id} is missing from the footer`);
  }
  assert.ok(!/<section[^>]*id="cc-safety"/.test(HTML), "data safety is a card again");
});

test("never and stale are loud; ok is not", () => {
  /* The asymmetry is the design. An infra line that looks the same whether or
   * not you are protected is one you stop reading. */
  const fn = JS.match(/function renderSafety\([\s\S]*?\n  \}/);
  assert.ok(fn, "renderSafety not found");
  assert.match(fn[0], /never:\s*\{\s*cls:\s*"bad"/);
  assert.match(fn[0], /stale:\s*\{\s*cls:\s*"bad"/);
  assert.match(fn[0], /ok:\s*\{\s*cls:\s*"good"/);
  assert.match(fn[0], /unknown:\s*\{\s*cls:\s*"muted"/);
  assert.match(fn[0], /BODY\[s\.state\]\s*\|\|\s*BODY\.unknown/,
    "an unrecognised state must fall back to unknown, not to ok");
  assert.match(fn[0], /restore\.sh --drill/, "the way out is named");
});

test("the footer names every import state, and the suspect one loudest", () => {
  const fn = JS.match(/function renderSafety\([\s\S]*?\n  \}/)[0];
  assert.ok(fn.includes("import_run"), "the footer no longer reads import_run");
  for (const st of ["never", "stale", "failed", "unverified", "quiet", "suspect", "ok", "unknown"])
    assert.ok(new RegExp(`\\n\\s+${st}: \\[`).test(fn), `import state ${st} has no wording`);
  assert.ok(/suspect: \["warn"/.test(fn), "'runs but nothing enters' is not marked as a warning");
  assert.ok(/never: \["warn"/.test(fn), "'never run' is not marked as a warning");
  assert.match(fn, /d\.disk\.state !== "unknown"/, "an unknown disk state must render nothing");
});

/* --- and the rest stays gone ------------------------------------------------- */

test("the retired cards have no element, no renderer and no stylesheet", () => {
  for (const id of ["cc-donext", "cc-attention", "cc-deadlines", "cc-inbox", "cc-today",
                    "cc-health", "cc-weekly", "cc-weekly-slot", "cc-capture", "cc-since",
                    "cc-briefing", "palette", "focus"]) {
    assert.ok(!HTML.includes(`id="${id}"`), `${id} is back in index.html`);
  }
  for (const fn of ["renderDoNext", "renderAttention", "renderDeadlines", "renderInbox",
                    "renderToday", "renderHealth", "renderWeeklyReview", "renderCapture",
                    "renderSince", "startFocus", "openPalette", "snoozeBtn"]) {
    assert.ok(!JS.includes(fn + "("), `${fn} is back in home.js`);
  }
  for (const sel of [".donext", ".big3-item", ".score-ring", ".hx-log", ".cap-form",
                     ".palette", ".focus-overlay", ".wr-row", ".snz-menu", ".wk-flake",
                     ".wk-motif", 'data-season="oct"']) {
    assert.ok(!CSS.includes(sel), `${sel} is back in home.css`);
  }
});

test("the week grid carries no seasonal decoration", () => {
  /* A calendar is for reading. The events carry their own colours. */
  const week = JS.slice(JS.indexOf("function renderWeek("), JS.indexOf("function wireWeek("));
  assert.ok(!/SEASONS|motifs\(|mountAmbience|ambienceOn|data-season|data-tint/.test(week));
  assert.ok(!/localStorage/.test(week), "no decor toggle to remember");
});

test("settings carry only what the remaining cards read", () => {
  const fn = JS.slice(JS.indexOf("async function openSettings("), JS.indexOf("function parseHoldings("));
  for (const gone of ["Goals", "Exam", "score weights", "Daily-score", "Important senders"]) {
    assert.ok(!fn.includes(gone), `settings still offer "${gone}"`);
  }
  for (const kept of ["Investments", "Monthly budgets", "Paycheck", "Cash runway"]) {
    assert.ok(fn.includes(kept), `settings lost "${kept}"`);
  }
});

/* ---- SCRUM-142 / SCRUM-143: the import that feeds the money, and the
 * subscriptions it feeds. Source-level pins: each is a line that a later
 * "tidy" of the card could drop without any test in the repo noticing, and
 * each one is the point of its ticket. */

test("every recurring row states its yearly cost and who bills it", () => {
  const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const fn = app.match(/async budget\(app, body\) \{[\s\S]*?\n  \},/);
  assert.ok(fn, "budget panel not found");
  assert.ok(fn[0].includes("i.annual_cost"), "rows no longer show the per-year figure");
  assert.ok(fn[0].includes("i.via"), "rows no longer say which processor billed the charge");
  assert.ok(fn[0].includes("seen twice, a year apart"),
    "an annual charge seen twice is worded as weak evidence — it is a year of it");
  assert.ok(fn[0].includes("rec.annual_equivalent"), "the inventory has no yearly total");
});

test("the money card gives subscriptions per year as well as per month", () => {
  assert.ok(/rec\.annual_equivalent/.test(JS), "home.js never reads annual_equivalent");
});

test("the firefly panel shows what the import landed, per account", () => {
  const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  assert.ok(app.includes('"/firefly/accounts-health"'), "the panel no longer reads /accounts-health");
  const fn = app.match(/function importHealth\([\s\S]*?\n\}/);
  assert.ok(fn, "importHealth not found");
  for (const flag of ["no_credits", "stale"])
    assert.ok(fn[0].includes(flag), `the ${flag} flag is not rendered`);
  assert.ok(fn[0].includes("nothing in the window"),
    "an account with no rows must be listed as such, not left out");
});
