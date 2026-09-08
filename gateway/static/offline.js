/* What the dashboard is allowed to say when it is running on a cached payload.
 *
 * PRODUCT_IDEAS #7. Opening the hub from a phone home screen with the box
 * unreachable used to give a white page. A service worker fixes that by
 * serving the last /home response — which immediately creates the problem
 * docs/BUDGETS.md exists to prevent: yesterday's figures rendered as though
 * they were today's. "$65 spent today" is not a harmless staleness; it is a
 * false statement about a number the user acts on.
 *
 * So the decisions about WHAT A STALE PAYLOAD MAY CLAIM are pure and tested
 * here, and only the service-worker plumbing lives in sw.js. The rules:
 *
 *   * a cached payload is always labelled, with the time it was captured
 *   * anything time-of-day sensitive is suppressed rather than shown stale —
 *     a score, a "today" figure and a "starts in 20 min" are wrong the moment
 *     they are old, in a way a net worth or a deadline list is not
 *   * age is stated in the units a person uses, and an unknown age says so
 *     rather than defaulting to "just now"
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Offline = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Requests whose response is worth keeping for an offline render. The home
  // payload is the dashboard; everything else is either static (handled by the
  // shell cache) or a mutation, which must never be replayed from a cache.
  var CACHEABLE_API = ["/api/assistant/home"];

  function isCacheableApi(url, method) {
    if ((method || "GET").toUpperCase() !== "GET") return false;
    var path = String(url || "").split("?")[0];
    for (var i = 0; i < CACHEABLE_API.length; i++) {
      if (path === CACHEABLE_API[i] || path.indexOf(CACHEABLE_API[i] + "/") === 0) {
        return true;
      }
    }
    return false;
  }

  // A mutation must never be served from a cache, and must never be quietly
  // swallowed when offline: "logged" when nothing was logged is worse than an
  // error the user can see.
  function isMutation(method) {
    var m = (method || "GET").toUpperCase();
    return m === "POST" || m === "PUT" || m === "PATCH" || m === "DELETE";
  }

  function ageMinutes(cachedAt, now) {
    var a = Date.parse(cachedAt), b = Date.parse(now);
    if (isNaN(a) || isNaN(b)) return null;
    return (b - a) / 60000;
  }

  /* How old, in words. `null` when it cannot be worked out — an unknown age
   * must not render as "just now", which is the most reassuring possible
   * reading of the least information. */
  function ageLabel(cachedAt, now) {
    var mins = ageMinutes(cachedAt, now);
    if (mins === null) return "at an unknown time";
    if (mins < 0) return "at an unknown time";      // clock skew: do not guess
    if (mins < 2) return "moments ago";
    if (mins < 60) return Math.round(mins) + " min ago";
    if (mins < 60 * 36) return Math.round(mins / 60) + "h ago";
    return Math.round(mins / 1440) + "d ago";
  }

  /* Fields that are only true at the moment they were computed. Suppressed
   * rather than shown stale, because each is a claim about NOW:
   *   score/health   today's progress, wrong from the next log onward
   *   money.today    "spent today", flatly false on a later day
   *   do_next        "head to X, it starts in 20 minutes" — the worst one
   *   since          a diff against a baseline, meaningless when replayed
   *   deploy         what the box is running, which is what we cannot reach
   */
  var VOLATILE = ["score", "do_next", "since", "deploy", "health"];

  /* Strip a cached payload down to what is still honestly sayable, and tell
   * the UI it is doing so. Never mutates the input. */
  function staleView(payload, cachedAt, now) {
    var out = {};
    var src = payload && typeof payload === "object" ? payload : {};
    for (var k in src) {
      if (Object.prototype.hasOwnProperty.call(src, k) && VOLATILE.indexOf(k) === -1) {
        out[k] = src[k];
      }
    }
    if (out.money && typeof out.money === "object") {
      // "today" is the only money figure that is a claim about now; the month
      // and the paycheck window remain broadly true and are worth showing.
      var money = {};
      for (var m in out.money) {
        if (Object.prototype.hasOwnProperty.call(out.money, m) && m !== "today") {
          money[m] = out.money[m];
        }
      }
      money.today = null;
      out.money = money;
    }
    out.offline = {
      stale: true,
      cached_at: cachedAt || null,
      age_label: ageLabel(cachedAt, now),
      suppressed: VOLATILE.concat(["money.today"]),
    };
    return out;
  }

  function banner(offline) {
    if (!offline || !offline.stale) return "";
    return "Offline — showing the state from " + offline.age_label
      + ". Live figures are hidden rather than shown stale.";
  }

  return {
    isCacheableApi: isCacheableApi, isMutation: isMutation,
    ageMinutes: ageMinutes, ageLabel: ageLabel, staleView: staleView,
    banner: banner, VOLATILE: VOLATILE, CACHEABLE_API: CACHEABLE_API,
  };
});
