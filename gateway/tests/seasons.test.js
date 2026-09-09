/* The seasonal themes are a PALETTE per month, and they stay decoration.
 *
 * Two separate promises are pinned here. The first is that October actually
 * looks like October: three distinct colours, not one hue at three opacities,
 * and no two months wearing the same set. The second is the constraint that
 * makes the first safe — a theme may set accents and washes and nothing else,
 * so every word on the card has identical contrast in December and July. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");
const MONTHS = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"];

function paletteOf(mo) {
  const block = CSS.match(
    new RegExp(`\\.wk-grid\\[data-season="${mo}"\\][^{]*\\{([^}]*)\\}`));
  assert.ok(block, `no theme block for ${mo}`);
  const out = {};
  for (const m of block[1].matchAll(/--(sn-accent(?:-[23])?)\s*:\s*(#[0-9a-fA-F]{3,8})/g)) {
    out[m[1]] = m[2].toLowerCase();
  }
  return out;
}

test("every month declares three colours, all different from each other", () => {
  for (const mo of MONTHS) {
    const p = paletteOf(mo);
    const keys = ["sn-accent", "sn-accent-2", "sn-accent-3"];
    for (const k of keys) assert.ok(p[k], `${mo} is missing --${k}`);
    const uniq = new Set(keys.map((k) => p[k]));
    assert.strictEqual(uniq.size, 3,
      `${mo} repeats a colour: ${JSON.stringify(p)} — the whole complaint was ` +
      `that a month read as one colour`);
  }
});

test("no two months wear the same palette", () => {
  const seen = new Map();
  for (const mo of MONTHS) {
    const p = paletteOf(mo);
    const key = [p["sn-accent"], p["sn-accent-2"], p["sn-accent-3"]].join("/");
    assert.ok(!seen.has(key), `${mo} and ${seen.get(key)} are identical: ${key}`);
    seen.set(key, mo);
  }
});

test("October is pumpkin and December is not", () => {
  /* The two the request named. Guards against a refactor that keeps twelve
   * distinct palettes but quietly reshuffles which month gets which. */
  const oct = paletteOf("oct"), dec = paletteOf("dec");
  const warm = (hex) => {
    const [r, , b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return r > b;
  };
  assert.ok(warm(oct["sn-accent"]), `October's primary should be warm: ${oct["sn-accent"]}`);
  assert.ok(!warm(dec["sn-accent"]), `December's primary should be cool: ${dec["sn-accent"]}`);
});

test("a theme sets accents only — never a text, surface or border token", () => {
  /* This is what lets the palettes be as loud as they are. If a month could
   * set --text, "readable in October" would become a per-month question. */
  const forbidden = ["--text", "--muted", "--panel", "--panel-2", "--line", "--bg"];
  for (const mo of MONTHS) {
    const block = CSS.match(
      new RegExp(`\\.wk-grid\\[data-season="${mo}"\\][^{]*\\{([^}]*)\\}`))[1];
    for (const tok of forbidden) {
      assert.ok(!new RegExp(`${tok}\\s*:`).test(block),
        `${mo} sets ${tok}, which would change contrast between months`);
    }
  }
});

test("the stacked phone layout drops the desktop height floor", () => {
  /* The floor makes seven columns line up. Inherited by the stacked rows it
   * turned every quiet day into a screenful of empty colour. */
  const phone = CSS.slice(CSS.indexOf("@media (max-width: 680px)"));
  const day = phone.match(/\.wk-day\s*\{([^}]*)\}/);
  assert.ok(day, "no .wk-day rule in the phone media query");
  assert.match(day[1], /min-height:\s*0/,
    "the phone rule must reset min-height, or a Clear day is 260px tall");
});

test("the desktop card still has a height floor and real padding", () => {
  const base = CSS.slice(0, CSS.indexOf("@media (max-width: 1080px)"));
  const day = base.match(/\n\.wk-day \{([^}]*)\}/);
  assert.ok(day, "no base .wk-day rule");
  const min = day[1].match(/min-height:\s*(\d+)px/);
  assert.ok(min && Number(min[1]) >= 200,
    `day cards should stay roomy, got ${min && min[1]}`);
});
