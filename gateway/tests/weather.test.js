/* The weather card: the big number is true or it is absent.
 *
 * This is the one card on the dashboard that gets read without being read —
 * people take the temperature and move on. So every assertion here is about
 * the same thing: no code path may print a number the service did not send.
 *
 * WHAT THIS CAN AND CANNOT PROVE. `home.js` and `app.js` are IIFEs with no
 * exports, so these read source rather than a rendered DOM. They catch the
 * one-character regressions — a falsy check where a null check belongs, a
 * figure on the unreachable path — and say nothing about layout. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const HOME = fs.readFileSync(path.join(__dirname, "../static/home.js"), "utf8");
const APP = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "../static/home.css"), "utf8");

const slice = (src, start, next) => {
  const i = src.indexOf(start);
  assert.ok(i > -1, `${start} is gone`);
  const j = src.indexOf(next, i + 1);
  return src.slice(i, j > -1 ? j : src.length);
};

const renderWeather = slice(HOME, "function renderWeather(", "\n  function ");
const weatherApp = slice(APP, "  async weather(app, body) {", "\n  async ");
const picker = slice(APP, "async function wireWeatherPicker(", "\nasync function ");

test("the card has somewhere to render and a consumer that calls it", () => {
  assert.match(HTML, /id="cc-weather"/);
  assert.match(HOME, /renderWeather\(d\.weather\)/);
});

test("weather sits beside the calendar and amex under it", () => {
  /* Anthony, 2026-09-16: "i want the weather to be a smallish box next to the
   * calendar, and the amex to be under the calendar." Third arrangement and
   * the one he asked for by hand, so it is pinned rather than left to drift. */
  const row = HTML.indexOf('class="cc-cal-row"');
  const cal = HTML.indexOf('id="cc-calendar"');
  const wx = HTML.indexOf('id="cc-weather"');
  const ax = HTML.indexOf('id="cc-amex"');
  const cols = HTML.indexOf('class="cc-cols"');
  for (const [name, i] of [["cal row", row], ["calendar", cal], ["weather", wx],
                           ["amex", ax], ["cols", cols]]) {
    assert.ok(i > -1, `${name} is missing from the page`);
  }
  assert.ok(row < cal && row < wx, "calendar and weather share the top row");
  assert.ok(cal < wx, "the calendar leads its row; weather is the box beside it");
  assert.ok(wx < ax, "amex sits under the calendar row, not in it");
  assert.ok(ax < cols, "amex must not sink into the two-column region");
});

test("the weather column is a fixed width, not a fraction of the row", () => {
  /* A forecast does not get more useful with more pixels and a calendar does.
   * A fractional split hands the week grid's space to a temperature. */
  const rule = CSS.match(/\.cc-cal-row\s*\{[^}]*\}/);
  assert.ok(rule, ".cc-cal-row has no rule");
  assert.match(rule[0], /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+\d+px/);
});

test("the two stack on a phone, with the calendar first", () => {
  assert.match(CSS, /@media \(max-width: 900px\) \{ \.cc-cal-row \{ grid-template-columns: 1fr; \} \}/);
});

test("amex is not a fourth card competing inside the money row", () => {
  const row = HTML.slice(HTML.indexOf('class="cc-money-row"'),
                         HTML.indexOf("</div>", HTML.indexOf('class="cc-money-row"')));
  assert.ok(!row.includes('id="cc-amex"'),
    "amex is back inside the money row, where it wraps out of sight");
});

test("a missing temperature renders as a dash, never as zero", () => {
  /* `Math.round(null)` is 0 and `null || 0` is 0. Either one prints 0° for a
   * reading we never got, which in February is entirely believable. */
  assert.match(renderWeather, /v == null \? "—"/,
    "the temperature formatter must answer null before rounding");
  assert.ok(!/Number\(w\.temp \|\| 0\)/.test(renderWeather));
  assert.ok(!/w\.temp \|\| 0/.test(renderWeather));
});

