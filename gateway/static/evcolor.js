/* Colour for one calendar event, kept in its own file so `node --test` can
 * hold it to its rules instead of them being eyeballed once and then quietly
 * drifting — the same reason weekclock.js is its own file.
 *
 * The rules, in order:
 *   1. Status wins over identity. An offer awaiting a reply is amber whatever
 *      it is called, because that colour carries a fact rather than decorating
 *      one. Only confirmed events get a hue of their own.
 *   2. A hue is derived from the title, never from the row's position — so a
 *      recurring commitment is the same colour every week, and reordering a
 *      day never repaints it.
 *   3. Red is reserved. --imp marks overlaps and things needing your
 *      attention, so generated hues skip the band around it: a routine dentist
 *      appointment must not come out the same colour as a scheduling clash.
 *
 * Colour is never the sole carrier of meaning in the grid — the status word,
 * the overlap flag and the time all stay in text. This decides appearance. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EventColor = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATUS_COLOR = { pending: "#f2c14e", countered: "#ff9f5b" };

  // 25°–344°: everything except the reds around 0°.
  var HUE_FLOOR = 25, HUE_SPAN = 320;

  /* djb2, clamped to 32 bits. Any stable hash does; this one is short and
   * spreads the handful of titles in a week well enough. */
  function hueOf(str) {
    var h = 5381;
    for (var i = 0; i < str.length; i++) {
      h = ((h << 5) + h + str.charCodeAt(i)) | 0;
    }
    return HUE_FLOOR + (Math.abs(h) % HUE_SPAN);
  }

  function of(event) {
    event = event || {};
    var forced = STATUS_COLOR[event.status];
    if (forced) return forced;
    // Fixed saturation and lightness across every hue, so no event shouts over
    // its neighbour purely because of where it landed on the wheel; 68%
    // lightness stays legible against the dark panel at every hue.
    return "hsl(" + hueOf(event.title || "untitled") + ", 62%, 68%)";
  }

  return { of: of, hueOf: hueOf, STATUS_COLOR: STATUS_COLOR,
           HUE_FLOOR: HUE_FLOOR, HUE_SPAN: HUE_SPAN };
});
