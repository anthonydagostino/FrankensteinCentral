/* The week grid's date-dependent browser logic, kept in its own file so it can
 * be unit-tested by `node --test` instead of only eyeballed.
 *
 * docs/TESTING.md: date-dependent code goes through a single seam and is swept
 * across a calendar, never run against "today". The rest of the week grid's
 * date work happens server-side in dashboard.py, which is swept there. This is
 * the one piece that has to live in the browser, because it is about when the
 * open tab should go and ask again — so it takes `now` as an argument and is
 * swept here in the same way. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeekClock = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // A few seconds past the boundary, not exactly on it: firing at 00:00:00.000
  // races the server's own idea of the date, and losing that race re-fetches
  // the identical window and leaves yesterday on screen until the next poll.
  var GRACE_MS = 5000;

  /* Milliseconds from `now` until just after the next local midnight.
   *
   * Uses setHours(24, ...) rather than +86400000: on a 23- or 25-hour DST day
   * a fixed day of milliseconds lands an hour off, which either fires early
   * (harmless but wasteful) or an hour late (yesterday stays on screen). Date
   * normalises hour 24 into the following calendar day for us. */
  function msUntilNextMidnight(now) {
    var next = new Date(now.getTime());
    next.setHours(24, 0, 0, 0);
    return (next.getTime() - now.getTime()) + GRACE_MS;
  }

  return { msUntilNextMidnight: msUntilNextMidnight, GRACE_MS: GRACE_MS };
});
