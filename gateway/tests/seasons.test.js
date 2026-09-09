/* The seasonal themes are a PALETTE per month, and they stay decoration.
 *
 * Two separate promises are pinned here. The first is that October actually
 * looks like October: three distinct colours, not one hue at three opacities,
 * and no two months wearing the same set. The second is the constraint that
 * makes the first safe — a theme may set accents and washes and nothing else,
 * so every word on the card has identical contrast in December and July.
 *
 * A month declares its palette as --sn-p1/p2/p3. Those are the SOURCE, read
 * below. --sn-accent/-2/-3 are a rotation of them applied per day card, and
 * they need separate names: `--sn-accent: var(--sn-accent-2)` is a cycle, and
 * CSS discards the entire set when it finds one. */
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
  for (const m of block[1].matchAll(/--(sn-p[123])\s*:\s*(#[0-9a-fA-F]{3,8})/g)) {
    out[m[1]] = m[2].toLowerCase();
  }
  return out;
}

test("every month declares three colours, all different from each other", () => {
  for (const mo of MONTHS) {
    const p = paletteOf(mo);
    const keys = ["sn-p1", "sn-p2", "sn-p3"];
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
    const key = [p["sn-p1"], p["sn-p2"], p["sn-p3"]].join("/");
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
  assert.ok(warm(oct["sn-p1"]), `October's primary should be warm: ${oct["sn-p1"]}`);
  assert.ok(!warm(dec["sn-p1"]), `December's primary should be cool: ${dec["sn-p1"]}`);
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


/* --- the palette has to actually vary across the week --------------------
 *
 * Three colours per month were already declared and every card drew all three,
 * in the same order, seven times. Nothing changed as your eye moved across the
 * grid, so a three-colour theme still read as a single colour. `data-tint`
 * rotates which colour a day leads with. */

function tintRule(n) {
  const m = CSS.match(new RegExp(`\\.wk-day\\[data-tint="${n}"\\][^{]*\\{([^}]*)\\}`));
  assert.ok(m, `no rule for data-tint="${n}"`);
  const out = {};
  for (const d of m[1].matchAll(/--(sn-accent(?:-[23])?)\s*:\s*var\(--(sn-p[123])/g)) {
    out[d[1]] = d[2];
  }
  return out;
}

test("each tint is a genuine rotation, never a repeat", () => {
  /* A tint that mapped two slots to the same source colour would drop part of
   * the month's palette off that card. */
  for (const n of [1, 2]) {
    const r = tintRule(n);
    const slots = ["sn-accent", "sn-accent-2", "sn-accent-3"];
    for (const s of slots) assert.ok(r[s], `tint ${n} does not remap --${s}`);
    assert.strictEqual(new Set(slots.map((s) => r[s])).size, 3,
      `tint ${n} repeats a source colour: ${JSON.stringify(r)}`);
  }
});

test("every tint leads with a different colour", () => {
  /* The point of the whole thing: neighbouring cards must not lead with the
   * same colour, or the repetition is back. */
  const leads = new Set(["sn-p1", tintRule(1)["sn-accent"], tintRule(2)["sn-accent"]]);
  assert.strictEqual(leads.size, 3,
    `two tints lead with the same colour: ${[...leads].join(", ")}`);
});

test("the rotation reads the source palette, never itself", () => {
  /* `--sn-accent: var(--sn-accent-2)` is a cycle. CSS does not warn — it
   * throws the whole custom-property set away, and every themed colour on the
   * card silently falls back to the generic accent. */
  for (const n of [1, 2]) {
    const block = CSS.match(
      new RegExp(`\\.wk-day\\[data-tint="${n}"\\][^{]*\\{([^}]*)\\}`))[1];
    assert.ok(!/var\(--sn-accent/.test(block),
      `tint ${n} references --sn-accent, which is what it is defining`);
  }
});
