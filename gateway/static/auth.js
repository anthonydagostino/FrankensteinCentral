/* Login plumbing for the front door (SCRUM-98). Loaded FIRST.
 *
 * Two jobs, both small:
 *
 * 1. A 401 from any same-origin /api/ call means the session is missing or
 *    expired. home.js has ~20 raw fetch() sites and no shared helper, so rather
 *    than touch each one this wraps window.fetch once and sends the browser to
 *    /login, remembering where it was. Nothing else about the response is
 *    changed: callers still see exactly what the gateway sent.
 *
 * 2. When no password is configured the gateway is deliberately open (see
 *    gateway/app/auth.py for why that is fail-OPEN and not fail-closed). Open
 *    is a choice; SILENT is the bug. So the page says so, at the top, until
 *    GATEWAY_PASSWORD is set.
 *
 * Exposed on window.FCAuth for the tests; nothing else reads it.
 */
"use strict";
(function () {
  // `base` is the page origin. Passed explicitly by the tests; in a browser it
  // falls back to the real location. No `self` dereference before the check,
  // or this file cannot load under node at all.
  function origin(base) {
    if (base) return base;
    return (typeof self !== "undefined" && self.location) ? self.location.href : "http://localhost/";
  }

  function isApi(url, base) {
    try {
      var o = new URL(origin(base));
      var u = new URL(url, o.href);
      return u.origin === o.origin && u.pathname.indexOf("/api/") === 0
        && u.pathname !== "/api/auth/status";
    } catch (e) { return false; }
  }

  function loginUrl(here) {
    return "/login?next=" + encodeURIComponent(here || "/");
  }

  // Returns true when the response means "go log in". Separated from the
  // side effect so it can be tested without a window to navigate.
  function needsLogin(res, url, base) {
    return !!res && res.status === 401 && isApi(url, base);
  }

  var redirected = false;   // twenty parallel 401s should navigate once
  function install(win) {
    var real = win.fetch;
    if (!real || real.__fc_wrapped) return;
    var wrapped = function (input, init) {
      var url = (typeof input === "string") ? input : (input && input.url) || "";
      return real.call(win, input, init).then(function (res) {
        if (!redirected && needsLogin(res, url, win.location.href)) {
          redirected = true;
          win.location.assign(loginUrl(win.location.pathname + win.location.search));
        }
        return res;
      });
    };
    wrapped.__fc_wrapped = true;
    win.fetch = wrapped;
  }

  function bannerText(status) {
    if (!status || status.configured !== false) return null;
    return "No dashboard password is set — anyone on this network can open this page. " +
      "Set GATEWAY_PASSWORD in the box's .env (see .env.example).";
  }

  function showBanner(doc, text) {
    if (!text || doc.getElementById("fc-auth-banner")) return;
    var el = doc.createElement("div");
    el.id = "fc-auth-banner";
    el.setAttribute("role", "alert");
    el.style.cssText = "background:#5a1d1d;color:#ffd7d7;border-bottom:1px solid #a33;" +
      "padding:8px 14px;font:13px system-ui,sans-serif;text-align:center";
    el.textContent = text;
    (doc.body || doc.documentElement).insertBefore(el, (doc.body || doc.documentElement).firstChild);
  }

  // The sign-out control exists in the markup but is hidden until we know it
  // means something: a password is set AND this browser holds a session.
  // Showing it with no password configured would offer an action that does
  // nothing, which is worse than no button.
  function showLogout(doc, status) {
    var el = doc.getElementById("fc-logout");
    if (!el) return false;
    var show = !!(status && status.configured === true && status.authenticated === true);
    el.hidden = !show;
    return show;
  }

  var api = { isApi: isApi, loginUrl: loginUrl, needsLogin: needsLogin,
              bannerText: bannerText, install: install, showBanner: showBanner,
              showLogout: showLogout };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;                  // node tests
  } else if (typeof window !== "undefined") {
    window.FCAuth = api;
    var unwrapped = window.fetch;
    install(window);
    // Use the ORIGINAL fetch for the status probe so a 401 elsewhere cannot
    // race it, then paint the banner once the body exists.
    unwrapped.call(window, "/api/auth/status").then(function (r) { return r.json(); })
      .then(function (s) {
        var paint = function () { showBanner(document, bannerText(s)); showLogout(document, s); };
        if (document.body) paint(); else document.addEventListener("DOMContentLoaded", paint);
      }).catch(function () {});
  }
})();
