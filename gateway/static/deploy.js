/* The dashboard's own deploy state (PRODUCT_IDEAS #24).
 *
 * The box records what it is running in ~/.frankenstein/deployed.json, and
 * until now that was visible only by SSH-ing in and running
 * frankenstein-status.sh. The case that matters is a FAILED deploy: deploy.sh
 * only advances running_commit on success, so a failed attempt leaves the
 * previous build serving, answering every request perfectly, with nothing
 * anywhere in the UI to say the fix you shipped is not the code you are
 * looking at.
 *
 * Read-only by design. promote.sh stays the only path to production; there are
 * no controls here, just the truth about what is running.
 *
 * Pure and DOM-free so `node --test` can exercise it, same as health.js. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DeployState = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MIN = 60, HOUR = 3600, DAY = 86400;

  /* "3h ago" / "2d ago", or null when we do not know.
   *
   * null rather than "just now": an unknown age is not a recent one, and
   * substituting the flattering reading is exactly what this whole card is
   * built to stop. A negative age means the record is ahead of our clock —
   * also unknown, not "in the future". */
  function ago(seconds) {
    if (seconds == null || typeof seconds !== "number" ||
        !isFinite(seconds) || seconds < 0) return null;
    if (seconds < MIN) return "just now";
    if (seconds < HOUR) return Math.floor(seconds / MIN) + "m ago";
    if (seconds < DAY) return Math.floor(seconds / HOUR) + "h ago";
    return Math.floor(seconds / DAY) + "d ago";
  }

  function short(sha) {
    return typeof sha === "string" && sha ? sha.slice(0, 7) : null;
  }

  /* What the footer may say. Returns {tone, text, title}.
   *
   * tone is "bad" | "warn" | "ok" | "unknown" — never "ok" for a state we did
   * not establish. `unknown` and `current` must not collapse into one another:
   * "we cannot see the box" is not "the box is up to date". */
  function describe(d) {
    if (!d || typeof d !== "object" || !d.state) {
      return { tone: "unknown", text: "◔ Build unknown",
               title: "No deploy record is readable from here." };
    }
    var running = short(d.running), attempted = short(d.attempted);
    var age = ago(d.age_seconds), attemptAge = ago(d.attempt_age_seconds);

    if (d.state === "failed") {
      /* The loud one. Name both commits: which build you are looking at, and
       * which one failed to replace it. */
      return {
        tone: "bad",
        text: "⚠ Deploy failed — still on " + (running || "an earlier build"),
        title: "The last deploy attempt (" + (attempted || "unknown commit") +
               (attemptAge ? ", " + attemptAge : "") + ") did not succeed" +
               (d.last_result ? " (" + d.last_result + ")" : "") + ".\n" +
               "The box kept serving " + (running || "its previous build") +
               (age ? ", deployed " + age : "") + ".\n" +
               "What you are looking at is NOT the latest promoted commit.",
      };
    }
    if (d.state === "pending") {
      return {
        tone: "warn",
        text: "◔ No confirmed deploy yet",
        title: "A deploy record exists but names no successfully deployed " +
               "commit. This says nothing about whether containers are up.",
      };
    }
    if (d.state === "current") {
      return {
        tone: "ok",
        text: "● Build " + (running || "current") + (age ? " · " + age : ""),
        title: "Running " + (running || "the promoted commit") +
               (age ? ", deployed " + age : "") + ". Last deploy succeeded.",
      };
    }
    return { tone: "unknown", text: "◔ Build unknown",
             title: "The deploy record is absent or self-contradictory, so " +
                    "what the box is running cannot be established from here." };
  }

  return { describe: describe, ago: ago };
});
