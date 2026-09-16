/* What is owed on cards, beside "Left to spend" — never folded into it.
 *
 * Firefly derives left_to_spend from budgets and spending; card balances are
 * not part of that arithmetic. Rewriting its number here would make the
 * dashboard disagree with the ledger it claims to mirror, so the debt sits
 * next to it as its own figure.
 *
 * Every assertion below is the same rule in a different place: no code path
 * may render "nothing owed" from an absence of information. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const HOME = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");

const money = (() => {
  const i = HOME.indexOf("const owed = m.owed");
  assert.ok(i > -1, "the owed block is gone from renderMoney");
  return HOME.slice(i, HOME.indexOf("const accts =", i));
})();

test("the debt is rendered only when the state is ok", () => {
  /* `unreachable` and `no_liabilities` must never reach the total. */
  assert.match(money, /owed\.state === "ok"/);
  const lead = money.indexOf("mo-lead");
  const guard = money.indexOf('owed.state === "ok"');
  assert.ok(guard < lead, "the total is printed before the state is checked");
});

test("a missing owed block defaults to unreachable, not to zero", () => {
  assert.match(money, /m\.owed \|\| \{ state: "unreachable" \}/);
  assert.ok(!/m\.owed \|\| \{[^}]*total:\s*0/.test(money));
});

test("no liability accounts is explained, not shown as nothing owed", () => {
  /* The state Anthony is in if his cards are Firefly assets. A blank strip
   * there reads as "no debt", which is the opposite of the truth. */
  assert.match(money, /owed\.state === "no_liabilities"/);
  assert.match(money, /No liability accounts in Firefly/);
  assert.match(money, /asset/);
});

test("a partial total is labelled a floor", () => {
  /* One unreadable balance means the sum UNDERSTATES the debt, and understating
   * is the direction that matters. */
  assert.match(money, /owed\.partial \? "at least "/);
  assert.match(money, /floor rather than the total/);
});

test("an unreadable card balance renders as a dash, not a zero", () => {
  assert.match(money, /c\.owed == null \? "—"/);
});

test("the card names are escaped", () => {
  /* They come from Firefly, where the user names their own accounts. */
  assert.match(money, /esch\(c\.name\)/);
  assert.match(money, /esch\(money\(owed\.total, 2\)\)/);
});

test("the debt is not folded into left to spend", () => {
  /* left_to_spend is Firefly's figure. It is displayed, never recomputed. */
  const left = HOME.slice(HOME.indexOf("const ffTiles"), HOME.indexOf("const catPie"));
  assert.ok(!/left_to_spend[^\n]*owed|owed[^\n]*left_to_spend/.test(left),
    "left to spend is being adjusted by the card debt");
});

test("the owed strip has styles, including for the empty explanation", () => {
  assert.match(CSS, /\.mny-owed\b/);
  assert.match(CSS, /\.mo-lead\b/);
  assert.match(CSS, /\.mo-note\b/);
});
