/* Summarising the gateway's own /api/health probe (PRODUCT_IDEAS #14).
 *
 * The footer used to compute its health claim from two of fifteen services —
 * core and gmail — so firefly, schedule, budget, stocks, finance, tasks,
 * networth, vault, deals, plex and powerbuy could all be down while it still
 * said "Systems healthy". The gateway has always probed all fifteen at
 * GET /api/health and the UI discarded the answer.
 *
 * Pure and separated from the DOM so `node --test` can exercise it, same as
 * weekclock.js. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SystemsHealth = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Summarise {key: {status: "up"|"down", ...}} into what the footer may claim.
   *
   * Three states, not two. A probe we could not perform is `unknown`, NEVER
   * healthy: the whole defect being fixed here is a footer that reported
   * health it had not established. An empty or unparseable payload means the
   * gateway did not answer, which tells us nothing about the services. */
  function summarize(aggregate) {
    if (!aggregate || typeof aggregate !== "object" || Array.isArray(aggregate)) {
      return { state: "unknown", down: [], up: 0, total: 0 };
    }
    var keys = Object.keys(aggregate);
    if (!keys.length) return { state: "unknown", down: [], up: 0, total: 0 };

    var down = [];
    var up = 0;
    for (var i = 0; i < keys.length; i++) {
      var entry = aggregate[keys[i]];
      var status = entry && entry.status;
      if (status === "up") up += 1;
      else if (status === "down") down.push(keys[i]);
      // Anything else is neither up nor down: counted in total, claimed as
      // neither. It must not silently inflate the healthy count.
    }
    down.sort();

    var state;
    if (down.length) state = "degraded";
    else if (up === keys.length) state = "healthy";
    else state = "unknown";  // some service reported something we don't know

    return { state: state, down: down, up: up, total: keys.length };
  }

  /* The one line the footer shows. Names the services when any are down —
   * "a stopped container makes the footer say so by name" is idea #14's
   * stated acceptance signal. */
  function line(summary) {
    if (summary.state === "healthy") {
      return "● All " + summary.total + " systems healthy";
    }
    if (summary.state === "degraded") {
      var n = summary.down.length;
      return "⚠ " + n + " of " + summary.total + " down: " + summary.down.join(", ");
    }
    return "◔ Couldn't check systems";
  }

  return { summarize: summarize, line: line };
});
