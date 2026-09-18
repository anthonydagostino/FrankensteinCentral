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

test("the weather pill sits in the header, next to the greeting", () => {
  /* Anthony, 2026-09-18: "put the weather right next to the Good evening at
   * the top. I want it to not take up so much space either." */
  const head = HTML.slice(HTML.indexOf("<header"), HTML.indexOf("</header>"));
  assert.ok(head.includes('id="cc-weather"'), "weather is not in the header");
  assert.ok(head.indexOf('id="cc-greeting"') < head.indexOf('id="cc-weather"'),
    "the greeting comes first, the weather beside it");
  assert.ok(head.indexOf('id="cc-weather"') < head.indexOf("cc-top-actions"),
    "weather belongs between the greeting and the action buttons");
});

test("weather is no longer a card in the grid", () => {
  /* The whole point of the move: it stopped being a card. */
  const grid = HTML.slice(HTML.indexOf('id="cc-grid"'));
  assert.ok(!grid.includes('id="cc-weather"'), "a second weather element in the grid");
  assert.ok(!HTML.includes('class="cc-card cc-wx"'), "still carrying card styling");
});

test("the calendar leads the grid and amex sits under it", () => {
  const cal = HTML.indexOf('id="cc-calendar"');
  const ax = HTML.indexOf('id="cc-amex"');
  const cols = HTML.indexOf('class="cc-cols"');
  assert.ok(cal > -1 && ax > -1 && cols > -1);
  assert.ok(cal < ax, "amex under the calendar");
  assert.ok(ax < cols, "amex must not sink into the two-column region");
});

test("the pill stays one line tall and carries no card chrome", () => {
  /* It gained an hourly strip on 2026-09-18 — "it can be slightly longer
   * showing the rest of the days weather" — so "small" is no longer "no
   * hours". It is: no heading, no wrapping, chips that stack hour over
   * temperature so the strip grows sideways and never downwards. */
  assert.ok(!/<h3>/.test(renderWeather), "a card heading in a header pill");
  assert.match(CSS, /\.cc-wx-pill\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(CSS, /\.wxp-h \{[^}]*flex-direction: column/);
});

test("the header strip shows the rest of today, bounded by the service", () => {
  /* The count is decided in dashboard.py, where it is swept across the clock.
   * The pill renders what it is handed and does not slice it again — two
   * places deciding how many hours is how they drift apart. */
  assert.match(renderWeather, /\(w\.hourly \|\| \[\]\)\.map/);
  assert.ok(!/w\.hourly[^\n]*\.slice\(/.test(renderWeather),
    "the pill is re-slicing a list the service already bounded");
});

test("each hour chip is labelled in 12-hour time, midnight included", () => {
  /* `h.hour === 0` must be "12a", not "0a". */
  assert.match(renderWeather, /h\.hour === 0 \? "12a"/);
  assert.match(renderWeather, /h\.hour === 12 \? "12p"/);
});

test("an hour with no temperature shows a dash, not a zero", () => {
  /* The same rule as the headline figure, one size down. */
  assert.match(renderWeather, /<i>\$\{esch\(t\(h\.temp\)\)\}<\/i>/);
});

test("the hours give way before the temperature on a narrow screen", () => {
  /* Order of sacrifice: extra hours, then all hours, then the place name. The
   * headline temperature is never the thing that goes. */
  assert.match(CSS, /@media \(max-width: 1100px\) \{ \.wxp-hrs \.wxp-h:nth-child\(n\+6\) \{ display: none; \} \}/);
  assert.match(CSS, /@media \(max-width: 900px\) \{ \.wxp-hrs \{ display: none; \} \}/);
  const narrow = CSS.slice(CSS.indexOf("@media (max-width: 1100px)"));
  assert.ok(!/\.wxp-t \{ display: none/.test(narrow), "the temperature is being hidden");
});

test("the header can wrap so the pill never squeezes the greeting", () => {
  const top = CSS.match(/\.cc-top\s*\{[^}]*\}/);
  assert.ok(top, ".cc-top has no rule");
  assert.match(top[0], /flex-wrap:\s*wrap/);
});

test("the place name is what gives way on a phone, never the temperature", () => {
  const narrow = CSS.slice(CSS.indexOf("@media (max-width: 700px)"));
  assert.match(narrow, /\.wxp-p \{ display: none; \}/);
  assert.ok(!/\.wxp-t \{ display: none/.test(narrow), "the temperature is being hidden");
});

test("clicking the pill opens the forecast", () => {
  assert.match(renderWeather, /openAppKey\("weather"\)/);
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
  assert.match(renderWeather, /wxp-stale/);
  assert.match(renderWeather, />old</, "the badge must carry a word");
  assert.match(renderWeather, /not current/, "and the title must say what it means");
  assert.match(CSS, /\.wxp-stale\b/);
});

test("every value the pill puts in HTML is escaped", () => {
  /* The place name comes from a geocoder and is arbitrary text. `title` is set
   * as a DOM PROPERTY rather than markup, so it needs no escaping — only the
   * innerHTML template does, and that is what this reads. */
  // lastIndexOf, not indexOf: the two early-return branches each assign
  // innerHTML first, and the `el.title = ...` template sits between them and
  // the one that matters. Starting at the first match swept the title in and
  // reported it as unescaped markup, which it is not.
  const html = renderWeather.slice(renderWeather.lastIndexOf("el.innerHTML = `"));
  const interps = [...html.matchAll(/\$\{([^}]*)\}/g)].map((m) => m[1]);
  const risky = interps.filter((x) =>
    /\bw\.(place|label|glyph|temp|high|low)\b/.test(x) && !x.includes("esch("));
  assert.deepStrictEqual(risky, [], `unescaped values: ${risky}`);
});

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
