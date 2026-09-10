/* Whether Do-Next and the attention feed are showing the same thing.
 *
 * Its own file so `node --test` can hold it to the rule, the same reason
 * weekclock.js and evcolor.js are their own files.
 *
 * The two surfaces share nudge keys ON PURPOSE — waving off "study" has to
 * quiet it in both places, because it is one thing and not two. The cost of
 * that is they are frequently the same item, and the page drew it twice: once
 * as a 180px hero, then again as the first attention row a few pixels below,
 * carrying the same title and the same button. Where they agree the feed keeps
 * it, because the feed is the list you scan, and the hero stands down. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Attention = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* True only when Do-Next is provably the same ITEM as one already in the
   * feed. Matching is by key and nothing else: two things can share a title
   * ("Reply to Dana") and be different emails, and suppressing on a
   * coincidence would silently drop a real recommendation.
   *
   * A Do-Next with no key cannot be matched, so it always shows — the calm
   * "You're on track" fallback carries no key and must never be suppressed. */
  function isDuplicate(doNext, nudges) {
    if (!doNext || !doNext.key) return false;
    if (!Array.isArray(nudges)) return false;
    return nudges.some(function (n) { return n && n.key === doNext.key; });
  }

  return { isDuplicate: isDuplicate };
});
