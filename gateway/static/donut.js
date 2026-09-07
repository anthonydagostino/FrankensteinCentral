/* The spending donut's geometry and markup, in its own file so it can be
 * unit-tested by `node --test` instead of only eyeballed.
 *
 * It used to live inline in app.js as a string-building function with no
 * interactivity: eight coloured wedges and a legend, and no way to ask a
 * wedge what it was. Anthony asked for hover, which is the moment a chart
 * stops being a picture and starts being something you can interrogate — so
 * the parts that decide what a slice SAYS are pure and tested here, and only
 * the event wiring lives in the browser.
 *
 * docs/BUDGETS.md applies to a chart as much as to a number: a slice states
 * an exact amount, never a rounded one dressed up as exact, and the "Other"
 * roll-up says how many categories it hides rather than pretending to be one.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Donut = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var COLORS = ["#e0592a", "#f5c542", "#5bd6c0", "#4aa3ff", "#c58cff",
    "#7bd88f", "#ff8a5b", "#38bdf8", "#a3e635", "#f2b8d0"];

  var MAX_SLICES = 8;          // past this the wedges are too thin to hit
  var CX = 100, CY = 100, R_OUT = 82, R_IN = 50;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function money(n, dp) {
    return "$" + Number(n).toLocaleString(undefined, {
      minimumFractionDigits: dp == null ? 2 : dp,
      maximumFractionDigits: dp == null ? 2 : dp,
    });
  }

  /* The slices, biggest first, with everything past MAX_SLICES rolled up.
   *
   * `pct` is kept as a real number, not a rounded one: the label rounds it for
   * display, but a caller that wants to know whether two slices are actually
   * equal should not be handed 33 and 33 for 33.4 and 32.6. */
  function slicesFor(cats) {
    var items = (cats || []).filter(function (c) { return Number(c.amount) > 0; });
    if (!items.length) return [];
    var sorted = items.slice().sort(function (a, b) { return b.amount - a.amount; });
    var top = sorted.slice(0, MAX_SLICES);
    var rest = sorted.slice(MAX_SLICES);
    if (rest.length) {
      top.push({
        name: "Other",
        amount: rest.reduce(function (s, c) { return s + Number(c.amount); }, 0),
        rolledUp: rest.length,
      });
    }
    var total = top.reduce(function (s, c) { return s + Number(c.amount); }, 0);
    return top.map(function (c, i) {
      return {
        name: String(c.name),
        amount: Number(c.amount),
        pct: (Number(c.amount) / total) * 100,
        color: COLORS[i % COLORS.length],
        rolledUp: c.rolledUp || 0,
        index: i,
      };
    });
  }

  /* What a slice says when you point at it. Exact dollars, rounded percent,
   * and — for the roll-up — how many categories are inside it, because "Other,
   * $412" invites the reader to treat one bar as one thing. */
  function sliceLabel(s) {
    var base = s.name + " " + money(s.amount) + " · " + Math.round(s.pct) + "%";
    return s.rolledUp
      ? base + " (" + s.rolledUp + " smaller categor" + (s.rolledUp === 1 ? "y" : "ies") + ")"
      : base;
  }

  function arc(frac, a0) {
    var a1 = a0 + frac * Math.PI * 2;
    var large = a1 - a0 > Math.PI ? 1 : 0;
    var pt = function (r, a) { return [CX + r * Math.cos(a), CY + r * Math.sin(a)]; };
    var p0 = pt(R_OUT, a0), p1 = pt(R_OUT, a1), p2 = pt(R_IN, a1), p3 = pt(R_IN, a0);
    var d = frac >= 0.999
      // A single full slice cannot be drawn as one arc; close it as two.
      ? "M" + (CX - R_OUT) + " " + CY + " A" + R_OUT + " " + R_OUT + " 0 1 1 " + (CX + R_OUT) + " " + CY +
        " A" + R_OUT + " " + R_OUT + " 0 1 1 " + (CX - R_OUT) + " " + CY +
        " M" + (CX - R_IN) + " " + CY + " A" + R_IN + " " + R_IN + " 0 1 0 " + (CX + R_IN) + " " + CY +
        " A" + R_IN + " " + R_IN + " 0 1 0 " + (CX - R_IN) + " " + CY + " Z"
      : "M" + p0[0].toFixed(2) + " " + p0[1].toFixed(2) +
        " A" + R_OUT + " " + R_OUT + " 0 " + large + " 1 " + p1[0].toFixed(2) + " " + p1[1].toFixed(2) +
        " L" + p2[0].toFixed(2) + " " + p2[1].toFixed(2) +
        " A" + R_IN + " " + R_IN + " 0 " + large + " 0 " + p3[0].toFixed(2) + " " + p3[1].toFixed(2) + " Z";
    return { d: d, next: a1 };
  }

  function render(cats, title) {
    var slices = slicesFor(cats);
    if (!slices.length) return "";
    var total = slices.reduce(function (s, c) { return s + c.amount; }, 0);

    var a0 = -Math.PI / 2;
    var paths = slices.map(function (s) {
      var a = arc(s.amount / total, a0);
      a0 = a.next;
      var label = sliceLabel(s);
      // tabindex + aria-label: the wedge is reachable and announced without a
      // mouse, so colour is never the only way to know what a slice is.
      return '<path class="dn-slice" d="' + a.d + '" fill="' + s.color + '"' +
        ' fill-rule="evenodd" tabindex="0" role="img"' +
        ' data-i="' + s.index + '" data-label="' + esc(label) + '"' +
        ' aria-label="' + esc(label) + '">' +
        "<title>" + esc(label) + "</title></path>";
    }).join("");

    var legend = slices.map(function (s) {
      return '<div class="row dn-row" data-i="' + s.index + '" tabindex="0" style="padding:4px 0">' +
        '<div class="grow"><span class="dn-dot" style="background:' + s.color + '"></span>' +
        "<b>" + esc(s.name) + "</b> <span class=\"sub\">" + Math.round(s.pct) + "%" +
        (s.rolledUp ? " · " + s.rolledUp + " categories" : "") + "</span></div>" +
        '<span class="right mono">' + money(s.amount) + "</span></div>";
    }).join("");

    return '<h4>' + esc(title || "Spending — last 30 days") + "</h4>" +
      '<div class="dn-wrap" style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">' +
      '<svg class="dn-svg" viewBox="0 0 200 200" width="180" height="180" style="flex:0 0 auto">' +
      paths +
      '<text class="dn-total" x="100" y="96" text-anchor="middle" fill="var(--text)" font-size="20" font-weight="700">' +
      money(total, 0) + "</text>" +
      '<text class="dn-cap" x="100" y="116" text-anchor="middle" fill="var(--muted, #8a93a6)" font-size="11">' +
      esc(title && /last (\d+)/.test(title) ? title.match(/last \d+ \w+/)[0] + " spend" : "30-day spend") +
      "</text></svg>" +
      '<div class="dn-legend" style="flex:1;min-width:200px">' + legend + "</div></div>";
  }

  return { slicesFor: slicesFor, sliceLabel: sliceLabel, render: render,
           COLORS: COLORS, MAX_SLICES: MAX_SLICES };
});
