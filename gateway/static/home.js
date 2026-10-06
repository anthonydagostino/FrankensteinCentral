/* The home screen. Renders from /api/assistant/home: the week, the money, the
 * portfolio, the resale book, the Amex credits and the weather pill. Reuses
 * the app detail-modals from app.js (openApp) for drill-downs.
 *
 * Anthony, 2026-09-09: "the only useful shit right now is the stocks, the
 * financial section, and the calendar, the rest of the main dashboard fucking
 * SUCKS." On 2026-10-06 the rest went: the daily score and its habit card, the
 * Do-Next nudge engine and its attention feed, inbox triage, Big 3, quick
 * capture, deadlines, the weekly review, the "while you were away" strip, the
 * command palette, the focus timer and the seasonal decoration. */
(function () {
  "use strict";
  const q = (s) => document.querySelector(s);
  const money = (n, d = 0) =>
    n === null || n === undefined ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const esch = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const post = (path, body) =>
    fetch("/api" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then((r) => r.json()).catch(() => ({}));

  let HOME = null;
  let APPSMAP = {};

  function toast(msg) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 1800);
  }

  // ---- data -----------------------------------------------------------------
  async function loadApps() {
    try {
      const apps = await fetch("/api/apps").then((r) => r.json());
      APPSMAP = {}; apps.forEach((a) => (APPSMAP[a.key] = a));
    } catch {}
  }
  // The seven-day window is anchored to the server's local day, so a tab left
  // open across midnight would keep showing yesterday as "Today" until a
  // manual reload. Re-fetch just after the boundary, then re-arm. The delay
  // itself lives in weekclock.js so it can be swept by node --test.
  let midnightTimer = null;
  function scheduleMidnightRollover() {
    if (midnightTimer) clearTimeout(midnightTimer);
    const delay = WeekClock.msUntilNextMidnight(new Date());
    midnightTimer = setTimeout(() => {
      refresh(true).finally(scheduleMidnightRollover);
    }, Math.max(1000, delay));
  }

  async function refresh(fresh) {
    let d;
    try {
      const res = await fetch("/api/assistant/home" + (fresh ? "?fresh=1" : ""));
      d = await res.json();
      // The service worker stamps a cached reply so the page can tell a live
      // payload from a replayed one. Without the stamp we are online.
      const cachedAt = res.headers.get("X-FC-Cached-At");
      if (cachedAt) d = Offline.staleView(d, cachedAt, new Date().toISOString());
    } catch {
      // No network AND nothing cached. Say so rather than leaving whatever is
      // on screen looking current.
      showOffline(null);
      return;
    }
    HOME = d;
    render(d);
    showOffline(d.offline);
  }

  // ---- offline ---------------------------------------------------------------
  // Opening the hub from a phone home screen with the box unreachable used to
  // give a white page. It now paints the last payload -- which immediately
  // creates the risk docs/BUDGETS.md exists to prevent, so the banner is not
  // decoration: it is the thing that stops yesterday's figures reading as
  // today's. Volatile fields are suppressed upstream in Offline.staleView.
  function showOffline(offline) {
    let el = q("#cc-offline");
    if (!el) {
      el = document.createElement("div");
      el.id = "cc-offline";
      el.setAttribute("role", "status");
      const grid = q("#cc-grid");
      if (grid && grid.parentNode) grid.parentNode.insertBefore(el, grid);
      else document.body.insertBefore(el, document.body.firstChild);
    }
    // Three states, and `null` means something different from absent:
    //   null       no network AND nothing cached -- a total failure
    //   undefined  a live payload; nothing to say
    //   object     a cached payload, which must announce itself
    if (offline === null) {
      el.hidden = false;
      el.textContent = "Offline, and nothing saved to show yet.";
    } else if (!offline || !offline.stale) {
      el.hidden = true;
      el.textContent = "";
    } else {
      el.hidden = false;
      el.textContent = Offline.banner(offline);
    }
  }

  // Register the worker. A service worker needs a SECURE CONTEXT, so over
  // plain HTTP on the LAN this does nothing at all -- the hub is not behind
  // HTTPS until Tailscale (SCRUM-48) lands. Reported rather than assumed:
  // silently doing nothing is how you end up believing offline works.
  function registerWorker() {
    if (!("serviceWorker" in navigator)) {
      console.info("[fc] offline mode unavailable: no service worker support");
      return;
    }
    if (!self.isSecureContext) {
      console.info("[fc] offline mode inactive: needs HTTPS (SCRUM-48). "
        + "The hub still works; it just will not open offline.");
      return;
    }
    navigator.serviceWorker.register("/sw.js").catch((e) => {
      console.warn("[fc] offline mode failed to register:", e && e.message);
    });
  }
  registerWorker();

  // ---- top / clock ----------------------------------------------------------
  function paintClock() {
    const now = new Date();
    q("#cc-clock").textContent = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (HOME) {
      q("#cc-greeting").textContent = HOME.greeting || "Hello";
      q("#cc-date").textContent = HOME.date_label || "";
      document.body.setAttribute("data-mode", HOME.mode || "day");
    }
  }

  // ---- render ---------------------------------------------------------------
  // A card that renders NOTHING must not look like a card that is not there.
  //
  // An empty `<section class="cc-card">` collapses to a sliver of border with
  // no content, which reads exactly like an absent card — so "the weather card
  // is missing" and "the weather card rendered nothing" were indistinguishable
  // from a screenshot, and two deploys were spent moving a card that was
  // already in the right place.
  //
  // `hidden` is left alone: a card that hides itself when it has genuinely
  // nothing to say has made a decision, not fallen silent.
  const CARD_NAMES = {
    "cc-weather": "Weather", "cc-amex": "Amex credits", "cc-calendar": "Calendar",
    "cc-money": "Money", "cc-portfolio": "Portfolio", "cc-resale": "Resale",
    "cc-systems": "Systems", "cc-deploy": "Deploy", "cc-safety": "Data safety",
  };

  function reportBlankCards() {
    // The weather pill is in the header rather than the grid, so it is named
    // explicitly — dropping out of this selector is exactly how it would go
    // back to failing silently, which is what this function exists to stop.
    document.querySelectorAll(".cc-card, .cc-wx-pill").forEach((el) => {
      if (el.hasAttribute("hidden")) return;
      if (el.innerHTML.trim()) return;
      const name = CARD_NAMES[el.id] || el.id || "This card";
      // A paragraph of explanation would wreck the header, so the pill says
      // the short version and the full one goes to the console.
      if (!el.classList.contains("cc-card")) {
        console.error("rendered nothing:", name);
        el.innerHTML = `<span class="wxp-set">${esch(name)} — no data</span>`;
        return;
      }
      el.innerHTML = `<h3>${esch(name)}</h3>
        <p class="att-empty">Rendered nothing this cycle — either the dashboard
        payload had no data under this card's key, or its renderer never ran.
        That is not the same as having nothing to show, and it is why you are
        reading this instead of an empty space.</p>`;
    });
  }

  // Paint one card without letting it take the others down.
  //
  // A dashboard is a list of independent facts. One of them failing to render
  // is a small problem; all the ones after it vanishing without a word is the
  // bug that hid the weather and amex cards through two deploys. The failure
  // is written INTO the card, not just the console, because the console is not
  // somewhere anyone looks at a dashboard from a phone.
  function paint(label, cardId, fn) {
    try {
      fn();
    } catch (err) {
      console.error("card failed to render:", label, err);
      const el = cardId && q(cardId);
      if (!el) return;
      // `hidden` is how a card stays out of the way when empty. A card that
      // just failed has something to say, so the attribute comes off.
      el.removeAttribute("hidden");
      el.innerHTML = `<h3>${esch(label)}</h3>
        <p class="att-empty">This card failed to render — ${esch(String((err && err.message) || err))}.
        Everything else on the page is unaffected. Details are in the browser console.</p>`;
    }
  }

  function render(d) {
    paintClock();
    // Each card painted in isolation. A bare sequence of calls means the FIRST
    // one to throw silently erases every card below it — the page renders
    // down to that point and simply stops, with no error anywhere a person
    // would look. One bad card is one bad card, and it SAYS so where it sits.
    paint("Calendar", "#cc-calendar", () => renderWeek(d));
    paint("Money", "#cc-money", () => renderMoney(d.money, d.budget));
    paint("Portfolio", "#cc-portfolio", () => renderPortfolio(d.portfolio));
    paint("Resale", "#cc-resale", () => renderResale(d.resale));
    paint("Amex credits", "#cc-amex", () => renderAmex(d.amex));
    paint("Weather", "#cc-weather", () => renderWeather(d.weather));
    paint("Updated stamp", null, () => {
      q("#cc-updated").textContent = "Updated " + new Date(d.last_updated || Date.now()).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    });
    paint("Systems", "#cc-systems", () => renderSystems());
    paint("Data safety", "#cc-safety", () => renderSafety(d));
    paint("Deploy", "#cc-deploy", () => renderDeploy(d));
    // Last, so it sees the finished page.
    paint("Blank-card check", null, reportBlankCards);
  }

  // ---- systems footer -------------------------------------------------------
  // The gateway probes every registered service at /api/health. Fetched
  // separately from the home payload so a slow probe never delays the page,
  // and `null` reads as "unknown", never as healthy.
  function renderSystems() {
    const el = q("#cc-systems");
    if (!el) return;
    const s = SystemsHealth.summarize(SYSHEALTH);
    el.textContent = SystemsHealth.line(s);
    el.style.color = s.state === "degraded" ? "var(--imp)" : "var(--muted)";
    // The full per-service roll is one hover away rather than always on show.
    el.title = s.state === "unknown"
      ? "The gateway's /api/health probe did not answer"
      : Object.keys(SYSHEALTH || {}).sort().map((k) =>
          `${(SYSHEALTH[k] || {}).status === "up" ? "●" : "✕"} ${k}`).join("\n");
    el.style.cursor = s.total ? "help" : "default";
  }

  let SYSHEALTH = null;
  async function loadSystems() {
    try {
      SYSHEALTH = await fetch("/api/health").then((r) => r.json());
    } catch { SYSHEALTH = null; }
    renderSystems();
  }

  // ---- the box's own deploy state -------------------------------------------
  // A failed deploy is otherwise completely silent: the previous build keeps
  // serving, so the dashboard looks perfect while the fix you shipped is not
  // the code you are looking at. Read-only — promote.sh stays the only path
  // to production and there are deliberately no controls here.
  function renderDeploy(d) {
    const el = q("#cc-deploy");
    if (!el) return;
    const v = DeployState.describe(d && d.deploy);
    el.textContent = v.text;
    el.title = v.title;
    // Only `bad` is loud. `unknown` is muted but must never be styled as ok.
    el.style.color = v.tone === "bad" ? "var(--imp)"
      : v.tone === "warn" ? "var(--imp)" : "var(--muted)";
    el.style.cursor = "help";
    el.dataset.tone = v.tone;
  }

  // ---- data safety (SCRUM-67), in the footer --------------------------------
  // "A backup you have never restored is a belief, not a backup." One line:
  // days since the last VERIFIED restore, and "never" until one happens, plus
  // whether the Firefly import is actually landing rows (SCRUM-142) — the
  // money card's figures are only as current as that import.
  //
  // Loud when there is nothing proving the data is recoverable, quiet when
  // there is. The asymmetry is the whole design. It was a card; it is an
  // operator fact, and operator facts live in the footer with the others.
  function renderSafety(d) {
    const el = q("#cc-safety");
    if (!el) return;
    const s = (d && d.data_safety) || { state: "unknown" };
    const days = (n) => (n === 0 ? "today" : n === 1 ? "1 day ago" : n + " days ago");

    const BODY = {
      never: { cls: "bad", text: "Restore never verified",
        title: "No restore has ever been verified. Until one is, these backups are a belief.\nRun: bash scripts/restore.sh --drill" },
      stale: { cls: "bad", text: `Restore verified ${s.restore_days == null ? "long ago" : days(s.restore_days)}`,
        title: "Long enough ago that the schema has probably moved since. A proof has an expiry date.\nRun: bash scripts/restore.sh --drill" },
      unknown: { cls: "muted", text: "Backup state unknown",
        title: "Can't read the backup record from here — that is not the same as being safe." },
      ok: { cls: "good", text: `Restore verified ${s.restore_days == null ? "" : days(s.restore_days)}`.trim(),
        title: s.rows ? `Last drill restored ${s.rows} rows and compared them against the backup's own record.`
                      : "Last restore drill passed." },
    };
    const b = BODY[s.state] || BODY.unknown;

    const bits = [b.text];
    // Reported separately on purpose: a fresh backup says nothing about
    // whether it can be restored, and that gap is this line's whole subject.
    if (s.backup_days != null) bits.push(`backup ${days(s.backup_days)}`);
    // "Runs but nothing enters" is the shape that hid a thin ledger for five
    // months, so the import's state is named, not rounded off into "ran".
    const imp = (d && d.import_run) || { state: "unknown" };
    const IMPORT = {
      never: ["warn", "import never run"],
      stale: ["warn", `import last tried ${imp.attempt_days != null ? days(imp.attempt_days) : "a while ago"}`],
      failed: ["warn", `import failed${imp.reason ? ` (${imp.reason})` : ""}`],
      unverified: ["warn", "import unverified"],
      quiet: ["", `import ran ${imp.attempt_days != null ? days(imp.attempt_days) : ""}, nothing new`],
      suspect: ["warn", `import runs but nothing enters${imp.ledger_ingest_days != null ? ` (ledger still for ${imp.ledger_ingest_days}d)` : ""}`],
      ok: ["", `import ${imp.rows != null ? imp.rows + " rows " : ""}${imp.landed_days != null ? days(imp.landed_days) : "landed"}`],
      unknown: ["", "import unknown"],
    };
    const [impCls, impText] = IMPORT[imp.state] || IMPORT.unknown;
    bits.push(impText);
    // Disk free on the state volume, omitted rather than invented when the
    // mount is absent: a number about the container's own disk would be the
    // wrong fact dressed as the right one.
    if (d && d.disk && d.disk.state !== "unknown" && d.disk.free_pct != null)
      bits.push(`disk ${d.disk.free_pct}% free`);

    const loud = b.cls === "bad" || impCls === "warn" || (d && d.disk && d.disk.state === "low");
    el.textContent = bits.join(" · ");
    el.title = b.title;
    el.style.color = loud ? "var(--imp)" : "var(--muted)";
    el.style.cursor = "help";
    el.dataset.tone = b.cls;
  }

  // ---- the week grid --------------------------------------------------------
  // Seven day columns: today and the next six. The server does every date
  // decision (see services/assistant/app/dashboard.py:week_window and its
  // calendar sweep); this only draws what it is handed, so the browser clock
  // and the container clock can never disagree about which day is which.
  const CAL_STATUS = {
    confirmed: { label: "confirmed" },
    pending:   { label: "offered — awaiting reply" },
    countered: { label: "they countered — needs your yes" },
  };

  // Each commitment gets its own colour, so a day reads as a set of distinct
  // things instead of one grey block, and so a recurring event keeps the same
  // colour week after week. The rules live in evcolor.js, where node --test
  // holds them to it.
  const eventColor = (e) => EventColor.of(e);

  function evRow(e) {
    const st = CAL_STATUS[e.status] || CAL_STATUS.confirmed;
    const cls = [
      "wk-ev",
      e.needs_you ? "needs-you" : "",
      e.conflict ? "clash" : "",
      e.ongoing ? "live" : "",
      e.all_day ? "allday" : "",
      e.from_google ? "from-google" : "",
    ].filter(Boolean).join(" ");
    // The status word is spelled out, never carried by the colour alone.
    const tag = e.status && e.status !== "confirmed"
      ? `<span class="wk-tag">${esch(st.label)}</span>` : "";
    const when = e.end_label
      ? `${esch(e.time_label)}<span class="wk-dash">–</span>${esch(e.end_label)}`
      : esch(e.time_label);
    const where = e.location
      ? `<span class="wk-ev-where">${esch(e.location)}</span>` : "";
    const flags = [
      e.ongoing ? `<span class="wk-flag live">now</span>` : "",
      // "Overlaps" on its own is baffling when the other commitment is in a
      // different column, so a cross-midnight clash says so.
      e.conflict ? `<span class="wk-flag clash" title="${e.conflict_offday
        ? `Overlaps a commitment on the ${e.conflict_neighbour} day`
        : "Overlaps another commitment this day"}">⚠ overlaps${
        e.conflict_offday ? ` ${esch(e.conflict_neighbour)} day` : ""}</span>` : "",
    ].filter(Boolean).join("");
    const origin = e.from_google ? "From your Google Calendar" : "Added in FrankensteinCentral";
    return `<li class="${cls}" style="--ev:${eventColor(e)}" title="${esch(origin)}">
      <span class="wk-ev-when mono">${when}</span>
      <span class="wk-ev-title">${esch(e.title || "Untitled")}</span>
      ${where}${flags}${tag}
    </li>`;
  }

  // How many commitments a day column shows before it starts counting. Three
  // is what fits the column without the tallest day dictating the height of
  // the whole grid.
  const DAY_EVENTS_SHOWN = 3;

  function dayCard(day, index) {
    const cls = [
      "wk-day",
      day.is_today ? "is-today" : "",
      day.is_weekend ? "is-weekend" : "",
      day.starts_month && !day.is_today ? "month-start" : "",
      day.conflicts ? "has-clash" : "",
    ].filter(Boolean).join(" ");

    // "Today" replaces the weekday rather than sitting next to it: on the card
    // that has one it is the more useful of the pair. The full weekday and date
    // stay in the card's aria-label regardless.
    const rel = day.relative_label
      ? `<span class="wk-rel">${esch(day.relative_label)}</span>` : "";
    const count = day.counts.total
      ? `<span class="wk-count" title="${day.counts.total} scheduled">${day.counts.total}</span>`
      : "";
    // Anthony, 2026-09-18: "make the calendar not as fucking big and long...
    // not taking up the whole page." A day had no cap, so one busy Tuesday
    // set the height of all seven columns and the card ran the length of the
    // screen. Three fit; the rest are counted, never dropped silently —
    // "+2 more" is a fact, and the tap that shows them is right there.
    const shownEvs = (day.events || []).slice(0, DAY_EVENTS_SHOWN);
    const hiddenEvs = (day.events || []).length - shownEvs.length;
    const body = day.counts.total
      ? `<ul class="wk-evs">${shownEvs.map(evRow).join("")}</ul>${
          hiddenEvs > 0
            ? `<button class="wk-more" data-day="${esch(day.iso)}">+${hiddenEvs} more</button>`
            : ""}`
      : `<p class="wk-clear">Clear</p>`;
    // The month appears only where the window actually crosses into a new one.
    // The range beside the "This week" heading already names the first month.
    const month = day.starts_month && index > 0
      ? `<span class="wk-mo">${esch(day.month_short)}</span>` : "";

    // aria-label carries the full date so the column is announced as
    // "Wednesday, October 1st, 2026", not as a bare "1".
    const label = `${day.long_label}${day.counts.total
      ? `, ${day.counts.total} scheduled` : ", nothing scheduled"}${
      day.conflicts ? ", has overlapping commitments" : ""}`;
    return `<article class="${cls}" role="listitem" tabindex="0" aria-label="${esch(label)}">
      <header class="wk-hd">
        <div class="wk-hd-top">
          <time class="wk-date" datetime="${esch(day.iso)}">
            <span class="wk-num">${day.day}</span><sup>${esch(day.ordinal_suffix)}</sup>
          </time>
          ${rel ? "" : `<span class="wk-dow">${esch(day.weekday_short)}</span>`}
          ${month}${rel}${count}
        </div>
      </header>
      ${body}
    </article>`;
  }

  function renderWeek(d) {
    const el = q("#cc-calendar");
    const week = d.week;
    const openBtn = `<button class="hx-btn" id="cal-open">Open schedule →</button>`;

    // An unreachable service is not a clear week. Saying "nothing coming up"
    // when the schedule service is down is the calmest possible way to hide a
    // real commitment, so the two states never share a rendering.
    if (!week || !week.days || !week.days.length) {
      el.innerHTML = `<h3>This week</h3>
        <p class="att-empty">Schedule unavailable.</p>
        <div class="hx-btns" style="margin-top:10px">${openBtn}</div>`;
      wireWeek();
      return;
    }
    // An outage is the only state with nothing to draw. Disconnected and
    // unknown still have real local commitments in Postgres, so the grid is
    // rendered and the caveat sits above it — hiding a week you do have is
    // its own dishonesty.
    if (week.state === "unreachable") {
      el.innerHTML = `<h3>This week</h3>
        <p class="wk-down">⚠ Can't reach the schedule service. This is an
        outage, not a clear week — commitments may exist that aren't shown
        here.</p>
        <div class="hx-btns" style="margin-top:10px">${openBtn}</div>`;
      wireWeek();
      return;
    }

    const days = week.days;
    const first = days[0], last = days[days.length - 1];
    const range = first.month === last.month
      ? `${first.month} ${first.day}–${last.day}`
      : `${first.month} ${first.day} – ${last.month} ${last.day}`;

    const clashes = days.reduce((n, x) => n + (x.conflicts ? 1 : 0), 0);
    const notes = [
      clashes ? `<span class="wk-note clash">⚠ ${clashes} day${clashes > 1 ? "s" : ""} with overlaps</span>` : "",
      week.beyond ? `<span class="wk-note">+${week.beyond} later</span>` : "",
    ].filter(Boolean).join("");

    // Five ways the grid can be incomplete, said differently, because they
    // call for different actions: reconnect, re-consent, fix the box's
    // config, wait, or nothing. A state with no entry here renders NO
    // caveat at all — a week that is missing every Google event looks
    // exactly like a complete one — which is why tests/test_schedule_state_copy.py
    // fails when the backend can emit a state this map has never heard of.
    const CAVEAT = {
      disconnected: {
        cls: "warn", icon: "⚠", fix: true,
        text: `Google Calendar isn't connected, so only commitments stored
               here are shown. A clear day may not be a free day.`,
      },
      needs_consent: {
        cls: "warn", icon: "⚠", fix: true,
        text: `Google is refusing this account's calendar. The saved Google
               login was approved for mail only, and a login keeps the
               permissions it was granted — so this will not fix itself.
               Reconnect and approve calendar access to see your real
               schedule here.`,
      },
      not_configured: {
        cls: "warn", icon: "⚠", fix: false,
        text: `Google Calendar is disconnected at the box, not at Google:
               the services share a secret (FC_INTERNAL_SECRET) and this
               deployment has not been given one, so nothing here can read
               your calendar. Set it in .env and redeploy — reconnecting
               Google will not help. Until then a clear day may not be a
               free day.`,
      },
      unknown: {
        cls: "muted", icon: "◔", fix: false,
        text: `Couldn't confirm the calendar connection, so anything that
               lives only in Google may be missing from this week.`,
      },
    };
    const cv = CAVEAT[week.state];
    // A login Google revoked says when and why. The reason is usually the
    // fix: a token that dies every seven days is an OAuth app still in
    // "Testing" mode, and reconnecting alone just buys another week.
    const g = (d.google && d.google.state === "revoked") ? d.google : null;
    const revoked = g
      ? ` Google signed this login out${g.since ? ` on ${esch(dshort(g.since))}` : ""}${
          g.reason ? ` (${esch(g.reason)})` : ""}. If this keeps happening about weekly,
          the Google Cloud app is still in Testing mode — publish it (docs/SETUP-GMAIL.md)
          so the login stops expiring.`.replace(/\s+/g, " ")
      : "";
    // The repair is a link, not a sentence telling you to go and find one.
    // /api/gmail/auth/login redirects to Google's consent screen; approving
    // there mints a credential that carries the calendar scope.
    const caveat = cv
      ? `<p class="wk-caveat ${cv.cls}">${cv.icon} ${esch(
          cv.text.replace(/\s+/g, " ").trim())}${revoked}${cv.fix
          ? ` <a class="wk-fix" href="/api/gmail/auth/login">Connect Google Calendar →</a>`
          : ""}</p>`
      : "";

    // Positive evidence, and the only kind that means anything here: a count
    // of what actually came out of Google. `state === "ok"` only says a probe
    // got an answer — it says that just as cheerfully when nothing has ever
    // been imported.
    const synced = week.state === "ok"
      ? `<span class="wk-note synced" title="Events on this week's grid that came from your Google Calendar">${
          week.from_google ? `📅 ${week.from_google} from Google` : "📅 Google Calendar connected"}</span>`
      : "";

    el.innerHTML = `
      <div class="wk-top">
        <h3>This week</h3>
        <span class="wk-range">${esch(range)}</span>
        ${synced}${notes}
        ${openBtn}
      </div>
      ${caveat}
      <div class="wk-grid" role="list">
        ${days.map((day, i) => dayCard(day, i)).join("")}
      </div>`;
    wireWeek();
  }

  function wireWeek() {
    const b = q("#cal-open");
    if (b) b.onclick = () => openAppKey("schedule");
    // "+N more" opens the schedule rather than expanding in place: growing the
    // column re-creates the height problem the cap exists to solve, and the
    // sub-app is where a full day belongs anyway.
    document.querySelectorAll(".wk-more").forEach((m) => {
      m.onclick = (e) => { e.stopPropagation(); openAppKey("schedule"); };
    });
    // Left/right move between days; the grid is one tab stop per column.
    const cards = Array.from(document.querySelectorAll(".wk-day"));
    cards.forEach((card, i) => {
      card.onkeydown = (e) => {
        const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        const next = cards[i + step];
        if (next) next.focus();
      };
    });
  }

  // "Aug 28" — short enough to sit inline in a sentence.
  const dshort = (iso) => {
    if (!iso) return "";
    const p = String(iso).slice(0, 10).split("-");
    if (p.length !== 3) return String(iso);
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+p[1] - 1]
      + " " + (+p[2]);
  };

  // ---- Money ----------------------------------------------------------------
  function renderMoney(m, bud) {
    m = m || {}; bud = bud || {};
    // Unreachable and unconfigured are different problems with different
    // fixes. Telling you to set FIREFLY_URL because a container blinked sends
    // you to repair something that isn't broken.
    if (m.state === "unreachable") {
      q("#cc-money").innerHTML = `<h3>Money</h3><p class="att-empty">Couldn't reach Firefly just now — a connection problem, not a setup one. Figures are hidden rather than guessed at.</p>`;
      return;
    }
    if (!m.connected) {
      q("#cc-money").innerHTML = `<h3>Money</h3><p class="att-empty">Firefly not connected — set FIREFLY_URL/FIREFLY_TOKEN to see live spending.</p>`;
      return;
    }
    // One statement, not two stacked banners: an empty month is the stronger
    // fact and absorbs the day count when both are true.
    const staleLine = m.month_ingested === false
      ? `<p class="mny-stale">📅 ${m.stale_days ? `Nothing imported for <b>${m.stale_days} days</b>` : "Nothing imported yet"} — <b>this month's spending is unknown, not $0</b></p>`
      : (m.stale_days
        ? `<p class="mny-stale">📅 Financial data hasn't been imported for <b>${m.stale_days} days</b> — anything spent since then is missing from these figures</p>` : "");

    // Trailing 30 days is a rolling window; the month and the pay cycle below
    // are not. The labels say so explicitly so the three can't be conflated.
    const trend30 = (m.last_30_trend_pct != null)
      ? ` <span class="mny-trend ${m.last_30_trend_pct <= 0 ? "up" : "down"}">${m.last_30_trend_pct <= 0 ? "↓" : "↑"} ${Math.abs(m.last_30_trend_pct)}%</span>`
      : "";
    // Never present a partial window as complete.
    const through30 = (m.last_30 != null && m.last_30_through && m.stale_days)
      ? ` (through ${esch(m.last_30_through)})` : "";

    // ---- the pay cycle: what's left of this paycheck --------------------
    const pay = m.paycheck || {};
    const stateCls = { over: "down", low: "warn", ok: "up" }[pay.state] || "";
    const leftVal = (pay.available && pay.left != null) ? money(pay.left) : "—";
    const leftSub = pay.available
      ? (pay.overdue
        ? "next paycheck missing from the ledger"
        : `of ${money(pay.spendable)} this paycheck · ${pay.days_to_next > 0
            ? `${pay.days_to_next} day${pay.days_to_next === 1 ? "" : "s"} to ${esch(dshort(pay.next_payday))}`
            : `payday ${esch(dshort(pay.next_payday))}`}`)
      : (pay.configured ? "not available yet"
        : `<span id="pay-setup" style="cursor:pointer;color:var(--accent-2)">set up your paycheck →</span>`);

    // One muted line: where "left to spend" came from. The hero above already
    // carries the figure itself, the window and the days to payday, so this
    // only states the paycheck, the savings taken out of it, and the pace —
    // plus any configuration problem, which stays loud. "Expected" allocations
    // are labelled as such: they are the configured amount, not something
    // seen in the ledger.
    let payLine = "";
    if (pay.available && pay.window_complete === false) {
      // Every money figure is unknown here, so the arithmetic line would read
      // "— paycheck − — to savings = —". State the reason instead.
      payLine = `<p class="mny-pay">💵 ${esch(pay.text || "Only part of this window could be read, so this paycheck's figures can't be stated.")}</p>`;
    } else if (pay.available && !pay.overdue) {
      const allocs = (pay.allocations || []).map((a) => {
        const tag = a.source === "expected" ? " expected"
          : a.source === "withheld_before_deposit" ? " pre-deposit" : "";
        return `${esch(a.name)} ${money(a.source === "withheld_before_deposit" ? a.planned : a.amount)}${tag}`;
      }).join(" · ");
      const perDay = pay.per_day != null ? ` · ~${money(pay.per_day)}/day to payday` : "";
      // Money that came back out of savings is available but is NOT part of
      // what this paycheck left you, so it is stated separately, never added.
      const fromSav = pay.from_savings
        ? `<br><span class="sub">${money(pay.from_savings)} came back out of savings this cycle — available, not counted above.</span>` : "";
      // A real transfer whose only matching rule is withheld-before-deposit:
      // deducted by nothing, so the configuration needs fixing.
      const withheldConflict = (pay.withheld_rule_conflicts || []).length
        ? `<br><span class="sub warn">⚠ ${money(pay.withheld_rule_conflicts[0].amount)} went to savings after payday but your "${esch(pay.withheld_rule_conflicts[0].rule)}" rule is marked pre-deposit, so it was not deducted. Untick pre-deposit in Settings.</span>` : "";
      // A transfer whose description says "savings" but whose accounts don't.
      // Direction can't be read from a description, so it is named here rather
      // than guessed — silently ignoring it would push "left to spend" up.
      const unmatched = (pay.unmatched_savings || []).length
        ? `<br><span class="sub warn">⚠ ${money(pay.unmatched_savings.reduce((s2, u) => s2 + (u.amount || 0), 0))} looks like savings but matches no rule's accounts — not counted. Match on the account name in Settings.</span>` : "";
      const overlap = (pay.allocation_overlaps || []).length
        ? `<br><span class="sub warn">⚠ ${esch(pay.allocation_overlaps[0])} — counted once, under the first rule. Fix the match terms in Settings.</span>` : "";
      payLine = `<p class="mny-pay">💵 ${money(pay.paycheck)} paycheck ${esch(dshort(pay.cycle_start))}
        − ${money(pay.savings_total)} savings${allocs ? ` <span class="sub">(${allocs})</span>` : ""}
        = <b>${money(pay.spendable)}</b> to spend${perDay}${fromSav}${overlap}${unmatched}${withheldConflict}
        ${!pay.fresh && pay.stale_reason ? `<br><span class="sub">Spending counted only through ${esch(pay.as_of || "the last import")} — ${esch(pay.stale_reason)}</span>` : ""}</p>`;
    } else if (pay.available && pay.overdue) {
      payLine = `<p class="mny-pay">💵 ${esch(pay.text || "The current pay cycle can't be established.")}</p>`;
    } else if (pay.configured && pay.reason) {
      payLine = `<p class="mny-pay">💵 Left to spend unavailable — ${esch(pay.reason)}.</p>`;
    }

    // The single most important budget signal — never the whole database.
    let budLine = "";
    if (bud.available && bud.configured) {
      if (!bud.fresh) {
        const imp = bud.importer_url
          ? ` <a href="${esch(bud.importer_url)}" target="_blank" rel="noopener" style="color:var(--accent-2)">Import transactions ↗</a>`
          : "";
        budLine = `<p class="bud-line paused">🫙 Budget guidance paused — ${esch(bud.paused_reason || "ledger freshness unknown")}.${imp}</p>`;
      } else if (bud.worst) {
        const w = bud.worst;
        budLine = `<p class="bud-line ${esch(w.state)}">⚠ ${esch(w.text)}</p>`;
      } else if (bud.on_track) {
        budLine = `<p class="bud-line ok">✓ All monthly budgets are on track.</p>`;
      }
    } else if (bud.available && !bud.configured) {
      budLine = `<p class="bud-line"><span id="bud-setup" style="cursor:pointer;color:var(--accent-2)">Set up monthly budgets →</span></p>`;
    }
    // ---- subscriptions: what changed since you last looked --------------
    // Only ever events. A list of every subscription belongs in the budget
    // app; the card's job is to say what you didn't already know. An
    // unavailable read renders nothing at all rather than "none found" —
    // silence about an unread ledger beats a false all-clear.
    const rec = bud.recurring || {};
    let recLine = "";
    if (rec.available && (rec.events || []).length) {
      const verb = { appeared: "started charging", resumed: "charged again after a break" };
      const per = { weekly: "wk", fortnightly: "2wk", monthly: "mo",
                    quarterly: "qtr", annual: "yr" };
      const bits = rec.events.map((e) => {
        // Two charges set a cadence but not a fact. Every event inferred from
        // one interval says so, including a price move — "Netflix went up" off
        // two charges could as easily be two unrelated purchases.
        const hedge = e.confidence === "low" ? " <i class=\"sub\">(may not be a pattern)</i>" : "";
        if (e.event === "changed")
          return `<b>${esch(e.name)}</b> ${money(e.from, 2)} → <b>${money(e.to, 2)}</b>${hedge}`;
        return `<b>${esch(e.name)}</b> ${money(e.amount, 2)}/${esch(per[e.cadence] || e.cadence || "")} — ${verb[e.event] || "changed"}${hedge}`;
      });
      const more = rec.event_count > bits.length
        ? ` · ${rec.event_count - bits.length} more` : "";
      recLine = `<p class="mny-sub mny-rec">🔁 ${bits.join(" · ")}${more}</p>`;
    }
    // ---- cash runway: the only forward-looking number on this card -------
    // Everything else here is a rear-view mirror. This one answers "how long
    // do I last", so it gets stated plainly or not at all — an optimistic
    // runway is worse than no runway, which is why the engine returns null
    // with a reason rather than a cheerful guess.
    //
    // One line. Which accounts were counted, which were dropped as debts and
    // which you excluded yourself are a tap away under "which accounts": an
    // account that is dropped must still be SEEN to be dropped (Discover once
    // fell out of this card entirely), but three sentences of account names
    // under every runway figure is what made the card a wall of text.
    const rw = m.runway || {};
    let runLine = "";
    if (rw.available && rw.months_low != null) {
      const listed = (xs) => esch((xs || []).join(", "));
      const owedN = (rw.debts || []).length;
      const details = [
        (rw.liquid_accounts || []).length ? `Counting: ${listed(rw.liquid_accounts)}.` : "",
        (rw.excluded || []).length ? `Not counted, because you said so: ${listed(rw.excluded)}.` : "",
        owedN ? `${listed(rw.debts)} ${owedN === 1 ? "carries a debt" : "carry debts"}, so ${owedN === 1 ? "it is" : "they are"} not part of the pot.` : "",
      ].filter(Boolean).join(" ");
      const why = details
        ? `<details class="mny-why"><summary>which accounts</summary><span class="sub">${details}</span></details>` : "";
      if (rw.certain) {
        const cls = rw.months < 3 ? "warn" : "";
        const alt = rw.months_without_resale != null
          ? ` · ${rw.months_without_resale} without resale` : "";
        runLine = `<div class="mny-run ${cls}">🧭 <b>${rw.months} months</b> of runway${alt}
          <span class="sub">— ${money(rw.liquid)} cash ÷ ${money(rw.burn_monthly)}/mo over the last ${rw.burn_window_days} days</span>${why}</div>`;
      } else {
        // The range is open. Lead with the FLOOR, never the ceiling: the high
        // end is the optimistic direction and the expensive one to anchor on.
        // NO warning colour on an open range: `months_low` is a BOUND, not a
        // measurement, and a colour change is an alarm.
        const amb = rw.ambiguous || [];
        runLine = `<div class="mny-run">🧭 <b>At least ${rw.months_low} months</b> of runway
          <span class="sub">— ${money(rw.liquid)} confirmed cash ÷ ${money(rw.burn_monthly)}/mo over the last ${rw.burn_window_days} days. Could be as much as ${rw.months_high} months.</span>
          <br><span class="sub">Firefly has no account type for a brokerage, so it can't say whether ${esch(amb.slice(0, 4).join(", "))}${
             amb.length > 4 ? ` and ${amb.length - 4} more` : ""
           } ${amb.length === 1 ? "is" : "are"} cash. Tick the ones that aren't cash in Settings and this becomes one number.</span>${why}</div>`;
      }
    } else if (rw.reason) {
      // Named, not blank: a missing runway with no explanation reads as a bug.
      runLine = `<div class="mny-run"><span class="sub">🧭 Runway unavailable — ${esch(rw.reason)}.</span></div>`;
    }

    // Secondary context: a rolling window and remaining budget capacity.
    // Neither is a bank balance and neither is "left to spend".
    const subBits = [];
    if (m.last_30 != null) subBits.push(`Past 30 days <b>${money(m.last_30)}</b>${trend30}${through30}`);
    if (bud.fresh && bud.budget_room != null)
      subBits.push(`Budget room <b>${money(bud.budget_room)}</b>`);
    // Committed spending, stated as a monthly figure so it is comparable to
    // the other numbers on the card. Suppressed when the read was truncated:
    // a floor presented as a total is the failure docs/BUDGETS.md forbids.
    if (rec.available && rec.window_complete !== false && rec.monthly_equivalent)
      subBits.push(`Subscriptions <b>${money(rec.monthly_equivalent)}</b>/mo across ${rec.tracked}${rec.annual_equivalent ? ` (≈ ${money(rec.annual_equivalent)}/yr)` : ""}`);
    const subLine = subBits.length ? `<p class="mny-sub">${subBits.join(" · ")}</p>` : "";

    // The Firefly sub-app's headline figures, here so they cost no clicks.
    // A missing value renders as an em dash, never as $0 — unknown is not zero.
    //
    // Two of these are dropped when the pay cycle is available, because the
    // hero above already answers them from better evidence and the same word
    // meaning two different numbers on one card is the exact failure
    // docs/BUDGETS.md exists to prevent:
    //   * "Spent (mo)" is Firefly's raw month total; the hero's is the same
    //     month with savings transfers taken back out.
    //   * "Left to spend" is Firefly's own budget arithmetic; the hero's is
    //     this paycheck minus its savings minus what's been spent since.
    // Without a pay cycle configured, both stay — nothing is lost.
    const ffTiles = [
      ["Net worth", m.net_worth],
      ["Earned (mo)", m.earned],
      ...(pay.available ? [] : [["Spent (mo)", m.spent], ["Left to spend", m.left_to_spend]]),
    ].map(([l, v]) => `<div class="mny-stat"><div class="v mono">${v ? esch(v) : "—"}</div><div class="l">${esch(l)}</div></div>`).join("");

    // Spending by category, last 30 days — the Firefly sub-app's own donut,
    // called rather than reimplemented (app.js loads first and defines it
    // globally). One implementation means the dashboard and the sub-app
    // cannot drift into showing the same data two different ways.
    const catPie = (typeof spendingDonut === "function")
      ? spendingDonut(m.categories, "Spending by category · last 30 days")
      : "";

    // What is owed across Firefly's liability accounts, beside "left to spend"
    // rather than folded into it: Firefly derives that figure from budgets and
    // spending, and quietly changing someone else's number is how a dashboard
    // stops agreeing with the ledger it claims to mirror.
    //
    // "No liability accounts" is NOT "$0 owed", and it says so. That is the
    // state you are in when cards are entered as Firefly ASSETS, which is the
    // setup runway.py records as the known-bad one — reporting it as no debt
    // is the most flattering possible reading of a ledger that has never been
    // told about the cards.
    const owed = m.owed || { state: "unreachable" };
    const owedLine = owed.state === "ok"
      ? `<div class="mny-owed">
          <span class="mo-lead">${owed.partial ? "at least " : ""}${esch(money(owed.total, 2))}</span>
          <span class="mo-unit">owed on cards</span>
          <span class="mo-cards">${(owed.cards || []).map((c) =>
            `<span class="mo-card"><b>${esch(c.name)}</b> ${
              c.owed == null ? "—" : esch(money(c.owed, 2))}</span>`).join("")}</span>
          ${owed.partial ? `<span class="mo-note">One balance could not be read, so this is a
            floor rather than the total.</span>` : ""}
        </div>`
      : owed.state === "no_liabilities"
        ? `<div class="mny-owed"><span class="mo-note">No liability accounts in Firefly, so
            card balances are not being counted anywhere. Cards entered as
            <b>asset</b> accounts do not show here — re-enter them as liabilities, or
            tick them under Settings so the runway stops treating them as cash.</span></div>`
        : "";

    const accts = (m.accounts || []).map((a) =>
      `<div class="pos"><span>${esch(a.name)}</span><span class="mono">${a.balance != null ? money(a.balance, 2) : "—"}</span></div>`).join("");
    q("#cc-money").innerHTML = `
      <h3>Money</h3>
      ${staleLine}
      <div class="mny-hero">
        <div class="mny-stat"><div class="v">${m.month == null ? "—" : (m.month_complete === false ? "at least " : "") + money(m.month)}</div><div class="l">${m.month_label ? `Spent in ${esch(m.month_label.split(" ")[0])}` : "Spent this month"}<br><span style="font-size:10px">${m.month_complete === false ? "more transactions than could be read — a floor, not the total" : `month to date${m.month_savings ? ` · ${money(m.month_savings)} to savings not counted` : ""}`}</span></div></div>
        <div class="mny-stat"><div class="v mono ${stateCls}">${leftVal}</div><div class="l">Left to spend<br><span style="font-size:10px">${leftSub}</span></div></div>
        <div class="mny-stat"><div class="v mono">${m.today != null ? money(m.today) : "—"}</div><div class="l">Today</div></div>
      </div>
      ${owedLine}
      ${payLine}${runLine}${budLine}${recLine}${subLine}
      <div class="mny-hero mny-ff">${ffTiles}</div>
      ${catPie}
      ${accts ? `<h3 style="margin-top:14px">Accounts</h3><div class="acct-grid">${accts}</div>` : ""}
      <div class="hx-btns" style="margin-top:10px">
        <button class="hx-btn" id="money-budget">Budget →</button>
        <button class="hx-btn" id="money-firefly">Transactions →</button>
      </div>`;
    q("#money-firefly").onclick = () => openAppKey("firefly");
    q("#money-budget").onclick = () => openAppKey("budget");
    const setup = q("#bud-setup");
    if (setup) setup.onclick = () => openSettings();
    const paySetup = q("#pay-setup");
    if (paySetup) paySetup.onclick = () => openSettings();
  }

  function renderPortfolio(p) {
    p = p || { state: "unreachable" };
    // Three states, not two. A stocks service that is down is NOT an empty
    // portfolio, and telling you to "add your stocks" over a transient blip
    // sends you to fix configuration that is already correct.
    if (p.state === "unreachable") {
      q("#cc-portfolio").innerHTML = `<h3>Portfolio</h3>
        <p class="att-empty">Couldn't reach the stocks service just now — a
        connection problem, not a setup one. Your holdings are unchanged;
        figures are hidden rather than guessed at.</p>`;
      return;
    }
    if (p.state === "not_configured" || !p.configured) {
      q("#cc-portfolio").innerHTML = `<h3>Portfolio</h3>
        <p class="att-empty">No holdings yet. <b id="pf-add" style="cursor:pointer;color:var(--accent-2)">Add your stocks →</b><br>Then you'll see daily change, movers & watchlist here.</p>`;
      const a = q("#pf-add"); if (a) a.onclick = () => openSettings();
      return;
    }
    // The "Alert on move >= (%)" setting produces an alert.
    const alerts = p.alerts || [];
    const alertLine = alerts.length
      ? `<div class="pf-alert">⚡ ${alerts.length} past your ${esch(String(p.move_threshold_pct))}% alert: `
        + alerts.slice(0, 4).map((a) =>
            `<b class="${a.direction}">${esch(a.symbol)} ${a.change_pct >= 0 ? "+" : ""}${a.change_pct}%</b>`).join(", ")
        + (alerts.length > 4 ? ` +${alerts.length - 4} more` : "") + `</div>`
      : "";
    const dc = p.day_change || 0, cls = dc >= 0 ? "up" : "down", arrow = dc >= 0 ? "▲" : "▼";
    const mv = p.movers || {};
    const moverRow = (x, label) => x ? `<div class="pos"><span>${label} <b>${esch(x.symbol)}</b></span><span class="${x.change_pct >= 0 ? "up" : "down"} mono">${x.change_pct >= 0 ? "+" : ""}${x.change_pct}% · ${x.day_change >= 0 ? "+" : ""}${money(x.day_change)}</span></div>` : "";
    const live = (p.positions || []).filter((x) => x.available);
    const dead = (p.positions || []).filter((x) => !x.available);
    const positions = live.map((x) =>
      `<div class="pos"><span><b>${esch(x.symbol)}</b> <span class="${x.change_pct >= 0 ? "up" : "down"}">${x.change_pct >= 0 ? "+" : ""}${x.change_pct}%</span></span><span class="mono">${money(x.value)}</span></div>`).join("");
    // Unsupported/unreachable symbols must be visible, never silently vanish.
    const deadLine = dead.length
      ? `<p class="att-empty" style="margin-top:8px">⚠ No quote for ${dead.map((x) => esch(x.symbol)).join(", ")} — symbol not on the quote source, or it's unreachable. Other holdings still shown.</p>` : "";
    const noneLive = !live.length
      ? `<p class="att-empty">Quotes unavailable right now — your ${(p.positions || []).length} holding(s) are saved and will price when the quote source responds.</p>` : "";
    q("#cc-portfolio").innerHTML = `
      <h3>Portfolio</h3>
      ${live.length ? `<div class="mny-hero">
        <div class="mny-stat"><div class="v mono ${cls}">${arrow} ${p.day_change_pct}%</div><div class="l">${esch(p.session_label || "Last session")} · ${dc >= 0 ? "+" : ""}${money(dc)}${p.session_label ? "" : `<br><span style="font-size:10px">as-of date unavailable</span>`}</div></div>
        <div class="mny-stat"><div class="v mono" style="font-size:17px">${money(p.value)}</div><div class="l">Value</div></div>
        ${p.total_gain != null ? `<div class="mny-stat"><div class="v mono ${p.total_gain >= 0 ? "up" : "down"}" style="font-size:17px">${p.total_gain >= 0 ? "+" : ""}${money(p.total_gain)}</div><div class="l">Total gain</div></div>` : ""}
      </div>` : ""}
      ${alertLine}
      ${moverRow(mv.up, "▲")}${moverRow(mv.down, "▼")}
      <div style="margin-top:6px">${positions}</div>${noneLive}${deadLine}`;
  }

  // ---- resale (SCRUM-139) ---------------------------------------------------
  // Money with a deadline: PowerBuy's expiring buys are the only figure on
  // this page that can be lost by waiting.
  function renderResale(r) {
    const el = q("#cc-resale");
    if (!el) return;
    r = r || { state: "unreachable" };

    // Three states, not two. An unreachable service is not an empty book, and
    // this is the card whose entire job is to say when money is about to be
    // lost — rendering an outage as "$0 expected, 0 expiring" would be the
    // most reassuring possible version of something it does not know.
    if (r.state === "unreachable") {
      el.innerHTML = `<h3>Resale</h3>
        <p class="att-empty">Couldn't reach PowerBuy just now — a connection
        problem, not an empty book. Figures are hidden rather than guessed at.</p>`;
      return;
    }
    if (r.state === "not_configured") {
      el.innerHTML = `<h3>Resale</h3>
        <p class="att-empty">PowerBuy isn't connected — set POWERBUY_EMAIL and
        POWERBUY_PASSWORD to see your purchases here.</p>`;
      return;
    }

    // The lead figure is whichever one is actually urgent. Expiring buys have
    // a deadline and the profit number does not, so on any day something is
    // expiring that is the headline; otherwise the money you expect to make is.
    const money = (v) => "$" + Number(v || 0).toLocaleString(undefined,
      { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    const lead = r.urgent
      ? `<span class="rs-lead urgent">${r.expiring}</span>
         <span class="rs-unit">expiring within 7 days</span>`
      : `<span class="rs-lead">${esch(money(r.profit))}</span>
         <span class="rs-unit">profit expected</span>`;
    const sub = [
      r.urgent && r.profit != null ? `<span>${esch(money(r.profit))} <b>expected</b></span>` : "",
      r.unpaid ? `<span><b>${r.unpaid}</b> unpaid</span>` : "",
      r.in_flight ? `<span><b>${r.in_flight}</b> not delivered</span>` : "",
      r.total != null ? `<span><b>${r.total}</b> tracked</span>` : "",
    ].filter(Boolean).join("");

    el.innerHTML = `<h3>Resale</h3>
      <div class="rs-row">${lead}</div>
      <div class="rs-sub">${sub}</div>
      <div class="hx-btns" style="margin-top:12px">
        <button class="hx-btn" id="rs-open">Open PowerBuy →</button>
      </div>`;
    const b = q("#rs-open");
    if (b) b.onclick = () => openAppKey("powerbuy");
  }

  // ---- Amex credits --------------------------------------------------------
  // Statement credits that reset on a calendar boundary and do NOT roll over.
  // Same shape of fact as the resale card: money with a deadline, which is the
  // only kind of number that earns a place on a screen you glance at.
  function renderAmex(a) {
    const el = q("#cc-amex");
    if (!el) return;
    a = a || { state: "unreachable" };
    // Cents only where there are cents. Walmart+ is $12.95 and rounding it to
    // $13 on a card about exact amounts is a small lie for no gain; $200 does
    // not need ".00" to be read.
    const money = (v) => {
      const n = Number(v || 0);
      const dp = Number.isInteger(n) ? 0 : 2;
      return "$" + n.toLocaleString(undefined,
        { minimumFractionDigits: dp, maximumFractionDigits: dp });
    };
    const days = (n) => (n === 1 ? "1 day left" : n + " days left");

    // An unreachable service is not a week with nothing expiring. These credits
    // die at midnight, so a quiet card has to mean "nothing due", never "we did
    // not look".
    if (a.state !== "ok") {
      el.innerHTML = `<h3>Amex credits</h3>
        <p class="att-empty">Couldn't reach the Amex tracker just now — that is
        not the same as nothing expiring. Figures are hidden rather than
        guessed at.</p>`;
      return;
    }

    const urgent = a.urgent || [];
    const lead = urgent.length
      ? `<span class="ax-lead urgent">${esch(money(a.at_risk))}</span>
         <span class="ax-unit">expiring within 7 days</span>`
      : `<span class="ax-lead">${esch(money(a.available))}</span>
         <span class="ax-unit">unused this period</span>`;

    const rows = urgent.slice(0, 5).map((c) => `
      <li class="ax-row">
        <span class="ax-card ${esch(c.card)}">${c.card === "platinum" ? "PLAT" : "GOLD"}</span>
        <span class="ax-name">${esch(c.name)}${c.enroll
          ? `<em class="ax-enroll" title="Enrollment required — an unenrolled credit pays nothing">enroll</em>` : ""}</span>
        <span class="ax-amt">${esch(money(c.amount))}</span>
        <span class="ax-days${c.days_left <= 2 ? " hot" : ""}">${esch(days(c.days_left))}</span>
        <button class="ax-done" data-amex="${esch(c.key)}" title="Mark used for this period">used</button>
      </li>`).join("");

    const quiet = !urgent.length && a.soonest_days != null
      ? `<p class="ax-note">Nothing expiring this week. Next up:
         <b>${esch(a.soonest_name || "")}</b>, ${esch(days(a.soonest_days))}.</p>` : "";

    // Year to date against the two annual fees — the only figure that answers
    // "should I keep these cards". Rendered only when the service sent it: a
    // missing net is not a net of zero, and $0 vs "unknown" is the difference
    // between breaking even and not having looked.
    const ytd = a.ytd_net == null ? "" : `
      <p class="ax-note">Used <b>${esch(money(a.ytd_captured))}</b> of credits
      this year against ${esch(money(a.annual_fees))} in fees —
      <b class="${a.ytd_net >= 0 ? "ax-ahead" : "ax-behind"}">${
        a.ytd_net >= 0 ? "ahead by " : "behind by "}${esch(money(Math.abs(a.ytd_net)))}</b>.</p>`;

    el.innerHTML = `<h3>Amex credits</h3>
      <div class="ax-top">${lead}</div>
      ${rows ? `<ul class="ax-list">${rows}</ul>` : quiet}
      ${ytd}
      <div class="hx-btns" style="margin-top:12px">
        <button class="hx-btn" id="ax-open">All credits →</button>
      </div>`;

    el.querySelectorAll("[data-amex]").forEach((b) => (b.onclick = async () => {
      b.disabled = true;
      await post("/amex/used", { credit_key: b.dataset.amex, used: true });
      toast("Marked used");
      refresh(true);
    }));
    const open = q("#ax-open");
    if (open) open.onclick = () => openAppKey("amex");
  }

  // ---- Weather (header pill) -----------------------------------------------
  // One line beside the greeting: sky, degrees, high and low, the rest of
  // today by the hour, place. The ten days are a tap away in the sub-app.
  //
  // The big number is either true or absent. A temperature is the one thing
  // here read without being read, and 0° from a timed-out request is
  // believable in February.
  function renderWeather(w) {
    const el = q("#cc-weather");
    if (!el) return;
    w = w || { state: "unreachable" };
    const deg = w.degree || "°";
    const t = (v) => (v == null ? "—" : Math.round(v) + deg);
    el.onclick = () => openAppKey("weather");

    if (w.state === "not_configured") {
      // Fixable in ten seconds, so it reads as an invitation rather than a
      // fault. Clicking it opens the place picker.
      el.className = "cc-wx-pill empty";
      el.title = "No location set — click to pick one";
      el.innerHTML = `<span class="wxp-g">🌤️</span><span class="wxp-set">Set location</span>`;
      return;
    }
    if (w.state !== "ok") {
      el.className = "cc-wx-pill empty";
      el.title = "Couldn't reach the forecast. No temperature is shown rather "
        + "than a stale one.";
      el.innerHTML = `<span class="wxp-g">⛅</span><span class="wxp-set">Weather —</span>`;
      return;
    }

    el.className = "cc-wx-pill";
    el.title = `${w.label || "Weather"}${w.place ? " · " + w.place : ""}`
      + (w.feels_like != null ? ` · feels ${Math.round(w.feels_like)}${deg}` : "")
      + (w.stale ? " · not current" : "");
    // The rest of today by the hour, inline. The service decides how many —
    // "the rest of today" is 23 chips at 1am and none at 11pm, so dashboard.py
    // bounds it and rolls past midnight late in the evening rather than
    // showing a stub exactly when the next few hours matter most.
    const hours = (w.hourly || []).map((h) => `
      <span class="wxp-h" title="${esch((h.label || "") + (h.precip_pct != null ? ` · ${h.precip_pct}% rain` : ""))}">
        <b>${esch(h.hour === 0 ? "12a" : h.hour < 12 ? h.hour + "a"
          : h.hour === 12 ? "12p" : (h.hour - 12) + "p")}</b>
        <i>${esch(t(h.temp))}</i>
      </span>`).join("");

    el.innerHTML = `
      <span class="wxp-g">${esch(w.glyph || "")}</span>
      <span class="wxp-t">${esch(t(w.temp))}</span>
      <span class="wxp-x">H ${esch(t(w.high))} · L ${esch(t(w.low))}</span>
      ${hours ? `<span class="wxp-hrs">${hours}</span>` : ""}
      ${w.place ? `<span class="wxp-p">${esch(w.place)}</span>` : ""}
      ${w.stale ? `<span class="wxp-stale" title="The forecast service was unreachable, so this is the last reading we got">old</span>` : ""}`;
  }

  // ---- actions --------------------------------------------------------------
  function openAppKey(key) {
    const app = APPSMAP[key];
    if (!app) { toast("App '" + key + "' isn't registered — try a refresh."); return; }
    if (typeof openApp !== "function") {
      // app.js didn't load/execute (usually a stale cached copy). Be loud.
      toast("Stale page detected — hard-refresh (Ctrl+Shift+R) to load the new version.");
      console.error("openApp missing: app.js stale or failed to load");
      return;
    }
    openApp(app);
  }

  // ---- wiring ---------------------------------------------------------------
  q("#cc-apps").onclick = openLauncher;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !q("#launcher").hidden) closeLauncher();
  });

  // ---- apps & services launcher ---------------------------------------------
  // The one place to reach every sub-app: tile -> dashboard modal, ↗ -> the
  // full underlying application (Firefly, Plex, importer…).
  let EXT_LINKS = null;  // cached {key: url}
  async function externalLinks() {
    if (EXT_LINKS) return EXT_LINKS;
    const links = {};
    try {
      const ff = await fetch("/api/firefly/summary").then((r) => r.json());
      if (ff.web_url) links.firefly = ff.web_url;
      if (ff.importer_url) links._importer = ff.importer_url;
    } catch {}
    try {
      const px = await fetch("/api/plex/summary").then((r) => r.json());
      if (px.web_url) links.plex = px.web_url;
    } catch {}
    try {
      const vw = await fetch("/api/vault/summary").then((r) => r.json());
      if (vw.web_url) links.vault = vw.web_url;
    } catch {}
    EXT_LINKS = links;
    return links;
  }

  // ---- launch buttons: Plex and Vaultwarden, on the main screen --------------
  // Not behind the launcher. The two things opened every day are one click from
  // the home screen. Plex always has somewhere to go (app.plex.tv even when the
  // sub-app is not connected); Vaultwarden needs VAULTWARDEN_WEB_URL and SAYS
  // so on click rather than 404ing — an instruction on this dashboard has to
  // point at a control that exists. Titles stay "Open Plex" / "Open
  // Vaultwarden" so a screen reader announces the destination, not an emoji.
  async function wireLaunchButtons() {
    const links = await externalLinks();
    const plex = q("#cc-plex"), vault = q("#cc-vault");
    if (plex) { plex.title = "Open Plex"; if (links.plex) plex.href = links.plex; }
    if (!vault) return;
    if (links.vault) {
      vault.href = links.vault;
      vault.title = "Open Vaultwarden";
      vault.classList.remove("unconfigured");
      vault.onclick = null;
    } else {
      vault.classList.add("unconfigured");
      vault.title = "Vaultwarden — set VAULTWARDEN_WEB_URL in .env to enable";
      vault.onclick = (e) => { e.preventDefault(); toast("Set VAULTWARDEN_WEB_URL in .env (e.g. http://<box>:8222) and redeploy"); };
    }
  }
  wireLaunchButtons();
  async function openLauncher() {
    q("#launcher").hidden = false;
    const grid = q("#launcher-grid");
    let health = {};
    try { health = await fetch("/api/health").then((r) => r.json()); } catch {}
    const links = await externalLinks();
    const tiles = Object.values(APPSMAP).map((a) => {
      const st = (health[a.key] || {}).status || "";
      const ext = links[a.key]
        ? `<a class="ext" href="${links[a.key]}" target="_blank" rel="noopener" title="Open the full app">↗</a>` : "";
      return `<div class="launch-tile" data-key="${esch(a.key)}">
        <span class="ic">${a.icon || "▦"}</span><span class="nm">${esch(a.name)}</span>
        <span class="dot ${st}" title="${st || "unknown"}"></span>${ext}</div>`;
    });
    if (links._importer) {
      tiles.push(`<a class="launch-tile" href="${links._importer}" target="_blank" rel="noopener" title="Firefly data importer">
        <span class="ic">📥</span><span class="nm">Importer</span><span class="dot"></span><span class="ext">↗</span></a>`);
    }
    grid.innerHTML = tiles.join("") || '<p class="att-empty">No apps registered.</p>';
    grid.querySelectorAll(".launch-tile[data-key]").forEach((el) => {
      el.onclick = (e) => {
        if (e.target.closest(".ext")) return;  // let the deep link navigate
        closeLauncher();
        openAppKey(el.dataset.key);
      };
    });
  }
  function closeLauncher() { q("#launcher").hidden = true; }
  q("#launcher-close").onclick = closeLauncher;
  q("#launcher").onclick = (e) => { if (e.target.id === "launcher") closeLauncher(); };

  // ---- settings -------------------------------------------------------------
  // Only what the cards on this page read: holdings for the portfolio, the
  // budgets and pay cycle for the money card, and which Firefly accounts are
  // not spendable cash for the runway. The weather location is set from the
  // weather sub-app, where you can see the forecast you are choosing.
  //
  // Accounts as the ledger reports them, kept by index so a name never has to
  // survive a round-trip through an HTML attribute — `esch` does not escape
  // quotes and an account is named by the user.
  let _runwayAccounts = [];
  let _financeSettings = {};
  async function openSettings() {
    let s = {};
    try { s = await fetch("/api/core/settings").then((r) => r.json()); } catch {}
    let nw = {};
    try { nw = await fetch("/api/firefly/networth").then((r) => r.json()); } catch {}
    // Only asset-side accounts can be candidates: a Firefly liability or a
    // ccAsset card is already excluded by the engine and offering it here
    // would imply the choice matters.
    _runwayAccounts = ((nw || {}).accounts || []).filter(
      (a) => a && a.kind !== "liability" && a.role !== "ccAsset");
    _financeSettings = s.finance || {};
    const mk = (h) => h.map((c) => c.symbol + ":" + c.shares + (c.cost ? ":" + c.cost : "")).join("\n");
    const pc = s.paycheck || {};
    q("#settings-body").innerHTML = `
      <div class="set-group"><h4>Investments</h4><div class="set-grid">
        <div class="set-field" style="grid-column:1/-1"><label>Holdings — one per line (or comma-separated): SYMBOL shares cost — cost optional, fractional shares OK</label>
          <textarea id="s-hold" placeholder="NVDA 10 150&#10;AAPL 2.5&#10;VOO:1.25:380">${esch(mk(((s.market || {}).holdings) || []))}</textarea></div>
        <div class="set-field" style="grid-column:1/-1"><label>Watchlist (comma-separated symbols)</label>
          <input id="s-watch" value="${esch((((s.market || {}).watchlist) || []).join(", "))}"></div>
        <div class="set-field"><label>Alert on move ≥ (%)</label><input id="s-mv" type="number" value="${(s.market || {}).move_threshold_pct ?? 3}"></div>
      </div></div>
      <div class="set-group"><h4>Paycheck &amp; savings</h4>
        <p class="set-hint">Powers <b>Left to spend</b> on the Money card: the paycheck that landed,
        minus the savings that come out of it, minus what you've spent since. Matching is
        case-insensitive against a transaction's description and account names.</p>
        <div class="set-grid">
          <div class="set-field" style="grid-column:1/-1"><label>Paycheck deposit contains (comma-separated)</label>
            <input id="s-pay-match" value="${esch((pc.match || []).join(", "))}" placeholder="payroll, direct dep"></div>
          <div class="set-field"><label>Minimum deposit ($)</label><input id="s-pay-min" type="number" value="${pc.min_amount ?? 500}"></div>
          <div class="set-field"><label>Pay every (days)</label><input id="s-pay-cad" type="number" value="${pc.cadence_days ?? 14}"></div>
          <div class="set-field"><label>Track left to spend</label>
            <select id="s-pay-on"><option value="1"${pc.enabled === false ? "" : " selected"}>Yes</option><option value="0"${pc.enabled === false ? " selected" : ""}>No</option></select></div>
        </div>
        <p class="set-hint">Savings that come out of each paycheck. A real transfer in Firefly is
        used when there is one; otherwise the amount below is treated as expected and labelled that
        way. Tick <b>pre-deposit</b> only if your employer takes it before the money lands (then it
        is shown but not subtracted twice).</p>
        <div id="s-allocs"></div>
        <button class="hx-btn" id="s-alloc-add" type="button">+ Add savings deduction</button>
      </div>
      <div class="set-group"><h4>Monthly budgets</h4>
        <p class="set-hint">Each budget maps to one or more Firefly category names. Spending in those
        categories fills the budget's vessel on the Budget page.</p>
        <div id="s-budgets"></div>
        <button class="hx-btn" id="s-budget-add" type="button">+ Add budget</button>
        <p class="set-hint" id="s-cat-hint"></p>
      </div>
      <div class="set-group"><h4>Cash runway — which of these is not spendable cash?</h4>
        <div class="set-field" style="grid-column:1/-1"><label>Firefly has no account type for a brokerage, so it cannot tell a current account from a retirement fund — both are "defaultAsset". Tick the ones that are NOT money you could spend this month. Until you do, the runway is shown as a range instead of a number.</label>
        <div class="set-runway">${
          _runwayAccounts.length
            ? _runwayAccounts.map((a, i) => `<label class="set-check"><input type="checkbox" class="runway-x" data-i="${i}"${
                (_financeSettings.not_spendable || []).includes(a.name) ? " checked" : ""
              }> ${esch(a.name)}</label>`).join("")
            : `<span class="muted">No accounts to show — Firefly did not answer.</span>`
        }</div></div>
      </div>`;
    q("#settings").hidden = false;

    // budgets editor: one row per budget (name / $limit / mapped categories)
    const budRow = (b) => {
      const div = document.createElement("div");
      div.className = "budget-row";
      div.innerHTML = `
        <input class="b-name" placeholder="Name (e.g. Dining)" value="${esch(b.name || "")}">
        <input class="b-limit" type="number" min="1" step="1" placeholder="$/mo" value="${b.limit ?? ""}">
        <input class="b-cats" placeholder="Firefly categories, comma-separated" value="${esch((b.categories || []).join(", "))}">
        <button class="b-x" type="button" title="remove">✕</button>`;
      div.querySelector(".b-x").onclick = () => div.remove();
      return div;
    };
    const wrap = q("#s-budgets");
    (s.budgets || []).forEach((b) => wrap.appendChild(budRow(b)));
    if (!(s.budgets || []).length) wrap.appendChild(budRow({}));
    q("#s-budget-add").onclick = () => wrap.appendChild(budRow({}));

    // savings-deduction editor: name / $amount / match terms / pre-deposit
    const allocRow = (a) => {
      const div = document.createElement("div");
      div.className = "alloc-row";
      div.innerHTML = `
        <input class="a-name" placeholder="Name (e.g. Fidelity)" value="${esch(a.name || "")}">
        <input class="a-amt" type="number" min="0" step="1" placeholder="$/paycheck" value="${a.amount ?? ""}">
        <input class="a-match" placeholder="matches (comma-separated)" value="${esch((a.match || []).join(", "))}">
        <label class="a-pre" title="Employer takes it before the deposit lands"><input type="checkbox" class="a-withheld"${a.already_withheld ? " checked" : ""}> pre-deposit</label>
        <button class="a-x" type="button" title="remove">✕</button>`;
      div.querySelector(".a-x").onclick = () => div.remove();
      return div;
    };
    const awrap = q("#s-allocs");
    (pc.allocations || []).forEach((a) => awrap.appendChild(allocRow(a)));
    if (!(pc.allocations || []).length) awrap.appendChild(allocRow({}));
    q("#s-alloc-add").onclick = () => awrap.appendChild(allocRow({}));

    // category hints from live budget status (what Firefly actually has)
    fetch("/api/budget/status").then((r) => r.json()).then((st) => {
      const names = new Set();
      (st.budgets || []).forEach((b) => (b.categories || []).forEach((c) => names.add(c)));
      Object.keys((st.unbudgeted || {}).categories || {}).forEach((c) => names.add(c));
      if (names.size) q("#s-cat-hint").textContent =
        "Categories seen in Firefly this month: " + [...names].slice(0, 12).join(" · ");
    }).catch(() => {});
  }
  // Tolerant holdings parser — token-stream based, so ANY separator mix works:
  // "MET, 1.63" · "NVDA 10 150" · "aapl:2.5" · "VOO: 1.25 : 380" · all on one
  // line or one per line. Grammar: a symbol (letters) starts a holding; the
  // following 1–2 numbers are shares [and cost]. Fractional shares supported.
  // Returns what parsed AND what didn't, so the UI stays honest.
  function parseHoldings(text) {
    const atoms = (text || "")
      .split(/[\s,:\n]+/)
      .map((a) => a.trim().replace(/^\$/, ""))
      .filter(Boolean)
      .filter((a) => !/^(sh|shs|share|shares|of|x|@)$/i.test(a));  // filler words
    const holdings = [], rejected = [];
    let cur = null;
    const flush = () => {
      if (!cur) return;
      if (cur.nums.length >= 1 && parseFloat(cur.nums[0]) > 0) {
        const o = { symbol: cur.sym, shares: parseFloat(cur.nums[0]) };
        if (cur.nums[1] != null) o.cost = parseFloat(cur.nums[1]);
        holdings.push(o);
      } else {
        rejected.push(cur.sym + " (no share count)");
      }
      cur = null;
    };
    for (const a of atoms) {
      if (/^[A-Za-z][A-Za-z.^-]{0,11}$/.test(a)) {
        flush();
        cur = { sym: a.toUpperCase(), nums: [] };
      } else if (/^\d*\.?\d+$/.test(a)) {
        if (cur && cur.nums.length < 2) cur.nums.push(a);
        else rejected.push(a);
      } else {
        rejected.push(a);
      }
    }
    flush();
    return { holdings, rejected };
  }
  async function saveSettings() {
    const num = (id, d) => { const v = Number(q(id).value); return Number.isFinite(v) ? v : d; };
    const list = (id) => q(id).value.split(",").map((x) => x.trim()).filter(Boolean);
    const status = q("#settings-status");

    const parsed = parseHoldings(q("#s-hold").value);
    if (parsed.rejected.length) {
      status.textContent = "⚠ Couldn't read: " + parsed.rejected.slice(0, 3).map((r) => `"${r}"`).join(", ")
        + " — use SYMBOL shares [cost], e.g.  NVDA 10 150  or  AAPL:2.5";
      status.style.color = "var(--down)";
      return;  // don't silently drop the user's input — let them fix it
    }

    // budget rows: a row with any content must have a name and a limit > 0
    const budgets = [];
    for (const row of q("#settings-body").querySelectorAll(".budget-row")) {
      const name = row.querySelector(".b-name").value.trim();
      const limit = Number(row.querySelector(".b-limit").value);
      const cats = row.querySelector(".b-cats").value.split(",").map((c) => c.trim()).filter(Boolean);
      if (!name && !limit && !cats.length) continue;  // untouched blank row
      if (!name || !(limit > 0)) {
        status.textContent = `⚠ Budget row "${name || "(unnamed)"}" needs a name and a monthly limit above 0.`;
        status.style.color = "var(--down)";
        return;
      }
      budgets.push({ id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), name, limit, categories: cats });
    }

    // savings deductions: a touched row needs a name; a blank amount is 0
    const allocations = [];
    for (const row of q("#settings-body").querySelectorAll(".alloc-row")) {
      const name = row.querySelector(".a-name").value.trim();
      const amount = Number(row.querySelector(".a-amt").value) || 0;
      const match = row.querySelector(".a-match").value.split(",").map((c) => c.trim()).filter(Boolean);
      const withheld = row.querySelector(".a-withheld").checked;
      if (!name && !amount && !match.length) continue;   // untouched blank row
      if (!name) {
        status.textContent = "⚠ Every savings deduction needs a name (it's what gets matched in Firefly).";
        status.style.color = "var(--down)";
        return;
      }
      allocations.push({ name, amount, match: match.length ? match : [name], already_withheld: withheld });
    }

    const patch = {
      paycheck: {
        enabled: q("#s-pay-on").value === "1",
        match: list("#s-pay-match"),
        min_amount: num("#s-pay-min", 500),
        cadence_days: num("#s-pay-cad", 14),
        allocations: allocations,
      },
      // Merged, not replaced: anything else `finance` carries survives a save
      // of the picker rather than being dropped by a bare {not_spendable}.
      finance: { ..._financeSettings, not_spendable:
        [...q("#settings-body").querySelectorAll(".runway-x")]
          .filter((c) => c.checked)
          .map((c) => (_runwayAccounts[Number(c.dataset.i)] || {}).name)
          .filter(Boolean) },
      budgets: budgets,
      market: { holdings: parsed.holdings, watchlist: list("#s-watch").map((s) => s.toUpperCase()), move_threshold_pct: num("#s-mv", 3) },
    };
    status.style.color = "";
    status.textContent = "Saving…";
    let saved = null;
    try {
      const res = await fetch("/api/core/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
      if (res.ok) saved = await res.json();
    } catch {}
    if (!saved || typeof saved !== "object" || saved.error) {
      status.textContent = "✗ Save failed — the core service didn't accept it. Try again or check the stack.";
      status.style.color = "var(--down)";
      return;  // keep the modal open; never claim success on failure
    }
    // Round-trip confirmation: report what the SERVER now holds, not what we sent.
    const n = ((saved.market || {}).holdings || []).length;
    const nb = (saved.budgets || []).length;
    status.textContent = `Saved ✓ — ${n} holding${n === 1 ? "" : "s"}, ${nb} budget${nb === 1 ? "" : "s"} stored`;
    fetch("/api/budget/status?fresh=1").catch(() => {});  // recompute budgets now
    setTimeout(() => { q("#settings").hidden = true; status.textContent = ""; refresh(true); }, 900);
  }
  window.ccOpenSettings = openSettings;  // used by the Budget deep view's setup button
  q("#cc-settings-btn").onclick = openSettings;
  q("#settings-close").onclick = () => (q("#settings").hidden = true);
  q("#settings-save").onclick = saveSettings;
  q("#settings").onclick = (e) => { if (e.target.id === "settings") q("#settings").hidden = true; };

  // ---- boot -----------------------------------------------------------------
  setInterval(paintClock, 1000);
  (async function boot() {
    await loadApps();
    await refresh(true);
    loadSystems();
    scheduleMidnightRollover();
    // background refresh every 60s (skip while a modal is open)
    setInterval(() => {
      if (q("#overlay").classList.contains("open")) return;
      if (!q("#settings").hidden || !q("#launcher").hidden) return;
      refresh(false);
      loadSystems();
    }, 60000);
  })();
})();
