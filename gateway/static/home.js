/* Command center — the Today home screen.
 * Renders from /api/assistant/home and wires the quick actions (core service),
 * the focus timer, quick capture, and the Cmd/Ctrl-K command palette. Reuses
 * the app detail-modals from app.js (openApp) for deep dives. */
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
      d = await fetch("/api/assistant/home" + (fresh ? "?fresh=1" : "")).then((r) => r.json());
    } catch { return; }
    HOME = d;
    render(d);
  }

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
  function render(d) {
    paintClock();
    q("#cc-score-n").textContent = (d.score && d.score.score) || 0;
    q("#cc-briefing").innerHTML = (d.briefing || [])
      .map((b) => `<span class="cc-chip">${esch(b)}</span>`).join("");
    renderSince(d);
    renderWeeklyReview(d.weekly_review);
    renderWeek(d);
    renderDoNext(d.do_next, d);
    renderInbox(d.inbox);
    renderMoney(d.money, d.budget);
    renderPortfolio(d.portfolio);
    renderToday(d);
    renderHealth(d);
    renderCapture(d.captures);
    q("#cc-updated").textContent = "Updated " + new Date(d.last_updated || Date.now()).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    renderSystems();
    renderDeploy(d);
    saveSnapshot(d);
  }

  // ---- since last check (per-device via localStorage) -----------------------
  function snapshot(d) {
    return {
      ts: Date.now(),
      importantIds: (d.inbox && d.inbox.items || []).filter((i) => i.important).map((i) => i.id),
      spendMonth: d.money && d.money.month,
      spendToday: d.money && d.money.today,
      port: d.portfolio && d.portfolio.day_change_pct,
      portVal: d.portfolio && d.portfolio.value,
      score: d.score && d.score.score,
    };
  }
  function saveSnapshot(d) {
    try { localStorage.setItem("cc_snap", JSON.stringify(snapshot(d))); } catch {}
  }
  function renderSince(d) {
    let prev;
    try { prev = JSON.parse(localStorage.getItem("cc_snap") || "null"); } catch {}
    const el = q("#cc-since");
    if (!prev || Date.now() - prev.ts < 15 * 60 * 1000) { el.hidden = true; return; } // only after a 15-min+ gap
    const bits = [];
    const nowImp = (d.inbox && d.inbox.items || []).filter((i) => i.important).map((i) => i.id);
    const newImp = nowImp.filter((id) => !(prev.importantIds || []).includes(id)).length;
    if (newImp) bits.push(`<span class="it"><b>${newImp}</b> new important email${newImp > 1 ? "s" : ""}</span>`);
    if (prev.portVal != null && d.portfolio && d.portfolio.value != null) {
      const dp = (d.portfolio.day_change_pct || 0);
      if (Math.abs(dp) >= 0.1) bits.push(`<span class="it">Portfolio <b>${dp >= 0 ? "+" : ""}${dp}%</b> today</span>`);
    }
    if (prev.spendToday != null && d.money && d.money.today != null) {
      const diff = d.money.today - prev.spendToday;
      if (diff > 0) bits.push(`<span class="it"><b>$${Math.round(diff)}</b> new spending</span>`);
    }
    if (d.score && prev.score != null && d.score.score !== prev.score) {
      bits.push(`<span class="it">Score ${d.score.score > prev.score ? "up" : "down"} to <b>${d.score.score}</b></span>`);
    }
    if (!bits.length) bits.push('<span class="it">No new alerts. You\'re current.</span>');
    el.innerHTML = `<span class="lbl">Since you last checked</span>${bits.join("")}<button class="x" title="dismiss">✕</button>`;
    el.querySelector(".x").onclick = () => (el.hidden = true);
    el.hidden = false;
  }

  // ---- schedule -------------------------------------------------------------
  // Pending and countered holds are shown, not filtered. Bones proposes slots
  // from your sent mail and writes them to the real calendar; a slot awaiting
  // a reply is the single most actionable thing the pipeline produces, and it
  // used to be dropped before it ever reached the page.
  const CAL_STATUS = {
    confirmed: { dot: "🟢", label: "confirmed" },
    pending:   { dot: "🟡", label: "offered — awaiting reply" },
    countered: { dot: "🟠", label: "they countered — needs your yes" },
  };
  // ---- systems footer (PRODUCT_IDEAS #14) ------------------------------------
  // The footer used to compute its claim from `d.systems`, which the assistant
  // builds from core and gmail alone — so eleven other services could be down
  // while it said "Systems healthy". The gateway probes all fifteen at
  // /api/health and the UI discarded it. Now it doesn't.
  let SYSHEALTH = null;
  async function loadSystems() {
    try {
      SYSHEALTH = await fetch("/api/health").then((r) => r.json());
    } catch { SYSHEALTH = null; }   // null reads as "unknown", not healthy
    renderSystems();
  }

  function renderSystems() {
    const el = q("#cc-systems");
    if (!el) return;
    const s = SystemsHealth.summarize(SYSHEALTH);
    el.textContent = SystemsHealth.line(s);
    el.style.color = s.state === "degraded" ? "var(--imp)"
      : s.state === "unknown" ? "var(--muted)" : "var(--muted)";
    // The full per-service roll is one click away rather than always on show.
    el.title = s.state === "unknown"
      ? "The gateway's /api/health probe did not answer"
      : Object.keys(SYSHEALTH || {}).sort().map((k) =>
          `${(SYSHEALTH[k] || {}).status === "up" ? "●" : "✕"} ${k}`).join("\n");
    el.style.cursor = s.total ? "help" : "default";
  }


  // ---- the box's own deploy state (PRODUCT_IDEAS #24) ------------------------
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

  // ---- weekly review (PRODUCT_IDEAS #5) --------------------------------------
  // core has served GET /weekly-review since it was written and nothing ever
  // rendered it. On Sunday evening it takes the top of the page; the rest of
  // the week it sits above the calendar, quietly.
  function wrRow(label, row, unit, fmt) {
    if (!row) return "";
    const f = fmt || ((v) => `${v}${unit || ""}`);
    // "No goal set" and "unknown" are NOT 0% — reporting either as failure is
    // the same dishonesty the money layer's rules forbid.
    if (row.state === "unknown") {
      return `<div class="wr-row"><span class="wr-l">${esch(label)}</span>
        <span class="wr-v wr-unknown">—</span>
        <span class="wr-note">not recorded</span></div>`;
    }
    if (row.state === "no_goal") {
      return `<div class="wr-row"><span class="wr-l">${esch(label)}</span>
        <span class="wr-v">${esch(f(row.value))}</span>
        <span class="wr-note">no goal set</span></div>`;
    }
    const pct = Math.max(0, Math.min(100, row.pct));
    return `<div class="wr-row"><span class="wr-l">${esch(label)}</span>
      <span class="wr-v">${esch(f(row.value))} <i>/ ${esch(f(row.goal))}</i></span>
      <span class="wr-bar"><i style="width:${pct}%" class="${row.state}"></i></span>
      <span class="wr-pct ${row.state}">${row.pct}%</span></div>`;
  }

  function renderWeeklyReview(wr) {
    const el = q("#cc-weekly");
    // An unreachable core is not a week where nothing happened: no card at all
    // beats a card full of zeroes.
    if (!wr) { el.hidden = true; return; }
    el.hidden = false;
    el.classList.toggle("lead", wr.slot === "lead");

    const mins = (v) => (v >= 60 ? `${Math.floor(v / 60)}h ${v % 60}m`.replace(" 0m", "") : `${v}m`);
    const t = wr.study && wr.study.trend_min;
    const trend = (t === null || t === undefined || t === 0) ? ""
      : `<span class="wr-trend ${t > 0 ? "up" : "down"}">${t > 0 ? "▲" : "▼"} ${esch(mins(Math.abs(t)))} vs last week</span>`;

    el.innerHTML = `<h3>${wr.slot === "lead" ? "Your week" : "This week so far"}</h3>
      ${wrRow("Study", wr.study, "", mins)}
      ${wrRow("Gym", wr.gym, "")}
      ${wrRow("Water", wr.water, " days")}
      ${trend}`;
  }

  // ---- the week grid --------------------------------------------------------
  // Seven day columns: today and the next six. The server does every date
  // decision (see services/assistant/app/dashboard.py:week_window and its
  // calendar sweep); this only draws what it is handed, so the browser clock
  // and the container clock can never disagree about which day is which.

  // Decoration only. A season is picked from the date and changes accent and
  // motif — never text colour, never what the data says.
  const SEASONS = {
    jan: { name: "Deep Winter", glyph: "❄", drift: ["❄", "❅", "❆"] },
    feb: { name: "Sweetheart", glyph: "♥", drift: ["♥", "♡"] },
    mar: { name: "First Green", glyph: "☘", drift: ["☘", "❀"] },
    apr: { name: "Showers", glyph: "☂", drift: ["☂", "ᴗ"] },
    may: { name: "Bloom", glyph: "✿", drift: ["✿", "❀", "✾"] },
    jun: { name: "Solstice", glyph: "☀", drift: ["☀", "✺"] },
    jul: { name: "Fireworks", glyph: "✺", drift: ["✺", "✹"] },
    aug: { name: "High Summer", glyph: "⛱", drift: ["⛱", "≋"] },
    sep: { name: "Harvest", glyph: "✾", drift: ["🌾", "🍃"] },
    oct: { name: "Pumpkin Season", glyph: "🎃", drift: ["🎃", "🦇", "🍁"] },
    nov: { name: "Late Autumn", glyph: "🍂", drift: ["🍂", "🍁", "🌰"] },
    dec: { name: "Snowfall", glyph: "❄", drift: ["❄", "❅", "❆", "✻"] },
  };

  const AMBIENCE_KEY = "cc.ambience";
  const ambienceOn = () => localStorage.getItem(AMBIENCE_KEY) !== "off";

  function evRow(e) {
    const st = CAL_STATUS[e.status] || CAL_STATUS.confirmed;
    const cls = [
      "wk-ev",
      e.needs_you ? "needs-you" : "",
      e.conflict ? "clash" : "",
      e.ongoing ? "live" : "",
      e.all_day ? "allday" : "",
    ].filter(Boolean).join(" ");
    // The status word is spelled out, never carried by the dot alone.
    const tag = e.status && e.status !== "confirmed"
      ? `<span class="wk-tag">${esch(st.label)}</span>` : "";
    const when = e.end_label
      ? `${esch(e.time_label)}<span class="wk-dash">–</span>${esch(e.end_label)}`
      : esch(e.time_label);
    const flags = [
      e.ongoing ? `<span class="wk-flag live">now</span>` : "",
      // "Overlaps" on its own is baffling when the other commitment is in a
      // different column, so a cross-midnight clash says so.
      e.conflict ? `<span class="wk-flag clash" title="${e.conflict_offday
        ? `Overlaps a commitment on the ${e.conflict_neighbour} day`
        : "Overlaps another commitment this day"}">⚠ overlaps${
        e.conflict_offday ? ` ${esch(e.conflict_neighbour)} day` : ""}</span>` : "",
    ].filter(Boolean).join("");
    return `<li class="${cls}">
      <span class="wk-ev-dot" aria-hidden="true">${st.dot}</span>
      <span class="wk-ev-when mono">${when}</span>
      <span class="wk-ev-title">${esch(e.title || "Untitled")}</span>
      ${flags}${tag}
    </li>`;
  }

  function dayCard(day) {
    const season = SEASONS[day.season] || SEASONS.jan;
    const cls = [
      "wk-day",
      day.is_today ? "is-today" : "",
      day.is_weekend ? "is-weekend" : "",
      day.starts_month && !day.is_today ? "month-start" : "",
      day.conflicts ? "has-clash" : "",
    ].filter(Boolean).join(" ");

    const rel = day.relative_label
      ? `<span class="wk-rel">${esch(day.relative_label)}</span>` : "";
    const count = day.counts.total
      ? `<span class="wk-count" title="${day.counts.total} scheduled">${day.counts.total}</span>`
      : "";
    const body = day.counts.total
      ? `<ul class="wk-evs">${day.events.map(evRow).join("")}</ul>`
      : `<p class="wk-clear">Clear</p>`;

    // aria-label carries the full date so the column is announced as
    // "Wednesday, October 1st, 2026", not as a bare "1".
    const label = `${day.long_label}${day.counts.total
      ? `, ${day.counts.total} scheduled` : ", nothing scheduled"}${
      day.conflicts ? ", has overlapping commitments" : ""}`;
    return `<article class="${cls}" data-season="${esch(day.season)}"
        role="listitem" tabindex="0" aria-label="${esch(label)}">
      <header class="wk-hd">
        <div class="wk-hd-top">
          <span class="wk-dow">${esch(day.weekday)}</span>${count}
        </div>
        <time class="wk-date" datetime="${esch(day.iso)}">
          <span class="wk-num">${day.day}</span><sup>${esch(day.ordinal_suffix)}</sup>
          ${rel}
          <span class="wk-mo">${esch(day.month)}</span>
        </time>
        ${ambienceOn() ? `<span class="wk-motif" aria-hidden="true">${season.glyph}</span>` : ""}
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
    const season = SEASONS[first.season] || SEASONS.jan;

    const clashes = days.reduce((n, x) => n + (x.conflicts ? 1 : 0), 0);
    const notes = [
      clashes ? `<span class="wk-note clash">⚠ ${clashes} day${clashes > 1 ? "s" : ""} with overlaps</span>` : "",
      week.beyond ? `<span class="wk-note">+${week.beyond} later</span>` : "",
    ].filter(Boolean).join("");

    // Three ways the grid can be incomplete, said differently, because they
    // call for different actions: reconnect, wait, or nothing.
    const CAVEAT = {
      disconnected: {
        cls: "warn",
        text: `Google Calendar isn't connected, so only commitments stored
               here are shown. A clear day may not be a free day.`,
      },
      unknown: {
        cls: "muted",
        text: `Couldn't confirm the calendar connection, so anything that
               lives only in Google may be missing from this week.`,
      },
    };
    const caveat = CAVEAT[week.state]
      ? `<p class="wk-caveat ${CAVEAT[week.state].cls}">${
          week.state === "disconnected" ? "⚠" : "◔"} ${esch(
          CAVEAT[week.state].text.replace(/\s+/g, " ").trim())}</p>`
      : "";

    el.innerHTML = `
      <div class="wk-top">
        <h3>This week</h3>
        <span class="wk-range">${esch(range)}</span>
        <span class="wk-season" title="Seasonal theme">${
          ambienceOn() ? season.glyph + " " : ""}${esch(season.name)}</span>
        ${notes}
        <button class="wk-amb" id="wk-amb" type="button"
          aria-pressed="${ambienceOn()}" title="Seasonal decoration">
          ${ambienceOn() ? "✦ Decor on" : "✧ Decor off"}</button>
      </div>
      ${caveat}
      <div class="wk-grid" role="list" data-season="${esch(first.season)}">
        ${days.map(dayCard).join("")}
        <div class="wk-amb-layer" aria-hidden="true"></div>
      </div>
      <div class="hx-btns" style="margin-top:12px">${openBtn}</div>`;

    mountAmbience(el.querySelector(".wk-amb-layer"), first.season);
    wireWeek();
  }

  function mountAmbience(layer, seasonKey) {
    if (!layer) return;
    layer.innerHTML = "";
    if (!ambienceOn()) return;
    // Motion is opt-out via the toggle and automatically suppressed for
    // prefers-reduced-motion in CSS; the glyphs stay purely decorative and
    // never sit above interactive content.
    const drift = (SEASONS[seasonKey] || SEASONS.jan).drift;
    const n = 14;
    let html = "";
    for (let i = 0; i < n; i++) {
      const g = drift[i % drift.length];
      const left = Math.round((i / n) * 100 + (i % 3) * 4);
      const dur = 9 + ((i * 7) % 11);
      const delay = -((i * 13) % 17);
      const size = 11 + ((i * 5) % 10);
      html += `<span class="wk-flake" style="left:${left}%;
        animation-duration:${dur}s; animation-delay:${delay}s;
        font-size:${size}px">${g}</span>`;
    }
    layer.innerHTML = html;
  }

  function wireWeek() {
    const b = q("#cal-open");
    if (b) b.onclick = () => openAppKey("schedule");
    const amb = q("#wk-amb");
    if (amb) amb.onclick = () => {
      localStorage.setItem(AMBIENCE_KEY, ambienceOn() ? "off" : "on");
      if (HOME) renderWeek(HOME);
    };
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

  function renderDoNext(dn, d) {
    dn = dn || { title: "You're on track", reason: "Nothing urgent.", action: null };
    const el = q("#cc-donext");
    const calm = !dn.action;
    const btn = dn.action ? `<button class="big-btn" id="dn-go">${esch(actionLabel(dn.action))}</button>` : "";
    el.className = "cc-card hero";
    el.innerHTML = `
      <h3>Do this next</h3>
      <div class="donext ${calm ? "calm" : ""}">
        <div class="title">${esch(dn.title)}</div>
        <div class="reason">${esch(dn.reason || "")}</div>
        <div class="cta">${btn}</div>
      </div>`;
    if (dn.action) q("#dn-go").onclick = () => handleAction(dn.action);
  }

  function actionLabel(a) {
    if (!a) return "";
    if (a.type === "focus") return `Start ${a.minutes || 45}-min session`;
    if (a.type === "gym") return "Log workout";
    if (a.type === "water") return `+${a.oz || 16} oz water`;
    if (a.type === "gmail") return "Open Gmail";
    if (a.type === "big3") return "Mark done";
    if (a.type === "open") return "Open " + (a.app || "");
    return "Go";
  }

  // ---- Inbox (email signal) ----
  function renderInbox(inbox) {
    inbox = inbox || {};
    const items = inbox.items || [];
    const catTag = (c) => (c === "interview" || c === "deadline")
      ? `<span class="inbox-tag ${c}">${c}</span>` : "";
    const rows = items.map((e) =>
      `<div class="inbox-item ${e.important ? "important" : ""} ${e.stale ? "stale" : ""}" data-id="${esch(e.id)}">
        <span class="cat"></span>
        <div class="inbox-main">
          <div class="s">${esch(e.subject || "(no subject)")}</div>
          <div class="f"><span>${esch(e.from)}</span><span class="age">${esch(e.age || "")}</span></div>
        </div>${catTag(e.category)}</div>`).join("");
    const need = inbox.need_reply || 0;
    const header = `<h3>Inbox${need ? ` · ${need} need a reply` : ""}</h3>`;
    // Sync age, not message age — these are different facts. A 72h-old
    // message can sit in an inbox checked 4 minutes ago.
    const sync = inbox.sync || {};
    const agoStr = (iso) => {
      const t = new Date(iso); if (isNaN(t)) return null;
      const mins = Math.floor((Date.now() - t.getTime()) / 60000);
      if (mins < 1) return "just now";
      if (mins < 60) return mins + "m ago";
      const h = Math.floor(mins / 60);
      return h < 24 ? h + "h ago" : Math.floor(h / 24) + "d ago";
    };
    const checked = agoStr(sync.last_successful_sync);
    const syncLine = sync.sync_status === "failed"
      ? `<div class="inbox-sync failed">Last updated ${esch(checked || "never")} · refresh failed</div>`
      : checked
        ? `<div class="inbox-sync">Checked ${esch(checked)}</div>`
        : (sync.sync_status ? `<div class="inbox-sync">Not checked yet</div>` : "");
    const replies = (inbox.replies || []).length
      ? `<div class="att-empty" style="margin-top:8px">↩ ${inbox.replies.length} interview thread(s) countered your time.</div>` : "";
    q("#cc-inbox").innerHTML = header + syncLine +
      `<div class="inbox">${rows || `<div class="att-empty">${esch(inbox.empty || "Inbox looks clear. 🎉")}</div>`}</div>` +
      replies +
      `<div class="hx-btns" style="margin-top:10px">
         <button class="hx-btn" id="inbox-open">Open Gmail ↗</button>
         <button class="hx-btn" id="inbox-refresh" title="Check Gmail now">↻ Refresh</button>
       </div>`;
    const rf = q("#inbox-refresh");
    if (rf) rf.onclick = async () => {
      rf.disabled = true; rf.textContent = "Checking…";
      const r = await post("/gmail/refresh");
      if (r && r.refreshed === false && r.retry_after_seconds)
        toast(`Just checked — try again in ${r.retry_after_seconds}s`);
      else if (r && r.sync_status === "failed") toast("Gmail refresh failed — inbox shows last known good");
      else toast("Inbox updated");
      await refresh(true);   // re-pull the homepage so counts/cards agree
    };
    q("#cc-inbox").querySelectorAll(".inbox-item").forEach((el) => (el.onclick = () => openGmail()));
    q("#inbox-open").onclick = () => openGmail();
  }

  // ---- Today (Big 3 + next event) ----
  function renderToday(d) {
    const items = d.big3 || [];
    const ev = d.next_event;
    let b3;
    if (!items.length) {
      b3 = `<div class="big3-empty">
        <input class="cc-input" id="big3-input" placeholder="Your 3 wins for today…" />
        <button class="hx-btn" id="big3-save">Set</button></div>`;
    } else {
      b3 = items.map((b) =>
        `<div class="big3-item ${b.done ? "done" : ""}" data-id="${b.id}">
          <div class="big3-box">${b.done ? "✓" : ""}</div><div class="t">${esch(b.text)}</div></div>`).join("");
    }
    const evLine = ev ? `<div class="att-empty" style="margin-top:10px">🗓️ Next: <b style="color:var(--text)">${esch(ev.title)}</b>${ev.starts_at ? " · " + esch(String(ev.starts_at).slice(11, 16) || String(ev.starts_at).slice(0, 10)) : ""}</div>` : "";
    q("#cc-today").innerHTML = `<h3>Today's Big 3</h3>${b3}${evLine}`;
    if (!items.length) {
      q("#big3-save").onclick = async () => {
        const v = q("#big3-input").value.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 3);
        if (!v.length) return; await post("/core/big3", { items: v }); refresh(true);
      };
      const inp = q("#big3-input");
      if (inp) inp.onkeydown = (e) => { if (e.key === "Enter") q("#big3-save").click(); };
    } else {
      q("#cc-today").querySelectorAll(".big3-item").forEach((el) =>
        (el.onclick = async () => { await post("/core/big3/" + el.dataset.id + "/toggle", {}); refresh(true); }));
    }
  }

  // "Aug 28" — short enough to sit inline in a sentence.
  const dshort = (iso) => {
    if (!iso) return "";
    const p = String(iso).slice(0, 10).split("-");
    if (p.length !== 3) return String(iso);
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+p[1] - 1]
      + " " + (+p[2]);
  };

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

    // The arithmetic, spelled out — the card should never show a number the
    // user can't retrace. "Expected" allocations are labelled as such: they
    // are the configured amount, not something seen in the ledger.
    let payLine = "";
    if (pay.available && pay.window_complete === false) {
      // Every money figure is unknown here, so the arithmetic line would read
      // "— paycheck − — to savings = —". State the reason instead.
      payLine = `<p class="mny-pay">💵 ${esch(pay.text || "Only part of this window could be read, so this paycheck's figures can't be stated.")}</p>`;
    } else if (pay.available && !pay.overdue) {
      const allocs = (pay.allocations || []).map((a) => {
        const tag = a.source === "expected" ? " expected"
          : a.source === "withheld_before_deposit" ? " withheld pre-deposit" : "";
        return `${esch(a.name)} ${money(a.source === "withheld_before_deposit" ? a.planned : a.amount)}${tag}`;
      }).join(" · ");
      const perDay = pay.per_day != null
        ? ` · about ${money(pay.per_day, 2)}/day keeps you to payday` : "";
      // Money that came back out of savings is available but is NOT part of
      // what this paycheck left you, so it is stated separately, never added.
      const fromSav = pay.from_savings
        ? `<br><span class="sub">${money(pay.from_savings)} moved out of savings this cycle — available, but not counted above.</span>` : "";
      // An ambiguous allocation config would otherwise just look like a
      // smaller number.
      // A transfer whose description says "savings" but whose accounts don't.
      // Direction can't be read from a description, so it is named here rather
      // than guessed — silently ignoring it would push "left to spend" up.
      // A real transfer whose only matching rule is withheld-before-deposit:
      // deducted by nothing, so the configuration needs fixing.
      const withheldConflict = (pay.withheld_rule_conflicts || []).length
        ? `<br><span class="sub warn">⚠ ${money(pay.withheld_rule_conflicts[0].amount)} moved to savings after payday but your "${esch(pay.withheld_rule_conflicts[0].rule)}" rule is marked pre-deposit, so nothing was deducted for it. Untick pre-deposit in Settings.</span>` : "";
      const unmatched = (pay.unmatched_savings || []).length
        ? `<br><span class="sub warn">⚠ ${money(pay.unmatched_savings.reduce((s2, u) => s2 + (u.amount || 0), 0))} looks like savings by description but its accounts don't match your "${esch(pay.unmatched_savings[0].rule)}" rule — not counted either way. Match on the account name in Settings.</span>` : "";
      const overlap = (pay.allocation_overlaps || []).length
        ? `<br><span class="sub warn">⚠ ${esch(pay.allocation_overlaps[0])} — counted once, under the first rule. Fix the match terms in Settings.</span>` : "";
      payLine = `<p class="mny-pay">💵 Since ${esch(dshort(pay.cycle_start))}: ${money(pay.paycheck)} paycheck
        − ${money(pay.savings_total)} to savings = <b>${money(pay.spendable)}</b> to spend
        · ${money(pay.spent)} spent · <b class="${stateCls}">${money(pay.left)} left</b>${perDay}
        ${allocs ? `<br><span class="sub">${allocs}</span>` : ""}${fromSav}${overlap}${unmatched}${withheldConflict}
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
        const hedge = e.confidence === "low" ? " (seen twice — may not be a pattern)" : "";
        if (e.event === "changed")
          return `<b>${esch(e.name)}</b> ${money(e.from, 2)} → <b>${money(e.to, 2)}</b>${hedge}`;
        return `<b>${esch(e.name)}</b> ${money(e.amount, 2)}/${esch(per[e.cadence] || e.cadence || "")} — ${verb[e.event] || "changed"}${hedge}`;
      });
      const more = rec.event_count > bits.length
        ? ` · ${rec.event_count - bits.length} more` : "";
      recLine = `<p class="mny-sub mny-rec">🔁 ${bits.join(" · ")}${more}</p>`;
    }
    // Secondary context: a rolling window and remaining budget capacity.
    // Neither is a bank balance and neither is "left to spend".
    const subBits = [];
    if (m.last_30 != null) subBits.push(`Past 30 days <b>${money(m.last_30)}</b>${trend30}${through30}`);
    if (bud.fresh && bud.budget_room != null)
      subBits.push(`Budget room <b>${money(bud.budget_room)}</b> ${esch(bud.budget_room_scope || "across active budgets")}`);
    // Committed spending, stated as a monthly figure so it is comparable to
    // the other numbers on the card. Suppressed when the read was truncated:
    // a floor presented as a total is the failure docs/BUDGETS.md forbids.
    if (rec.available && rec.complete !== false && rec.monthly_equivalent)
      subBits.push(`Subscriptions <b>${money(rec.monthly_equivalent)}</b>/mo across ${rec.tracked}`);
    const subLine = subBits.length ? `<p class="mny-sub">${subBits.join(" · ")}</p>` : "";

    const bills = (m.upcoming_bills || []).slice(0, 2).map((b) =>
      `<div class="pos"><span>${esch(b.name)}${b.days_until != null ? ` · ${b.days_until}d` : ""}</span><span class="mono">${money(b.amount, 2)}</span></div>`).join("");
    // The pay-cycle line already says what the first observation would.
    const obs = (m.observations || []).filter((o) => !(pay.text && o === pay.text))
      .slice(0, 2).map((o) => `<li>${esch(o)}</li>`).join("");

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
    // cannot drift into showing the same data two different ways; it already
    // handles slice colours, an "Other" roll-up past 8 categories, and the
    // centre total. It replaced a bar version here because the pie is what
    // was asked for.
    const catPie = (typeof spendingDonut === "function")
      ? spendingDonut(m.categories, "Spending by category · last 30 days")
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
      ${payLine}${budLine}${recLine}${subLine}
      <div class="hx-btns" style="margin:10px 0 4px"><button class="hx-btn" id="money-budget">View budget →</button></div>
      ${obs ? `<ul class="mny-obs">${obs}</ul>` : ""}
      <div class="mny-hero mny-ff">${ffTiles}</div>
      ${catPie}
      ${accts ? `<h3 style="margin-top:12px">Accounts</h3>${accts}` : ""}
      ${bills ? `<h3 style="margin-top:12px">Upcoming bills</h3>${bills}` : ""}
      <div class="hx-btns" style="margin-top:10px"><button class="hx-btn" id="money-firefly">Recent transactions →</button></div>`;
    const ffBtn = q("#money-firefly");
    if (ffBtn) ffBtn.onclick = () => openAppKey("firefly");
    q("#money-budget").onclick = () => openAppKey("budget");
    const setup = q("#bud-setup");
    if (setup) setup.onclick = () => openSettings();
    const paySetup = q("#pay-setup");
    if (paySetup) paySetup.onclick = () => openSettings();
  }

  function renderPortfolio(p) {
    p = p || { state: "unreachable" };
    // Three states, not two (PRODUCT_IDEAS #13). A stocks service that is down
    // is NOT an empty portfolio, and telling you to "add your stocks" over a
    // transient blip sends you to fix configuration that is already correct.
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
      <h3>Portfolio · what changed</h3>
      ${live.length ? `<div class="mny-hero">
        <div class="mny-stat"><div class="v mono ${cls}">${arrow} ${p.day_change_pct}%</div><div class="l">${esch(p.session_label || "Last session")} · ${dc >= 0 ? "+" : ""}${money(dc)}${p.session_label ? "" : `<br><span style="font-size:10px">as-of date unavailable</span>`}</div></div>
        <div class="mny-stat"><div class="v mono" style="font-size:17px">${money(p.value)}</div><div class="l">Value</div></div>
        ${p.total_gain != null ? `<div class="mny-stat"><div class="v mono ${p.total_gain >= 0 ? "up" : "down"}" style="font-size:17px">${p.total_gain >= 0 ? "+" : ""}${money(p.total_gain)}</div><div class="l">Total gain</div></div>` : ""}
      </div>` : ""}
      ${moverRow(mv.up, "▲")}${moverRow(mv.down, "▼")}
      <div style="margin-top:6px">${positions}</div>${noneLive}${deadLine}`;
  }

  function renderHealth(d) {
    const h = d.health || {};
    const score = d.score || { score: 0, parts: {} };
    const st = h.study || {}, gym = h.gym || {}, w = h.water || {}, nut = h.nutrition || {};
    const studyPct = st.goal_min ? Math.min(100, Math.round((st.today_min / st.goal_min) * 100)) : 0;
    const waterPct = w.goal ? Math.min(100, Math.round((w.oz / w.goal) * 100)) : 0;
    const fmtH = (m) => `${Math.floor((m || 0) / 60)}h ${(m || 0) % 60}m`;
    const focusBtns = (st.presets || [25, 45, 60]).map((m) => `<button class="hx-btn" data-focus="${m}">${m}m</button>`).join("");
    const waterBtns = (w.presets || [8, 16, 24]).map((oz) => `<button class="hx-btn" data-water="${oz}">+${oz}</button>`).join("");
    const rating = nut.rating;
    const nutBtns = ["poor", "okay", "good"].map((r) => `<button class="hx-btn ${rating === r ? "on" : ""}" data-nut="${r}">${r[0].toUpperCase() + r.slice(1)}</button>`).join("");
    const exam = st.exam;
    const parts = score.parts || {};
    const scoreBars = Object.keys(parts).map((k) =>
      `<div class="score-bar"><span>${esch(k)}</span><div class="track"><div class="fill" style="width:${Math.round((parts[k].ratio || 0) * 100)}%"></div></div></div>`).join("");
    q("#cc-health").innerHTML = `
      <h3>Health &amp; discipline · today's score ${score.score || 0}</h3>
      <div class="hx-score">
        <div class="score-ring" style="--p:${score.score || 0}"><div class="hole">${score.score || 0}</div></div>
        <div class="score-bars" style="flex:1">${scoreBars || '<span class="att-empty">Set goals in ⚙ to start scoring.</span>'}</div>
      </div>
      <div class="hx">
        <div class="hx-row">
          <div class="hx-head"><span class="l">📚 Study${st.streak ? ` · ${st.streak}d streak` : ""}</span><span class="v">${fmtH(st.today_min)} / ${fmtH(st.goal_min)}</span></div>
          <div class="hx-track"><div class="hx-fill" style="width:${studyPct}%"></div></div>
          ${exam && exam.days_left != null ? `<div class="hx-head"><span class="l">${esch(exam.label)} in ${exam.days_left}d</span>${exam.remaining_hours != null ? `<span class="v">${exam.remaining_hours}h left · ${exam.weekly_needed_hours}h/wk</span>` : ""}</div>` : ""}
          <div class="hx-btns">${focusBtns}</div>
        </div>
        <div class="hx-row">
          <div class="hx-head"><span class="l">🏋️ Gym${gym.last ? ` · last ${timeAgoShort(gym.last)}` : ""}</span><span class="v">${gym.week || 0} / ${gym.goal || 0} this week</span></div>
          <div class="hx-track"><div class="hx-fill" style="width:${gym.goal ? Math.min(100, (gym.week / gym.goal) * 100) : 0}%"></div></div>
          <div class="hx-btns"><button class="hx-btn" data-gym="1">Log workout</button></div>
        </div>
        <div class="hx-row">
          <div class="hx-head"><span class="l">💧 Water</span><span class="v">${w.oz || 0} / ${w.goal || 0} oz</span></div>
          <div class="hx-track"><div class="hx-fill" style="width:${waterPct}%;background:var(--accent-2)"></div></div>
          <div class="hx-btns">${waterBtns}</div>
        </div>
        <div class="hx-row">
          <div class="hx-head"><span class="l">🍽️ Nutrition today</span></div>
          <div class="hx-btns">${nutBtns}</div>
        </div>
      </div>`;
    q("#cc-health").querySelectorAll("[data-focus]").forEach((b) => (b.onclick = () => startFocus(+b.dataset.focus, "Study")));
    q("#cc-health").querySelectorAll("[data-water]").forEach((b) => (b.onclick = async () => { await post("/core/water", { oz: +b.dataset.water }); toast(`+${b.dataset.water} oz`); refresh(true); }));
    q("#cc-health").querySelector("[data-gym]").onclick = async () => { await post("/core/gym", {}); toast("Workout logged 💪"); refresh(true); };
    q("#cc-health").querySelectorAll("[data-nut]").forEach((b) => (b.onclick = async () => { await post("/core/nutrition", { rating: b.dataset.nut }); refresh(true); }));
  }

  function renderCapture(items) {
    items = items || [];
    const list = items.map((c) =>
      `<div class="cap-item" data-id="${c.id}"><span>•</span><span>${esch(c.text)}</span><button class="x" title="done">✕</button></div>`).join("");
    q("#cc-capture").innerHTML = `
      <h3>Quick capture</h3>
      <div class="cap-form"><input class="cc-input" id="cap-input" placeholder="What's on your mind?" /><button class="hx-btn" id="cap-add">Add</button></div>
      <div class="cap-list">${list}</div>`;
    const add = async () => {
      const v = q("#cap-input").value.trim(); if (!v) return;
      await post("/core/capture", { text: v }); q("#cap-input").value = ""; refresh(true);
    };
    q("#cap-add").onclick = add;
    q("#cap-input").onkeydown = (e) => { if (e.key === "Enter") add(); };
    q("#cc-capture").querySelectorAll(".cap-item").forEach((el) =>
      (el.querySelector(".x").onclick = async () => { await fetch("/api/core/capture/" + el.dataset.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: true }) }); refresh(true); }));
  }

  function timeAgoShort(iso) {
    const d = new Date(iso); if (isNaN(d)) return iso;
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    return days <= 0 ? "today" : days === 1 ? "yesterday" : days + "d ago";
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
  // "Open Gmail" means GMAIL — the real thing, new tab. (The dashboard's own
  // triage view stays reachable from the Apps launcher tile.)
  function openGmail() {
    window.open("https://mail.google.com/", "_blank", "noopener");
  }
  async function handleAction(a) {
    if (!a) return;
    if (a.type === "focus") return startFocus(a.minutes || 45, a.label || "Study");
    if (a.type === "water") { await post("/core/water", { oz: a.oz || 16 }); toast(`+${a.oz || 16} oz`); return refresh(true); }
    if (a.type === "gym") { await post("/core/gym", {}); toast("Workout logged 💪"); return refresh(true); }
    if (a.type === "big3") { await post("/core/big3/" + a.id + "/toggle", {}); return refresh(true); }
    if (a.type === "gmail") return openGmail();
    if (a.type === "open") return openAppKey(a.app);
  }

  // ---- focus timer ----------------------------------------------------------
  let focus = { total: 0, left: 0, label: "Study", timer: null, paused: false };
  function startFocus(min, label) {
    focus = { total: min * 60, left: min * 60, label: label || "Study", timer: null, paused: false };
    q("#focus-label").textContent = focus.label;
    q("#focus-toggle").textContent = "Pause";
    q("#focus").hidden = false;
    paintFocus();
    focus.timer = setInterval(() => {
      if (focus.paused) return;
      focus.left--;
      paintFocus();
      if (focus.left <= 0) finishFocus(true);
    }, 1000);
  }
  function paintFocus() {
    const m = Math.floor(focus.left / 60), s = focus.left % 60;
    q("#focus-clock").textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  async function finishFocus(complete) {
    clearInterval(focus.timer);
    const done = Math.round((focus.total - Math.max(0, focus.left)) / 60);
    q("#focus").hidden = true;
    if (done >= 1) { await post("/core/focus", { minutes: done, label: focus.label }); toast(`Logged ${done} min of ${focus.label.toLowerCase()} 📚`); refresh(true); }
  }
  q("#focus-toggle").onclick = () => { focus.paused = !focus.paused; q("#focus-toggle").textContent = focus.paused ? "Resume" : "Pause"; };
  q("#focus-done").onclick = () => finishFocus(false);
  q("#focus-cancel").onclick = () => { clearInterval(focus.timer); q("#focus").hidden = true; };

  // ---- command palette ------------------------------------------------------
  let PAL = { items: [], sel: 0 };
  function commands() {
    const base = [
      { ic: "📚", label: "Start 45-min study session", hint: "study", run: () => startFocus(45, "Study") },
      { ic: "📚", label: "Start 25-min focus", hint: "study", run: () => startFocus(25, "Study") },
      { ic: "📚", label: "Start 60-min study session", hint: "study", run: () => startFocus(60, "Study") },
      { ic: "💧", label: "Add 16 oz water", hint: "water", run: async () => { await post("/core/water", { oz: 16 }); toast("+16 oz"); refresh(true); } },
      { ic: "💧", label: "Add 24 oz water", hint: "water", run: async () => { await post("/core/water", { oz: 24 }); toast("+24 oz"); refresh(true); } },
      { ic: "🏋️", label: "Log a workout", hint: "gym", run: async () => { await post("/core/gym", {}); toast("Workout logged 💪"); refresh(true); } },
      { ic: "🍽️", label: "Nutrition: good", hint: "food", run: async () => { await post("/core/nutrition", { rating: "good" }); refresh(true); } },
      { ic: "📝", label: "Add a task", hint: "task", run: () => quickCapturePrompt("Add task", (t) => post("/tasks/tasks", { title: t })) },
      { ic: "💭", label: "Quick capture a note", hint: "capture", run: () => q("#cap-input") && q("#cap-input").focus() },
      { ic: "⚙️", label: "Settings (goals, holdings, score)", hint: "settings", run: () => openSettings() },
      { ic: "📈", label: "Set stocks / holdings", hint: "stocks", run: () => openSettings() },
      { ic: "📬", label: "Open Gmail (web)", hint: "gmail mail email inbox", run: () => openGmail() },
      { ic: "▦", label: "Apps & services launcher", hint: "apps containers launcher", run: () => openLauncher() },
      { ic: "🔄", label: "Refresh dashboard", hint: "sync", run: () => refresh(true) },
      { ic: "🦴", label: "Legacy lounge view", hint: "lounge legacy old", run: () => (location.href = "/lounge.html") },
    ];
    // one command per app -> opens its modal, with natural aliases
    const ALIASES = {
      gmail: "mail email inbox", firefly: "money finance spending firefly ledger",
      vault: "passwords password vaultwarden bitwarden secrets", plex: "media movies tv shows jellyfin",
      stocks: "stocks portfolio investments shares", schedule: "calendar cal events",
      tasks: "todo task list", core: "score stats habits", networth: "net worth wealth",
      finance: "bills subscriptions", budget: "budget categories",
    };
    Object.values(APPSMAP).forEach((a) => base.push({
      ic: a.icon || "▦", label: "Open " + a.name,
      hint: (a.key + " " + (ALIASES[a.key] || "")).trim(), run: () => openAppKey(a.key),
    }));
    return base;
  }
  function openPalette() {
    PAL.items = commands(); PAL.sel = 0;
    q("#palette").hidden = false;
    const inp = q("#palette-input"); inp.value = ""; inp.focus();
    paintPalette("");
  }
  function closePalette() { q("#palette").hidden = true; }
  function paintPalette(filter) {
    const f = filter.toLowerCase();
    const matches = PAL.items.filter((c) => c.label.toLowerCase().includes(f) || (c.hint || "").includes(f));
    // if user typed a number after "study"/"water", offer a custom command
    const numMatch = f.match(/(study|focus|water)\s*(\d+)/);
    if (numMatch) {
      const n = +numMatch[2];
      if (numMatch[1] === "water") matches.unshift({ ic: "💧", label: `Add ${n} oz water`, hint: "", run: async () => { await post("/core/water", { oz: n }); toast(`+${n} oz`); refresh(true); } });
      else matches.unshift({ ic: "📚", label: `Start ${n}-min session`, hint: "", run: () => startFocus(n, "Study") });
    }
    PAL.filtered = matches; PAL.sel = 0;
    q("#palette-list").innerHTML = matches.map((c, i) =>
      `<li class="${i === 0 ? "sel" : ""}" data-i="${i}"><span class="ic">${c.ic}</span><span>${esch(c.label)}</span><span class="hint">${esch(c.hint || "")}</span></li>`).join("") || '<li class="att-empty">No match</li>';
    q("#palette-list").querySelectorAll("li[data-i]").forEach((li) =>
      (li.onclick = () => runPal(+li.dataset.i)));
  }
  function runPal(i) { const c = PAL.filtered[i]; if (c) { closePalette(); c.run(); } }
  function quickCapturePrompt(title, fn) {
    const v = prompt(title); if (v && v.trim()) { fn(v.trim()); toast("Added"); setTimeout(() => refresh(true), 300); }
  }

  // ---- wiring ---------------------------------------------------------------
  q("#cc-open-palette").onclick = openPalette;
  q("#cc-apps").onclick = openLauncher;
  q("#cc-score-pill").onclick = () => openAppKey("core");
  q("#palette").onclick = (e) => { if (e.target.id === "palette") closePalette(); };
  q("#palette-input").addEventListener("input", (e) => paintPalette(e.target.value));
  q("#palette-input").addEventListener("keydown", (e) => {
    const n = (PAL.filtered || []).length;
    if (e.key === "ArrowDown") { PAL.sel = Math.min(n - 1, PAL.sel + 1); highlight(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { PAL.sel = Math.max(0, PAL.sel - 1); highlight(); e.preventDefault(); }
    else if (e.key === "Enter") { runPal(PAL.sel); }
    else if (e.key === "Escape") { closePalette(); }
  });
  function highlight() {
    q("#palette-list").querySelectorAll("li").forEach((li, i) => li.classList.toggle("sel", i === PAL.sel));
  }
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); q("#palette").hidden ? openPalette() : closePalette(); }
    else if (e.key === "Escape" && !q("#palette").hidden) closePalette();
    else if (e.key === "Escape" && !q("#launcher").hidden) closeLauncher();
  });

  // ---- apps & services launcher ---------------------------------------------
  // The one place to reach every sub-app/container: tile -> dashboard modal,
  // ↗ -> the full underlying application (Firefly, Plex, importer…).
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
    EXT_LINKS = links;
    return links;
  }
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
  async function openSettings() {
    let s = {};
    try { s = await fetch("/api/core/settings").then((r) => r.json()); } catch {}
    const w = s.score_weights || {};
    const mk = (h) => h.map((c) => c.symbol + ":" + c.shares + (c.cost ? ":" + c.cost : "")).join("\n");
    const pc = s.paycheck || {};
    q("#settings-body").innerHTML = `
      <div class="set-group"><h4>Goals</h4><div class="set-grid">
        <div class="set-field"><label>Study/day (min)</label><input id="s-sd" type="number" value="${s.study_daily_min ?? 120}"></div>
        <div class="set-field"><label>Study/week (min)</label><input id="s-sw" type="number" value="${s.study_weekly_min ?? 600}"></div>
        <div class="set-field"><label>Workouts/week</label><input id="s-gw" type="number" value="${s.gym_weekly ?? 4}"></div>
        <div class="set-field"><label>Water goal (oz)</label><input id="s-wg" type="number" value="${s.water_goal_oz ?? 80}"></div>
      </div></div>
      <div class="set-group"><h4>Exam / deadline (optional)</h4><div class="set-grid">
        <div class="set-field"><label>Label</label><input id="s-el" value="${esch(s.exam_label || "")}"></div>
        <div class="set-field"><label>Date (YYYY-MM-DD)</label><input id="s-ed" value="${esch(s.exam_date || "")}"></div>
        <div class="set-field"><label>Target study hours</label><input id="s-eh" type="number" value="${s.exam_target_hours ?? ""}"></div>
      </div></div>
      <div class="set-group"><h4>Investments</h4><div class="set-grid">
        <div class="set-field" style="grid-column:1/-1"><label>Holdings — one per line (or comma-separated): SYMBOL shares cost — cost optional, fractional shares OK</label>
          <textarea id="s-hold" placeholder="NVDA 10 150&#10;AAPL 2.5&#10;VOO:1.25:380">${esch(mk(((s.market || {}).holdings) || []))}</textarea></div>
        <div class="set-field" style="grid-column:1/-1"><label>Watchlist (comma-separated symbols)</label>
          <input id="s-watch" value="${esch((((s.market || {}).watchlist) || []).join(", "))}"></div>
        <div class="set-field"><label>Alert on move ≥ (%)</label><input id="s-mv" type="number" value="${(s.market || {}).move_threshold_pct ?? 3}"></div>
      </div></div>
      <div class="set-group"><h4>Monthly budgets</h4>
        <p class="set-hint">Each budget maps to one or more Firefly category names. Spending in those
        categories fills the budget's vessel on the Budget page.</p>
        <div id="s-budgets"></div>
        <button class="hx-btn" id="s-budget-add" type="button">+ Add budget</button>
        <p class="set-hint" id="s-cat-hint"></p>
      </div>
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
      <div class="set-group"><h4>Important senders (comma-separated emails/domains)</h4><div class="set-field">
        <input id="s-imp" value="${esch((s.important_senders || []).join(", "))}"></div></div>
      <div class="set-group"><h4>Daily-score weights (0 disables a component)</h4><div class="set-grid">
        <div class="set-field"><label>Study</label><input id="w-study" type="number" value="${w.study ?? 30}"></div>
        <div class="set-field"><label>Fitness</label><input id="w-fitness" type="number" value="${w.fitness ?? 20}"></div>
        <div class="set-field"><label>Tasks/Big3</label><input id="w-tasks" type="number" value="${w.tasks ?? 20}"></div>
        <div class="set-field"><label>Hydration</label><input id="w-hydration" type="number" value="${w.hydration ?? 10}"></div>
        <div class="set-field"><label>Nutrition</label><input id="w-nutrition" type="number" value="${w.nutrition ?? 10}"></div>
        <div class="set-field"><label>Sleep</label><input id="w-sleep" type="number" value="${w.sleep ?? 0}"></div>
      </div></div>`;
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
      study_daily_min: num("#s-sd", 120), study_weekly_min: num("#s-sw", 600),
      gym_weekly: num("#s-gw", 4), water_goal_oz: num("#s-wg", 80),
      exam_label: q("#s-el").value.trim() || "exam",
      exam_date: q("#s-ed").value.trim() || null,
      exam_target_hours: q("#s-eh").value.trim() ? num("#s-eh", null) : null,
      important_senders: list("#s-imp"),
      budgets: budgets,
      market: { holdings: parsed.holdings, watchlist: list("#s-watch").map((s) => s.toUpperCase()), move_threshold_pct: num("#s-mv", 3) },
      score_weights: { study: num("#w-study", 30), fitness: num("#w-fitness", 20), tasks: num("#w-tasks", 20), hydration: num("#w-hydration", 10), nutrition: num("#w-nutrition", 10), sleep: num("#w-sleep", 0) },
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
    // background refresh every 60s (skip while a modal/palette/focus is open)
    setInterval(() => {
      if (q("#overlay").classList.contains("open")) return;
      if (!q("#palette").hidden || !q("#focus").hidden) return;
      refresh(false);
      loadSystems();
    }, 60000);
  })();
})();