test("neither non-ok branch prints a figure", () => {
  const ok = renderWeather.indexOf('w.state !== "ok"');
  assert.ok(ok > -1, "the unreachable branch is gone");
  const end = renderWeather.indexOf("return;", ok);
  const branch = renderWeather.slice(0, end);
  /* Everything before the ok-branch returns: the not_configured card and the
   * unreachable card. Neither may interpolate a reading. */
  for (const field of ["w.temp", "w.high", "w.low", "w.feels_like"]) {
    const uses = branch.split(field).length - 1;
    assert.strictEqual(uses, 0, `${field} is printed before the state is known to be ok`);
  }
});

test("no location set is offered as a fix, not reported as an error", () => {
  /* One is fixable in ten seconds and the other is not. Collapsing them hides
   * the button that fixes it. */
  assert.match(renderWeather, /w\.state === "not_configured"/);
  assert.match(renderWeather, /openAppKey\("weather"\)/);
});

test("a stale reading is labelled with a word, not only a colour", () => {
  assert.match(renderWeather, /not current/);
  assert.match(CSS, /\.wx-stale\b/);
});

test("every value the card interpolates is escaped", () => {
  /* The place name comes from a geocoder and is arbitrary text. */
  const interps = [...renderWeather.matchAll(/\$\{([^}]*)\}/g)].map((m) => m[1]);
  const risky = interps.filter((x) =>
    /\b(w\.place|w\.label|h\.glyph|w\.glyph)\b/.test(x) && !x.includes("esch("));
  assert.deepStrictEqual(risky, [], `unescaped values: ${risky}`);
});

/* --- the ten-day view ---------------------------------------------------- */

test("a day with no bar draws no bar rather than one from a filled-in temp", () => {
  assert.match(weatherApp, /d\.bar_start == null \? ""/,
    "a null bar must be answered before the geometry is used");
});

test("a zero-width bar is still visible", () => {
  /* A day whose high equals its low is a real day. Rendering it 0% wide makes
   * it look like missing data, which is a different fact entirely. */
  assert.match(weatherApp, /Math\.max\(d\.bar_width, 2\)/);
});

test("the hourly and daily counts are reported, not assumed", () => {
  /* The service can return fewer hours near the end of its data. Saying "Next
   * 12 hours" over six chips is a small lie that is easy to avoid. */
  assert.match(weatherApp, /Next \$\{esc\(\(wx\.hourly \|\| \[\]\)\.length\)\} hours/);
  assert.match(weatherApp, /Next \$\{esc\(\(wx\.daily \|\| \[\]\)\.length\)\} days/);
});

/* --- changing the location ----------------------------------------------- */

test("the location can be changed from where the weather is shown", () => {
  assert.match(weatherApp, /id="wx-q"/);
  assert.match(picker, /\/weather\/search\?q=/);
  assert.match(picker, /encodeURIComponent/);
});

test("the chosen place is saved to core settings", () => {
  assert.match(picker, /\/core\/settings/);
  assert.match(picker, /method: "PUT"/);
  assert.match(picker, /lat: r\.lat, lon: r\.lon/);
});

test("saving a place carries the unit instead of resetting it", () => {
  /* The settings PUT shallow-merges at the top level, so this object REPLACES
   * the saved one — a dropped unit silently moves a °C dashboard to °F. */
  assert.match(picker, /unit === "celsius" \? "celsius" : "fahrenheit"/);
  assert.ok(!/unit: "fahrenheit" \} \}\)/.test(picker),
    "the unit is hardcoded, so changing the city resets the scale");
});

test("no matches and a broken search are different sentences", () => {
  assert.match(picker, /found\.state !== "ok"/);
  assert.match(picker, /Nothing matched/);
});

test("a place is passed by index, never through an HTML attribute", () => {
  /* `esc` does not escape quotes and a place name is arbitrary text from a
   * geocoder — the same rule the runway account list follows. */
  assert.match(picker, /data-wx="\$\{i\}"/);
  assert.match(picker, /found\.results\[Number\(b\.dataset\.wx\)\]/);
});
