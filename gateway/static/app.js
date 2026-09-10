// Sub-app detail modals for the command-center homepage (home.js opens them
// via openApp / openAppKey). The pixel-art "agent lounge" that used to live
// below was retired in SCRUM-140.
const $ = (sel) => document.querySelector(sel);


function timeAgo(iso) {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

// ---- app detail modal -------------------------------------------------------

const esc = (s) =>
  String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const api = (path, opts) => fetch(`/api${path}`, opts).then((r) => r.json());
const fmtDate = (s) => (s ? String(s).slice(0, 16).replace("T", " ") : "—");

// ---- calendar date helpers (week/month views for the schedule app) --------
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];
const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const startOfWeek = (d) => { const x = startOfDay(d); x.setDate(x.getDate() - x.getDay()); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const addMonths = (d, n) => { const x = new Date(d); x.setMonth(x.getMonth() + n); return x; };
const isSameDay = (a, b) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
// Persists across re-renders of the schedule modal (view mode + which week/month is showing).
const SCHEDULE_VIEW = { mode: "week", anchor: new Date() };

function closeModal() {
  $("#overlay").classList.remove("open");
}
$("#modal-close").onclick = closeModal;
$("#overlay").onclick = (e) => {
  if (e.target.id === "overlay") closeModal();
};
document.addEventListener("keydown", (e) => e.key === "Escape" && closeModal());

async function openApp(app) {
  $("#modal-icon").textContent = app.icon;
  $("#modal-title").textContent = app.name;
  $("#modal-mode").textContent = "";
  $(".modal").classList.toggle("wide", app.key === "schedule");
  const body = $("#modal-body");
  body.innerHTML = `<p class="empty">Loading…</p>`;
  $("#overlay").classList.add("open");
  try {
    await (RENDERERS[app.key] || renderGeneric)(app, body);
  } catch (e) {
    body.innerHTML = `<p class="empty">Couldn't reach ${esc(app.name)}. Is the stack running?</p>`;
  }
}

function setMode(mode) {
  const notConnected = mode === "mock" || mode === "disconnected" || mode === "off";
  const label = notConnected ? "not connected"
    : mode === "error" ? "connection error"
    : mode ? `${mode} data` : "";
  $("#modal-mode").textContent = label;
}

// The donut's geometry, markup and slice labels live in donut.js so they can
// be unit-tested by `node --test` rather than only eyeballed. This file keeps
// the part that genuinely needs a browser: pointing at a wedge.
const DONUT_COLORS = (typeof Donut !== "undefined" && Donut.COLORS) || [];

function spendingDonut(cats, title) {
  return typeof Donut !== "undefined" ? Donut.render(cats, title) : "";
}

/* Hover, focus and touch all mean the same thing here: "tell me about this
 * wedge". One delegated listener on the document handles every donut on the
 * page, whenever it was rendered — the alternative is re-binding after each
 * innerHTML swap, and a donut that silently stops responding after a refresh
 * is worse than one that never responded at all.
 *
 * The centre of the donut is the readout. It holds the 30-day total at rest
 * and the pointed-at slice while pointing, then puts the total back — so the
 * chart never leaves a partial figure sitting where a total was, which is the
 * same rule docs/BUDGETS.md applies to every other number on the page. */
(function wireDonutHover() {
  if (typeof document === "undefined") return;

  function donutOf(el) {
    const wrap = el.closest && el.closest(".dn-wrap");
    return wrap || null;
  }

  function show(wrap, i) {
    const svg = wrap.querySelector(".dn-svg");
    const total = wrap.querySelector(".dn-total");
    const cap = wrap.querySelector(".dn-cap");
    if (!svg || !total || !cap) return;
    if (total.dataset.rest === undefined) {
      total.dataset.rest = total.textContent;
      cap.dataset.rest = cap.textContent;
    }
    const slice = svg.querySelector('.dn-slice[data-i="' + i + '"]');
    if (!slice) return;
    // Read the two figures straight off the wedge. donut.js composed them
    // (centreFor) next to the label they belong with, already trimmed to fit
    // the hole — this used to re-split data-label on " · " and " $", which
    // made the middle of the chart depend on a separator picked for display.
    total.textContent = slice.getAttribute("data-v") || "";
    cap.textContent = slice.getAttribute("data-cap") || "";
    wrap.classList.add("dn-active");
    svg.querySelectorAll(".dn-slice").forEach((p) =>
      p.classList.toggle("is-on", p.getAttribute("data-i") === String(i)));
    wrap.querySelectorAll(".dn-row").forEach((r) =>
      r.classList.toggle("is-on", r.getAttribute("data-i") === String(i)));
  }

  function clear(wrap) {
    const total = wrap.querySelector(".dn-total");
    const cap = wrap.querySelector(".dn-cap");
    if (total && total.dataset.rest !== undefined) total.textContent = total.dataset.rest;
    if (cap && cap.dataset.rest !== undefined) cap.textContent = cap.dataset.rest;
    wrap.classList.remove("dn-active");
    wrap.querySelectorAll(".is-on").forEach((e) => e.classList.remove("is-on"));
  }

  function onEnter(ev) {
    const el = ev.target.closest && ev.target.closest("[data-i]");
    const wrap = el && donutOf(el);
    if (!wrap) return;
    show(wrap, el.getAttribute("data-i"));
  }

  function onLeave(ev) {
    const wrap = donutOf(ev.target);
    if (!wrap) return;
    // Only clear when the pointer has actually left the whole donut, not when
    // it crosses between two adjacent wedges.
    if (ev.relatedTarget && wrap.contains(ev.relatedTarget)) return;
    clear(wrap);
  }

  document.addEventListener("mouseover", onEnter, true);
  document.addEventListener("mouseout", onLeave, true);
  document.addEventListener("focusin", onEnter, true);
  document.addEventListener("focusout", onLeave, true);
})();

const RENDERERS = {
  async powerbuy(app, body) {
    const [sum, list] = await Promise.all([api("/powerbuy/summary"), api("/powerbuy/purchases")]);
    setMode(sum.mode);
    const s = sum.summary || {};
    const rows = (list.purchases || [])
      .map((p) => {
        const paid = String(p.paymentStatus || "").toLowerCase() === "paid";
        return `<div class="row"><div class="grow"><b>${esc(p.item)}</b>
          <div class="sub">${esc(p.deliveryStatus || "—")} · ${paid ? "paid" : "unpaid"}</div></div>
          <span class="right">$${esc(p.profit7Percent ?? 0)} profit</span></div>`;
      })
      .join("");
    body.innerHTML = `
      <div class="tiles">
        <div class="tile good"><div class="n">$${esc(s.expected_profit ?? 0)}</div><div class="l">Expected profit</div></div>
        <div class="tile warn"><div class="n">${esc(s.unpaid_count ?? 0)}</div><div class="l">Unpaid</div></div>
        <div class="tile"><div class="n">${esc(s.not_delivered_count ?? 0)}</div><div class="l">Not delivered</div></div>
        <div class="tile warn"><div class="n">${esc(s.expiring_soon_count ?? 0)}</div><div class="l">Expiring soon</div></div>
      </div>
      <h4>Purchases</h4>
      <div class="rows">${rows || '<p class="empty">No purchases.</p>'}</div>`;
  },

  async gmail(app, body) {
    const data = await api("/gmail/needs-reply");
    setMode(data.mode);
    const rows = (data.emails || [])
      .map(
        (e) => `<div class="row"><div class="grow"><b>${esc(e.subject)}</b>
          <div class="sub">${esc(e.from)}</div></div>
          <span class="chip ${esc(e.category)}">${esc(e.category || "inbox")}</span></div>`
      )
      .join("");
    const empty = data.mode === "disconnected"
      ? '<p class="empty">📬 <b>Not connected.</b> Add your Google credentials (docs/SETUP.md) to triage your inbox.</p>'
      : data.mode === "error"
      ? '<p class="empty">⚠️ Couldn\'t reach Gmail — the token likely needs re-authorizing. (Not showing anything rather than fake emails.)</p>'
      : '<p class="empty">Inbox is clear. 🎉</p>';
    body.innerHTML = `
      <h4>Needs a reply</h4>
      <div class="rows">${rows || empty}</div>`;
  },

  async schedule(app, body) {
    const data = await api("/schedule/events");
    setMode();
    const events = data.events || [];

    const eventsOnDay = (day) =>
      events
        .filter((ev) => ev.starts_at && isSameDay(new Date(ev.starts_at), day))
        .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));

    const eventChip = (ev, compact) => {
      const status = ev.status || "confirmed";
      const time = new Date(ev.starts_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      return `<div class="${compact ? "cal-month-event" : "cal-event"} status-${esc(status)}" title="${esc(ev.title)}">
        <span class="t">${esc(ev.title)}</span>${compact ? "" : `<span class="tm">${time}</span>`}
      </div>`;
    };

    const renderWeek = () => {
      const start = startOfWeek(SCHEDULE_VIEW.anchor);
      const end = addDays(start, 6);
      const today = startOfDay(new Date());
      const label = end.getMonth() === start.getMonth()
        ? `${MONTHS_SHORT[start.getMonth()]} ${start.getDate()}–${end.getDate()}, ${end.getFullYear()}`
        : `${MONTHS_SHORT[start.getMonth()]} ${start.getDate()} – ${MONTHS_SHORT[end.getMonth()]} ${end.getDate()}, ${end.getFullYear()}`;
      const cols = Array.from({ length: 7 }, (_, i) => {
        const day = addDays(start, i);
        const past = day < today;
        const dayEvents = eventsOnDay(day);
        return `<div class="cal-day-col${isSameDay(day, today) ? " today" : ""}">
          <div class="cal-day-head"><div class="dow">${DOW[i]}</div><div class="dnum">${day.getDate()}</div></div>
          ${dayEvents.length ? dayEvents.map((e) => eventChip(e, false)).join("") : '<div class="cal-day-empty">—</div>'}
          ${past ? '<div class="cal-past-x">✕</div>' : ""}
        </div>`;
      }).join("");
      return { label, html: `<div class="cal-week-grid">${cols}</div>` };
    };

    const renderMonth = () => {
      const anchor = SCHEDULE_VIEW.anchor;
      const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
      const gridStart = startOfWeek(monthStart);
      const today = startOfDay(new Date());
      const label = `${MONTHS_LONG[anchor.getMonth()]} ${anchor.getFullYear()}`;
      const dowRow = DOW.map((d) => `<div class="cal-month-dow">${d}</div>`).join("");
      const cells = Array.from({ length: 42 }, (_, i) => {
        const day = addDays(gridStart, i);
        const inMonth = day.getMonth() === anchor.getMonth();
        const past = day < today;
        const dayEvents = eventsOnDay(day);
        const shown = dayEvents.slice(0, 3);
        const extra = dayEvents.length - shown.length;
        return `<div class="cal-month-cell${inMonth ? "" : " other-month"}${isSameDay(day, today) ? " today" : ""}">
          <div class="dnum">${day.getDate()}</div>
          ${shown.map((e) => eventChip(e, true)).join("")}
          ${extra > 0 ? `<div class="cal-month-more">+${extra} more</div>` : ""}
          ${past ? '<div class="cal-past-x">✕</div>' : ""}
        </div>`;
      }).join("");
      return { label, html: `<div class="cal-month-grid">${dowRow}${cells}</div>` };
    };

    const draw = () => {
      const { label, html } = SCHEDULE_VIEW.mode === "week" ? renderWeek() : renderMonth();
      body.innerHTML = `
        <div class="cal-toolbar">
          <div class="cal-nav">
            <button id="cal-prev">‹</button>
            <button id="cal-today">Today</button>
            <button id="cal-next">›</button>
          </div>
          <div class="cal-label">${label}</div>
          <div class="cal-view-toggle">
            <button data-view="week" class="${SCHEDULE_VIEW.mode === "week" ? "active" : ""}">Week</button>
            <button data-view="month" class="${SCHEDULE_VIEW.mode === "month" ? "active" : ""}">Month</button>
          </div>
        </div>
        ${html}
        <h4>Add an event</h4>
        <div class="inline-form">
          <input id="ev-title" placeholder="Title (e.g. Dentist)" />
          <input id="ev-when" type="datetime-local" />
          <button class="btn" id="ev-add">Add</button>
        </div>`;

      $("#cal-prev").onclick = () => {
        SCHEDULE_VIEW.anchor = SCHEDULE_VIEW.mode === "week"
          ? addDays(SCHEDULE_VIEW.anchor, -7) : addMonths(SCHEDULE_VIEW.anchor, -1);
        draw();
      };
      $("#cal-next").onclick = () => {
        SCHEDULE_VIEW.anchor = SCHEDULE_VIEW.mode === "week"
          ? addDays(SCHEDULE_VIEW.anchor, 7) : addMonths(SCHEDULE_VIEW.anchor, 1);
        draw();
      };
      $("#cal-today").onclick = () => { SCHEDULE_VIEW.anchor = new Date(); draw(); };
      body.querySelectorAll("[data-view]").forEach((btn) => {
        btn.onclick = () => { SCHEDULE_VIEW.mode = btn.dataset.view; draw(); };
      });
      $("#ev-add").onclick = async () => {
        const title = $("#ev-title").value.trim();
        const when = $("#ev-when").value;
        if (!title || !when) return;
        await api("/schedule/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, starts_at: when, source: "manual", status: "confirmed" }),
        });
        openApp(app);
      };
    };
    draw();
  },

  async fitness(app, body) {
    const [plan, nutrition, visits] = await Promise.all([
      api("/fitness/plan"),
      api("/fitness/nutrition"),
      api("/fitness/visits"),
    ]);
    setMode();
    const tp = plan.today_plan || {};
    const lifts = (tp.lifts || []).join(", ") || "recovery";
    const groceries = (nutrition.grocery_list || [])
      .map((g) => `<span class="pill">${esc(g.item)} · ${esc(g.qty)}</span>`)
      .join("");
    body.innerHTML = `
      <div class="tiles">
        <div class="tile"><div class="n">${esc(plan.today || "")}</div><div class="l">${esc(tp.focus || "Rest")} day</div></div>
        <div class="tile good"><div class="n">${esc(visits.count ?? 0)}</div><div class="l">Gym visits logged</div></div>
        <div class="tile"><div class="n">${esc(nutrition.target_protein_g ?? 0)}g</div><div class="l">Protein target</div></div>
      </div>
      <h4>Today: ${esc(tp.focus || "Rest")}</h4>
      <p>${esc(lifts)}</p>
      <button class="btn" id="log-visit">💪 Log a gym visit</button>
      <h4>Groceries to buy</h4>
      <div class="pill-list">${groceries || '<p class="empty">Nothing listed.</p>'}</div>`;
    $("#log-visit").onclick = async () => {
      await api("/fitness/visits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "" }),
      });
      openApp(app);
    };
  },

  async finance(app, body) {
    const [sum, list] = await Promise.all([api("/finance/summary"), api("/finance/bills")]);
    setMode();
    const nd = sum.next_due;
    const rows = (list.bills || [])
      .map(
        (b) => `<div class="row"><div class="grow"><b>${esc(b.name)}</b>
          <div class="sub">${esc(b.category)}</div></div>
          <span class="right">$${esc(b.amount)} · day ${esc(b.due_day)}</span></div>`
      )
      .join("");
    body.innerHTML = `
      <div class="tiles">
        <div class="tile"><div class="n">$${esc(sum.monthly_total ?? 0)}</div><div class="l">Per month</div></div>
        <div class="tile"><div class="n">${esc(sum.bill_count ?? 0)}</div><div class="l">Bills tracked</div></div>
        <div class="tile warn"><div class="n">${nd ? esc(nd.name) : "—"}</div><div class="l">Next due${nd ? ` · in ${esc(nd.days_until)}d` : ""}</div></div>
      </div>
      <h4>Bills & subscriptions</h4>
      <div class="rows">${rows || '<p class="empty">No bills yet.</p>'}</div>
      <h4>Add a bill</h4>
      <div class="inline-form">
        <input id="bill-name" placeholder="Name (e.g. Internet)" />
        <input id="bill-amt" type="number" placeholder="$/mo" style="max-width:90px" />
        <input id="bill-day" type="number" placeholder="Due day" style="max-width:90px" />
        <button class="btn" id="bill-add">Add</button>
      </div>`;
    $("#bill-add").onclick = async () => {
      const name = $("#bill-name").value.trim();
      const amount = parseFloat($("#bill-amt").value) || 0;
      const due_day = parseInt($("#bill-day").value) || 1;
      if (!name) return;
      await api("/finance/bills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, amount, due_day, category: "other" }),
      });
      openApp(app);
    };
  },

  async budget(app, body) {
    // The full recurring inventory is its own read: /status carries only the
    // headline (a few events), because the 13-month history behind the list
    // is too expensive to put on the homepage's hot path.
    const [d, rec] = await Promise.all([
      api("/budget/status"),
      api("/budget/recurring").catch(() => ({ available: false,
        reason: "the budget service didn't answer" })),
    ]);
    setMode(d.available === false ? "disconnected" : (d.freshness && !d.freshness.current_ok ? "paused" : ""));
    $(".modal").classList.add("wide");

    if (d.available === false) {
      body.innerHTML = `<p class="empty">🫙 Budget status unavailable — ${esc(d.reason || "Firefly unreachable")}.</p>`;
      return;
    }
    const fresh = d.freshness && d.freshness.current_ok;
    const mo = d.month || {};
    const fmt = (n, dec = 0) => n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });

    // ---- freshness: paused banner (stale ingestion) or active sync line ----
    const fr = d.freshness || {};
    const importBtn = d.importer_url
      ? ` <a class="btn bud-import" href="${esc(d.importer_url)}" target="_blank" rel="noopener">Import transactions ↗</a>`
      : "";
    const paused = !fresh
      ? `<div class="bud-paused"><p style="margin:0"><b>⏸ Financial data isn't current</b> — ${esc(fr.paused_reason || "ledger freshness unknown")}${d.ingest_latest ? ` (last import evidence: ${esc(d.ingest_latest)})` : ""}.</p>
         <p style="margin:6px 0 0">Budget guidance is paused so it can't mislead you — totals below are as of the ledger. To make it current, import your latest bank transactions.${importBtn}</p></div>`
      : "";
    const noMonthData = fr.month_ingested === false
      ? `<p class="bud-paused">📅 <b>No ${esc((mo.label || "this month").split(" ")[0])} transactions imported yet</b> — this month's spending is unknown, not $0. The figures below cover only what's in the ledger.</p>`
      : "";
    const agoTxt = (n) => n === 0 ? "today" : n === 1 ? "yesterday" : `${n} days ago`;
    const syncLine = fresh && fr.ingest_days != null
      ? `<p class="bud-sync">Data synced ${esc(agoTxt(fr.ingest_days))}${fr.activity_days != null ? ` · last transaction ${esc(agoTxt(fr.activity_days))}` : ""}</p>`
      : "";

    // ---- the pay cycle: what's left of this paycheck --------------------
    // Shown as the arithmetic, not just the answer: paycheck, each savings
    // deduction, what's been spent since payday, what's left. A number the
    // user can't retrace is a number they can't trust.
    const pay = d.paycheck || {};
    const pc = pay.cycle || {};
    const dshort = (iso) => {
      if (!iso) return "";
      const parts = String(iso).slice(0, 10).split("-");
      if (parts.length !== 3) return String(iso);
      return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+parts[1] - 1] + " " + (+parts[2]);
    };
    let paySection = "";
    if (pay.available && !pc.overdue) {
      const allocRows = (pc.allocations || []).map((a) => {
        const note = a.source === "observed" ? `seen ${esc(dshort(a.date))}`
          : a.source === "withheld_before_deposit" ? "withheld before the deposit — not subtracted"
          : "expected — no transfer in the ledger yet";
        const amt = a.source === "withheld_before_deposit" ? a.planned : a.amount;
        return `<div class="btxn"><span class="grow">${esc(a.name)}</span>
          <span class="sub">${note}</span>
          <span class="mono ${a.source === "withheld_before_deposit" ? "" : "down"}">${a.source === "withheld_before_deposit" ? "" : "−"}${fmt(amt, 2)}</span></div>`;
      }).join("");
      const perDay = pc.per_day != null
        ? `<div class="bud-sync">About ${fmt(pc.per_day, 2)}/day keeps you going to ${esc(dshort(pc.next_payday))}.</div>`
        : `<div class="bud-sync">Per-day guidance is paused — ${esc(pay.stale_reason || "the ledger isn't current")}.</div>`;
      paySection = `
        <h4>This paycheck · since ${esc(dshort(pc.start))}</h4>
        <div>
          <div class="btxn"><span class="grow"><b>Paycheck</b></span><span class="sub">${esc(pc.paycheck_desc || "")}${pc.paycheck_parts > 1 ? ` (${pc.paycheck_parts} deposits)` : ""}</span><span class="mono up">${fmt(pc.paycheck, 2)}</span></div>
          ${allocRows}
          <div class="btxn"><span class="grow"><b>Yours to spend</b></span><span class="sub">after savings</span><span class="mono">${fmt(pc.spendable, 2)}</span></div>
          <div class="btxn"><span class="grow">Spent since payday</span><span class="sub">${pc.days_elapsed} day(s)${pay.as_of ? ` · ledger through ${esc(pay.as_of)}` : ""}</span><span class="mono down">−${fmt(pc.spent, 2)}</span></div>
          <div class="btxn"><span class="grow"><b>Left to spend</b></span><span class="sub">${pc.days_to_next > 0 ? `${pc.days_to_next} day(s) to ${esc(dshort(pc.next_payday))}` : `payday ${esc(dshort(pc.next_payday))}`}</span><span class="mono ${pc.left < 0 ? "down" : "up"}"><b>${fmt(pc.left, 2)}</b></span></div>
        </div>
        ${perDay}`;
    } else if (pay.available && pc.overdue) {
      paySection = `<h4>This paycheck</h4><p class="bud-paused">${esc(pc.text)}</p>`;
    } else if (pay.configured === false) {
      paySection = `<h4>This paycheck</h4><p class="empty">Not set up yet — add your paycheck and the savings that come out of it in ⚙ Settings to see what's left to spend.</p>`;
    } else if (pay.reason) {
      paySection = `<h4>This paycheck</h4><p class="empty">Left to spend unavailable — ${esc(pay.reason)}.</p>`;
    }

    // ---- setup empty state ----
    if (!d.configured) {
      body.innerHTML = `${paused}${paySection}
        <p class="empty" style="margin:8px 0 14px">🫙 No budgets configured yet. Give each spending area a monthly limit and map it to your Firefly categories — then this page shows how full each "glass" is, your safe pace, and calm warnings before the money runs out.</p>
        <button class="btn" id="bud-setup2">Set up budgets</button>`;
      const b = document.getElementById("bud-setup2");
      if (b) b.onclick = () => { closeModal(); window.ccOpenSettings && window.ccOpenSettings(); };
      return;
    }

    // ---- header stats ----
    const bills = d.bills || {};
    const billsRemaining = bills.supported
      ? bills.items.filter((b) => !b.paid_this_month && b.amount != null).reduce((s, b) => s + b.amount, 0)
      : null;
    const stats = `
      <div class="bstats">
        <div class="bstat"><div class="l">${esc(mo.label || "This month")}</div><div class="v">${mo.days_left ?? "—"}<span class="u"> days left</span></div></div>
        <div class="bstat"><div class="l">Income</div><div class="v">${d.income_month ? fmt(d.income_month) : "—"}</div></div>
        <div class="bstat"><div class="l">Spent${(pay.month || {}).savings ? ' <span class="u">· excl. savings</span>' : ""}</div><div class="v">${fmt((pay.month || {}).spent != null ? pay.month.spent : (d.totals || {}).spent_month)}</div></div>
        <div class="bstat"><div class="l">Bills remaining</div><div class="v">${billsRemaining != null ? fmt(billsRemaining) : "—"}</div></div>
        <div class="bstat safe"><div class="l">Budget room<span class="u"> · ${esc(d.budget_room_scope || "")}</span></div><div class="v">${fmt(d.budget_room)}</div></div>
      </div>`;

    // ---- warnings ----
    const warns = (d.warnings || []).map((w) =>
      `<div class="bwarn ${esc(w.state)}"><span class="dot"></span>${esc(w.text)}</div>`).join("");

    // ---- vessels ----
    const vessel = (b) => {
      const pct = Math.max(0, Math.min(100, b.pct || 0));
      const over = b.state === "over";
      const fillH = over ? 100 : pct;
      const stateTxt = { healthy: "on track", watch: "pacing high", approaching: "running low", over: "over budget", paused: "paused" }[b.state] || b.state;
      const pace = (b.state !== "paused" && b.safe_per_day != null)
        ? `<div class="vpace">${fmt(b.safe_per_day, 2)}/day safe · ${mo.days_left} days</div>` : "";
      const proj = (b.state === "watch" || b.state === "over") && b.projected != null
        ? `<div class="vproj">→ ${fmt(b.projected)} projected</div>` : "";
      return `
      <div class="vcard st-${esc(b.state)}" data-bid="${esc(b.id)}" title="Click for transactions">
        <svg class="vessel" viewBox="0 0 84 108" width="84" height="108">
          <defs><clipPath id="clip-${esc(b.id)}"><rect x="6" y="4" width="72" height="100" rx="14"/></clipPath></defs>
          <rect x="6" y="4" width="72" height="100" rx="14" class="vglass"/>
          <g clip-path="url(#clip-${esc(b.id)})">
            <rect class="vfill" x="6" width="72" y="104" height="0" data-h="${fillH}"/>
            ${over ? '<rect x="6" y="4" width="72" height="3" class="vover-rim"/>' : ""}
          </g>
          <text x="42" y="58" text-anchor="middle" class="vpct">${b.state === "paused" ? "⏸" : pct + "%"}</text>
        </svg>
        <div class="vname">${esc(b.name)}</div>
        <div class="vnum">${fmt(b.spent)} / ${fmt(b.limit)}</div>
        <div class="vrem">${b.remaining >= 0 ? fmt(b.remaining) + " left" : fmt(-b.remaining) + " over"}</div>
        ${pace}${proj}
        <div class="vstate">${esc(stateTxt)}</div>
        <div class="bdetail" hidden>
          <div class="sub" style="margin:6px 0 4px">${esc((b.categories || []).join(", ") || "no categories mapped")}</div>
          ${(b.txns || []).map((t) => `<div class="btxn"><span>${esc(t.date.slice(5))}</span><span class="grow">${esc(t.desc)}</span><span class="mono ${t.amount < 0 ? "down" : "up"}">${t.amount < 0 ? "-" : "+"}$${Math.abs(t.amount).toFixed(2)}</span></div>`).join("") || '<p class="empty">No transactions yet this month.</p>'}
        </div>
      </div>`;
    };
    const vessels = (d.budgets || []).map(vessel).join("");

    // ---- uncategorized + unbudgeted ----
    const un = d.uncategorized || {};
    const uncat = un.amount > 0 ? `
      <div class="bwarn uncat"><span class="dot"></span>
        <b>Uncategorized: ${fmt(un.amount)}</b> across ${un.count} transaction(s) this month — needs review in Firefly.
        ${un.low_confidence ? " That's " + un.pct_of_spend + "% of this month's spending, so budget conclusions may be off." : ""}
      </div>` : "";
    const ub = d.unbudgeted || {};
    const ubCats = Object.entries(ub.categories || {}).sort((a, b2) => b2[1] - a[1]).slice(0, 4);
    const unbudgeted = ubCats.length ? `
      <p class="empty" style="margin-top:10px">Not in any budget: ${ubCats.map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(" · ")}</p>` : "";

    // ---- recurring charges ----
    // The Money card shows only what CHANGED; the full inventory lives here.
    // Every row states its own evidence — how many charges it was inferred
    // from, and when it was last seen — because a subscription list you
    // can't audit is a list you can't act on.
    let recSection = "";
    if (rec.available) {
      const badge = { appeared: ["new", "started charging"],
                      changed: ["price", "changed price"],
                      resumed: ["back", "charged again after a break"] };
      const evs = (rec.events || []).map((e) => {
        const [tag] = badge[e.event] || ["", ""];
        const what = e.event === "changed"
          ? `${fmt(e.from)} → <b>${fmt(e.to)}</b>`
          : `<b>${fmt(e.amount)}</b> ${esc(e.cadence || "")}`;
        return `<div class="btxn"><span class="grow"><b>${esc(e.name)}</b>
          <span class="sub"> ${esc(tag)}${e.confidence === "low" ? " · seen twice only" : ""}</span></span>
          <span class="mono">${what}</span></div>`;
      }).join("");
      const rows = (rec.items || []).map((i) => `
        <div class="btxn"><span class="grow"><b>${esc(i.name)}</b>
          <span class="sub"> ${esc(i.cadence)} · ${i.charges} charges · last ${esc((i.last_seen || "").slice(5))}${i.known_bill ? " · a Firefly bill" : ""}${i.confidence === "low" ? " · low confidence" : ""}</span></span>
          <span class="mono">${fmt(i.amount)}</span></div>`).join("");
      // A truncated read is stated, never rounded off into a total.
      const note = rec.window_complete === false
        ? `<p class="empty" style="margin:4px 0 8px">Only part of the history could be read, so nothing here is claimed as new or newly resumed, and no monthly total is given — these are the charges that were seen.</p>`
        : (rec.monthly_equivalent
            ? `<p class="empty" style="margin:4px 0 8px">About <b>${fmt(rec.monthly_equivalent)}/month</b> committed across ${(rec.items || []).filter((i) => i.confidence === "high").length} recurring charges, over the last ${rec.lookback_days || 400} days. Two-charge patterns are listed but not counted.</p>`
            : "");
      if (rows || evs)
        recSection = `<h4>Recurring charges</h4>${note}${evs}${rows}`;
    } else if (rec.reason) {
      recSection = `<h4>Recurring charges</h4><p class="empty">Couldn't read the transaction history — ${esc(rec.reason)}. Not "no subscriptions found".</p>`;
    }

    // ---- bills ----
    const billRows = bills.supported ? bills.items.slice(0, 8).map((b) => `
      <div class="btxn"><span class="grow"><b>${esc(b.name)}</b></span>
        <span class="sub">${b.paid_this_month ? "paid ✓" : (b.next_due ? "due " + esc(b.next_due.slice(5)) : "")}</span>
        <span class="mono">${b.amount != null ? fmt(b.amount) : "—"}</span></div>`).join("") : "";

    body.innerHTML = `
      ${paused}${noMonthData}${syncLine}${stats}${paySection}
      ${warns ? `<div class="bwarns">${warns}</div>` : (fresh && (d.budgets || []).length ? '<p class="empty" style="margin:4px 0 10px">✓ All budgets compatible with the time remaining.</p>' : "")}
      <div class="vgrid">${vessels}</div>
      ${uncat}${unbudgeted}
      ${billRows ? `<h4>Fixed bills (from Firefly)</h4><div>${billRows}</div>` : ""}
      ${recSection}
      <div class="hx-btns" style="margin-top:14px">
        <button class="btn btn-ghost" id="bud-edit">Edit budgets</button>
      </div>`;

    body.querySelectorAll(".vcard").forEach((el) => {
      el.onclick = () => { const det = el.querySelector(".bdetail"); det.hidden = !det.hidden; };
    });
    const be = document.getElementById("bud-edit");
    if (be) be.onclick = () => { closeModal(); window.ccOpenSettings && window.ccOpenSettings(); };
    // fill animation: from empty to target on next frame
    requestAnimationFrame(() => requestAnimationFrame(() => {
      body.querySelectorAll(".vfill").forEach((r) => {
        const h = Number(r.dataset.h);
        r.setAttribute("y", 104 - h);
        r.setAttribute("height", h);
      });
    }));
  },

  async tasks(app, body) {
    const data = await api("/tasks/tasks");
    setMode();
    const rows = (data.tasks || [])
      .map(
        (tk) => `<div class="row" data-id="${tk.id}" style="cursor:pointer">
          <span class="chip" style="background:${tk.done ? "rgba(110,231,183,.16)" : "rgba(124,156,255,.15)"}">${tk.done ? "✓ done" : "open"}</span>
          <div class="grow" style="${tk.done ? "opacity:.5;text-decoration:line-through" : ""}">${esc(tk.title)}</div>
          <button class="btn btn-ghost" data-del="${tk.id}" style="padding:4px 10px;font-size:12px">✕</button></div>`
      )
      .join("");
    body.innerHTML = `
      <h4>To-do${data.count ? ` (${data.count})` : ""}</h4>
      <div class="rows" id="task-rows">${rows || '<p class="empty">Nothing to do. 🎉</p>'}</div>
      <p class="empty" style="margin-top:8px">Tap a task to toggle done. ✕ to delete.</p>
      <h4>Add a task</h4>
      <div class="inline-form">
        <input id="task-title" placeholder="What needs doing?" />
        <button class="btn" id="task-add">Add</button>
      </div>`;
    body.querySelectorAll("#task-rows .row").forEach((r) => {
      r.onclick = async (e) => {
        if (e.target.closest("[data-del]")) return;
        await api(`/tasks/tasks/${r.dataset.id}/toggle`, { method: "POST" });
        openApp(app);
      };
    });
    body.querySelectorAll("#task-rows [data-del]").forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        await api(`/tasks/tasks/${btn.dataset.del}`, { method: "DELETE" });
        openApp(app);
      };
    });
    $("#task-add").onclick = async () => {
      const title = $("#task-title").value.trim();
      if (!title) return;
      await api("/tasks/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      openApp(app);
    };
  },

  // Bones' Desk — the manager's own station. Opens the assistant's full
  // briefing + ask box + text-me, in place of the old always-visible panel.
  async assistant(app, body) {
    const data = await api("/assistant/briefing");
    setMode();
    const rows = (data.items || [])
      .map(
        (it) => `<li><div class="src">${esc(it.source ?? "assistant")}</div>${esc(it.message)}</li>`
      )
      .join("");
    const summary = data.summary ?? `${(data.items || []).length} things on your plate.`;
    body.innerHTML = `
      <p class="muted">${esc(summary)} · synced ${timeAgo(data.synced_at)}</p>
      <ul class="briefing">${rows || "<li>Nothing needs you right now. Nice.</li>"}</ul>
      <div class="ask">
        <input id="modal-ask-input" placeholder="Ask your assistant… (e.g. what's due? what should I do today?)" />
        <button class="btn" id="modal-ask-btn">Ask</button>
      </div>
      <p id="modal-ask-answer" class="ask-answer"></p>
      <button class="btn btn-ghost" id="modal-text-btn" style="margin-top:12px">📱 Text me a digest</button>`;

    const ask = async () => {
      const input = $("#modal-ask-input");
      const out = $("#modal-ask-answer");
      const q = input.value.trim();
      if (!q) return;
      out.textContent = "Thinking…";
      try {
        const d = await api(`/assistant/ask?q=${encodeURIComponent(q)}`);
        out.textContent = d.answer || "Not sure about that one.";
      } catch {
        out.textContent = "Couldn't reach the assistant.";
      }
    };
    $("#modal-ask-btn").onclick = ask;
    $("#modal-ask-input").addEventListener("keydown", (e) => e.key === "Enter" && ask());

    $("#modal-text-btn").onclick = async () => {
      const btn = $("#modal-text-btn");
      btn.disabled = true;
      const original = btn.textContent;
      try {
        const r = await api("/assistant/notify", { method: "POST" });
        btn.textContent = r.sent ? "✓ Sent" : "⚠ Failed";
        if (r.sent) {
          $("#modal-ask-answer").textContent = `Bones sent: "${r.message}"`;
        } else if (r.reason && !/NOTIFY_CHANNEL/.test(r.reason)) {
          $("#modal-ask-answer").textContent = `Couldn't send (${r.reason}). See docs/SETUP-NOTIFICATIONS.md.`;
        } else {
          $("#modal-ask-answer").textContent =
            "WhatsApp texting isn't set up yet. See docs/SETUP-NOTIFICATIONS.md.";
        }
      } catch {
        btn.textContent = "⚠ Failed";
      } finally {
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2500);
      }
    };
  },

  async deals(app, body) {
    const data = await api("/deals/deals");
    setMode();
    const rows = (data.deals || [])
      .map(
        (d) => `<div class="row"><div class="grow"><b>${esc(d.merchant)}</b>
          <div class="sub">${esc(d.offer)}</div></div>
          <span class="right">${esc(d.source)}</span></div>`
      )
      .join("");
    body.innerHTML = `
      <h4>Real deals spotted</h4>
      <div class="rows">${rows || '<p class="empty">Nothing yet — Scout checks your inbox on every sync.</p>'}</div>
      <h4>Add one manually</h4>
      <div class="inline-form">
        <input id="deal-merchant" placeholder="Merchant (e.g. Grubhub)" />
        <input id="deal-offer" placeholder="Offer (e.g. 20% off)" />
        <button class="btn" id="deal-add">Add</button>
      </div>`;
    $("#deal-add").onclick = async () => {
      const merchant = $("#deal-merchant").value.trim();
      const offer = $("#deal-offer").value.trim();
      if (!merchant || !offer) return;
      await api("/deals/deals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ merchant, offer, source: "manual" }),
      });
      openApp(app);
    };
  },

  async networth(app, body) {
    const accts = await api("/networth/accounts");
    setMode();
    const total = (accts.accounts || []).reduce((s, a) => s + Number(a.balance), 0);
    if (accts.source === "firefly") {
      const web = accts.web_url;
      const openBtn = web
        ? `<a class="btn" href="${web}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-bottom:14px">Open in Firefly ↗</a>`
        : "";
      const rowsFF = (accts.accounts || [])
        .map((a) => {
          const bal = Number(a.balance);
          const neg = bal < 0;
          return `<div class="row"><div class="grow"><b>${esc(a.name)}</b></div>
            <span class="right" style="${neg ? "color:#ff6b6b" : ""}">${neg ? "-" : ""}$${Math.abs(bal).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span></div>`;
        })
        .join("");
      body.innerHTML = `
        <p class="empty" style="margin-bottom:14px">💎 Sourced live from your <b>Firefly III</b> — balances update as Firefly does.</p>
        ${openBtn}
        <div class="tiles">
          <div class="tile good"><div class="n">$${total.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div><div class="l">Total net worth</div></div>
          <div class="tile"><div class="n">${(accts.accounts || []).length}</div><div class="l">Accounts</div></div>
        </div>
        <h4>Accounts</h4>
        <div class="rows">${rowsFF || '<p class="empty">No accounts in Firefly yet.</p>'}</div>`;
      return;
    }
    const rec = await api("/networth/recurring");
    const acctOptions = (accts.accounts || [])
      .map((a) => `<option value="${a.id}">${esc(a.name)}</option>`)
      .join("");
    const acctRows = (accts.accounts || [])
      .map(
        (a) => `<div class="row" data-id="${a.id}">
          <div class="grow"><b>${esc(a.name)}</b></div>
          <span class="right">$${Number(a.balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
          <input type="number" class="acct-set" placeholder="New balance" style="max-width:110px" />
          <button class="btn" data-set="${a.id}">Set</button>
        </div>`
      )
      .join("");
    const recRows = (rec.recurring || [])
      .map(
        (r) => `<div class="row" data-rid="${r.id}"><div class="grow">
          <b>+$${Number(r.amount).toLocaleString()}</b> every ${r.interval_days}d to ${esc(r.account)}
          <div class="sub">next: ${esc(r.next_due_at)}</div></div>
          <button class="btn btn-ghost" data-del="${r.id}">Remove</button></div>`
      )
      .join("");
    body.innerHTML = `
      <div class="tiles">
        <div class="tile good"><div class="n">$${total.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div><div class="l">Total net worth</div></div>
        <div class="tile"><div class="n">${(accts.accounts || []).length}</div><div class="l">Accounts</div></div>
      </div>
      <h4>Accounts</h4>
      <div class="rows">${acctRows || '<p class="empty">No accounts yet.</p>'}</div>
      <h4>Recurring contributions</h4>
      <div class="rows">${recRows || '<p class="empty">None set up — add one below.</p>'}</div>
      <div class="inline-form">
        <select id="rec-account" style="flex:1;min-width:140px;background:var(--panel-2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 11px;">
          ${acctOptions}
        </select>
        <input id="rec-amount" type="number" placeholder="$ amount" style="max-width:100px" />
        <input id="rec-days" type="number" placeholder="Every N days" value="14" style="max-width:120px" />
        <button class="btn" id="rec-add">Add recurring</button>
      </div>
      <h4>Add an account</h4>
      <div class="inline-form">
        <input id="acct-name" placeholder="Account name" />
        <input id="acct-balance" type="number" placeholder="Starting balance" style="max-width:130px" />
        <button class="btn" id="acct-add">Add</button>
      </div>`;

    body.querySelectorAll("[data-set]").forEach((btn) => {
      btn.onclick = async () => {
        const row = body.querySelector(`.row[data-id="${btn.dataset.set}"]`);
        const val = parseFloat(row.querySelector(".acct-set").value);
        if (Number.isNaN(val)) return;
        await api(`/networth/accounts/${btn.dataset.set}/balance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ balance: val }),
        });
        openApp(app);
      };
    });
    body.querySelectorAll("[data-del]").forEach((btn) => {
      btn.onclick = async () => {
        await api(`/networth/recurring/${btn.dataset.del}`, { method: "DELETE" });
        openApp(app);
      };
    });
    $("#rec-add").onclick = async () => {
      const account_id = parseInt($("#rec-account").value);
      const amount = parseFloat($("#rec-amount").value);
      const interval_days = parseInt($("#rec-days").value) || 14;
      if (!account_id || !amount) return;
      await api("/networth/recurring", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id, amount, interval_days }),
      });
      openApp(app);
    };
    $("#acct-add").onclick = async () => {
      const name = $("#acct-name").value.trim();
      const balance = parseFloat($("#acct-balance").value) || 0;
      if (!name) return;
      await api("/networth/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, balance }),
      });
      openApp(app);
    };
  },

  async vault(app, body) {
    const [sum, list] = await Promise.all([api("/vault/summary"), api("/vault/items")]);
    setMode(sum.mode);
    const chipColor = {
      weak: "#ff6b6b", reused: "#ff6b6b", old: "#ffcc66",
      "insecure-url": "#ffcc66", "no-2fa": "#8a93a6",
    };
    const chip = (i) =>
      `<span style="font-size:11px;padding:2px 7px;border-radius:999px;margin-left:4px;` +
      `background:${chipColor[i] || "#8a93a6"}22;color:${chipColor[i] || "#8a93a6"}">${esc(i)}</span>`;
    const connected = sum.connected;
    const score = connected ? (sum.score ?? 0) : "—";
    const scoreCls = !connected ? "" : score >= 80 ? "good" : score >= 50 ? "warn" : "alert";
    const rows = (list.items || [])
      .map(
        (it) => `<div class="row"><div class="grow"><b>${esc(it.name)}</b>
          <div class="sub">${esc(it.username || "—")}${it.hosts?.length ? " · " + esc(it.hosts[0]) : ""}</div></div>
          <div style="text-align:right;max-width:55%">${(it.issues || []).map(chip).join("") || '<span class="sub">✓ ok</span>'}</div></div>`
      )
      .join("");
    const banner = sum.connected
      ? ""
      : `<p class="empty" style="margin-bottom:14px">🔐 <b>Not connected.</b> Set <code>VAULT_MODE=bitwarden</code> and <code>BW_SERVE_URL</code> (docs/SETUP-VAULT.md) to see your real vault health. Passwords are never stored or shown here.</p>`;
    body.innerHTML = `
      ${banner}
      <div class="tiles">
        <div class="tile ${scoreCls}"><div class="n">${esc(score)}</div><div class="l">Health score</div></div>
        <div class="tile ${sum.weak ? "alert" : ""}"><div class="n">${esc(sum.weak ?? 0)}</div><div class="l">Weak</div></div>
        <div class="tile ${sum.reused ? "alert" : ""}"><div class="n">${esc(sum.reused ?? 0)}</div><div class="l">Reused</div></div>
        <div class="tile ${sum.no_totp ? "warn" : ""}"><div class="n">${esc(sum.no_totp ?? 0)}</div><div class="l">No 2FA</div></div>
      </div>
      <h4>Logins to fix (worst first)</h4>
      <div class="rows">${rows || '<p class="empty">No items.</p>'}</div>
      <p class="empty" style="margin-top:10px">Read-only — manage entries in Vaultwarden itself.</p>`;
  },

  async plex(app, body) {
    const d = await api("/plex/dashboard");
    setMode(d.connected === false ? "disconnected" : "");
    const web = d.web_url;
    // Deep-link a title to its page on app.plex.tv (works from anywhere).
    const link = (label, id) =>
      web && id
        ? `<a href="${web}/details?key=${encodeURIComponent("/library/metadata/" + id)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;text-underline-offset:2px">${esc(label)}</a>`
        : esc(label);
    const banner = d.connected === false
      ? `<p class="empty" style="margin-bottom:14px">🎬 <b>Not connected.</b> Set <code>PLEX_TOKEN</code> (docs/SETUP-PLEX.md) to see the Plex server shared with you — continue watching, new stuff, and one-click open in Plex.</p>`
      : "";
    const openBtn = web
      ? `<a class="btn" href="${web}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-bottom:14px">▶ Open in Plex ↗</a>`
      : "";
    const cont = (d.continue || [])
      .map((c) => `<div class="bar-row"><div class="top"><b>${link(c.name, c.id)}</b><span class="amt">${esc(c.percent)}%</span></div>
        <div class="bar"><span style="width:${Math.min(100, c.percent)}%;background:#e5a00d"></span></div></div>`)
      .join("");
    const recent = (d.recent || [])
      .map((r) => web && r.id
        ? `<a class="pill" href="${web}/details?key=${encodeURIComponent("/library/metadata/" + r.id)}" target="_blank" rel="noopener" style="text-decoration:none">${esc(r.name)}</a>`
        : `<span class="pill">${esc(r.name)}</span>`).join("");
    const libs = (d.libraries || [])
      .map((l) => `<div class="tile"><div class="n">${l.count != null ? esc(l.count) : "—"}</div><div class="l">${esc(l.title)}</div></div>`)
      .join("");
    body.innerHTML = `
      ${banner}${openBtn}
      ${d.server ? `<p class="empty" style="margin-bottom:10px">Server: <b style="color:var(--text)">${esc(d.server)}</b></p>` : ""}
      ${libs ? `<div class="tiles">${libs}</div>` : ""}
      <h4>Continue watching</h4>
      <div>${cont || '<p class="empty">Nothing in progress.</p>'}</div>
      <h4>Recently added</h4>
      <div class="pill-list">${recent || '<p class="empty">Nothing new.</p>'}</div>`;
  },

  async firefly(app, body) {
    const d = await api("/firefly/dashboard");
    setMode(d.connected === false ? "disconnected" : "");
    const web = d.web_url;
    const imp = d.importer_url;
    const disp = (m) => (m && m.display) || "—";
    const banner = d.connected === false
      ? `<p class="empty" style="margin-bottom:14px">📒 <b>Not connected.</b> Set <code>FIREFLY_URL</code> and <code>FIREFLY_TOKEN</code> (docs/SETUP-FIREFLY.md)${web ? ` — or <a href="${web}" target="_blank" rel="noopener">open Firefly</a> to make a token` : ""} to see your real finances.</p>`
      : "";
    const openBtn = web
      ? `<a class="btn" href="${web}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-bottom:14px">Open in Firefly ↗</a>`
      : "";
    const impBtn = imp
      ? `<a class="btn" href="${imp}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-bottom:14px;margin-left:8px">Import data ↗</a>`
      : "";
    const accts = (d.accounts || [])
      .map((a) => `<div class="row"><div class="grow"><b>${esc(a.name)}</b></div>
        <span class="right">$${esc(Number(a.balance).toLocaleString(undefined, { minimumFractionDigits: 2 }))}</span></div>`)
      .join("");
    const txColor = { withdrawal: "#ff6b6b", deposit: "#6ee7b7", transfer: "#8a93a6" };
    const recent = (d.recent || [])
      .map((t) => {
        const c = txColor[t.type] || "#8a93a6";
        const amt = Number(t.amount);
        return `<div class="row"><div class="grow"><b>${esc(t.desc)}</b><div class="sub">${esc(t.date)} · ${esc(t.type)}</div></div>
          <span class="right" style="color:${c}">${amt >= 0 ? "+" : ""}$${esc(Math.abs(amt).toLocaleString(undefined, { minimumFractionDigits: 2 }))}</span></div>`;
      })
      .join("");
    body.innerHTML = `
      ${banner}${openBtn}${impBtn}
      <div class="tiles">
        <div class="tile good"><div class="n">${disp(d.net_worth)}</div><div class="l">Net worth</div></div>
        <div class="tile"><div class="n">${disp(d.earned)}</div><div class="l">Earned (mo)</div></div>
        <div class="tile alert"><div class="n">${disp(d.spent)}</div><div class="l">Spent (mo)</div></div>
        <div class="tile"><div class="n">${disp(d.left_to_spend)}</div><div class="l">Left to spend</div></div>
      </div>
      ${spendingDonut(d.categories, "Spending by category — last 30 days")}
      <h4>Accounts</h4>
      <div class="rows">${accts || '<p class="empty">No accounts.</p>'}</div>
      <h4>Recent transactions</h4>
      <div class="rows">${recent || '<p class="empty">Nothing recent.</p>'}</div>`;
  },
};

async function renderGeneric(app, body) {
  setMode();
  body.innerHTML = `<p>${esc(app.description)}</p>`;
}
