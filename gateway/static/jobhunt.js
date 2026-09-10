/* Where the job-hunt research lives, and what the page may say about it.
 *
 * SCRUM-131. jobs.html saved every edit to localStorage — and said so in its
 * own subtitle. That put the weighted ranking, the per-factor scores, the
 * pros and cons and the salary floors in exactly one browser: whichever of
 * the five machines they were typed into. Not synced, not in the database
 * backup, gone on "clear site data".
 *
 * The data now lives in core, one row per key, under the same jobhunt_*
 * names the page always used. The fetch plumbing is thin and stays in the
 * page; the decisions that have to be right are pure and tested here:
 *
 *   * which localStorage keys are ours — and only ours — to migrate
 *   * what a first load does when a browser still holds a local copy:
 *     import it if the server has nothing, do NOTHING SILENT if both hold
 *     different data, and clear it if it is already identical to the server
 *   * what the "saved" stamp may claim. It used to say "Saved just now (this
 *     browser)" the instant localStorage.setItem returned. It now says
 *     "saved" only after the server acknowledged the write, and a failed
 *     write is named as failed rather than left looking saved.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Jobhunt = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var KEY = /^jobhunt_[a-z0-9_]{1,120}$/;

  function isKey(k) { return typeof k === "string" && KEY.test(k); }

  // Every jobhunt_* entry in a Storage-like object, and nothing else. The hub
  // keeps its own keys (theme, snoozes) in the same localStorage; those are
  // not ours to move.
  function localEntries(storage) {
    var out = {};
    if (!storage || typeof storage.length !== "number") return out;
    for (var i = 0; i < storage.length; i++) {
      var k = storage.key(i);
      if (!isKey(k)) continue;
      var v = storage.getItem(k);
      if (typeof v === "string") out[k] = v;
    }
    return out;
  }

  function count(o) { return Object.keys(o || {}).length; }

  function subsetOf(local, server) {
    var keys = Object.keys(local);
    for (var i = 0; i < keys.length; i++) {
      if (server[keys[i]] !== local[keys[i]]) return false;
    }
    return true;
  }

  // What a first load should do with whatever this browser still holds.
  //
  //   none    nothing local; nothing to do
  //   import  local data, empty server: push it up, then clear it. This is
  //           the one-time migration, and it must never lose what was typed.
  //   clear   every local value already matches the server: a leftover from
  //           an interrupted migration, safe to drop, nothing would be lost
  //   ask     BOTH hold data and they differ. Two browsers each had their own
  //           copy before the move, and there is no honest automatic answer
  //           to "which one is right". Say so, and let the person pick.
  function migrationPlan(server, local) {
    server = server || {};
    local = local || {};
    if (count(local) === 0) return { action: "none" };
    if (count(server) === 0) return { action: "import", entries: local };
    if (subsetOf(local, server)) return { action: "clear" };
    return { action: "ask", local: count(local), server: count(server) };
  }

  // The per-card stamp. `saved` is only ever the server's word.
  function savedLabel(state) {
    var s = state && state.status;
    if (s === "saved") return "Saved — on the server, follows you between machines";
    if (s === "saving") return "Saving…";
    if (s === "failed") return "NOT SAVED — the server did not confirm this edit";
    return "Not edited yet";
  }

  // Page-level notices. Each names what is and is not true right now.
  function unreachableBanner() {
    return "Could not reach the server. Showing the research defaults — your "
      + "saved edits are NOT shown, and edits made now will not be saved "
      + "until it is back.";
  }
  function importFailedBanner() {
    return "This browser's local copy of your research could not be sent to "
      + "the server. It has been kept here, untouched.";
  }
  function askBanner(plan) {
    return "This browser still holds an older local copy of your research ("
      + plan.local + " saved fields) and the server already has a different "
      + "one (" + plan.server + "). They will not be merged automatically — "
      + "choose which to keep.";
  }

  return {
    isKey: isKey, localEntries: localEntries, migrationPlan: migrationPlan,
    savedLabel: savedLabel, unreachableBanner: unreachableBanner,
    importFailedBanner: importFailedBanner, askBanner: askBanner, KEY: KEY,
  };
});
