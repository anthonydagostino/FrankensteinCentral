const $ = (sel) => document.querySelector(sel);

function tickClock() {
  const el = $("#clock");
  if (!el) return;  // lounge-only element; the command center has its own clock
  const now = new Date();
  el.textContent = now.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
setInterval(tickClock, 1000);
tickClock();

// Every sub-app's catalog entry + live health, fetched once and reused so
// clicking a station on the HQ floor can open the right modal.
let APPS = [];
let HEALTH = {};
async function loadAppsData() {
  try {
    const [apps, health] = await Promise.all([
      fetch("/api/apps").then((r) => r.json()),
      fetch("/api/health").then((r) => r.json()).catch(() => ({})),
    ]);
    APPS = apps;
    HEALTH = health;
  } catch {}
}

function timeAgo(iso) {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

async function loadOverview() {
  const el = document.querySelector("#overview");
  if (!el) return;  // lounge-only element
  let o;
  try {
    o = await fetch("/api/assistant/overview").then((r) => r.json());
  } catch {
    return;
  }
  const evt = o.next_event
    ? `${o.next_event}`.slice(0, 18) + (o.next_event.length > 18 ? "…" : "")
    : "—";
  const tiles = [
    { n: `$${(o.net_worth ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, l: "Net worth", cls: "good" },
    { n: o.emails_to_reply ?? 0, l: "Emails to reply", cls: o.emails_to_reply ? "warn" : "" },
    { n: `$${o.budget_left ?? 0}`, l: "Budget left", cls: o.budget_over ? "alert" : "good" },
    { n: o.today_focus ?? "Rest", l: "Today's workout", cls: "" },
    { n: evt, l: "Next up", cls: "" },
  ];
  el.innerHTML = tiles
    .map((t) => `<div class="ov ${t.cls}"><div class="n">${t.n}</div><div class="l">${t.l}</div></div>`)
    .join("");
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

// Inline-SVG donut for category spending (no external chart libs — CSP-safe).
const DONUT_COLORS = ["#e0592a", "#f5c542", "#5bd6c0", "#4aa3ff", "#c58cff",
  "#7bd88f", "#ff8a5b", "#38bdf8", "#a3e635", "#f2b8d0"];

function spendingDonut(cats, title) {
  const items = (cats || []).filter((c) => Number(c.amount) > 0);
  if (!items.length) return "";
  // Keep the biggest 8 slices; roll the rest into "Other".
  const sorted = [...items].sort((a, b) => b.amount - a.amount);
  const top = sorted.slice(0, 8);
  const rest = sorted.slice(8);
  if (rest.length) top.push({ name: "Other", amount: rest.reduce((s, c) => s + Number(c.amount), 0) });
  const total = top.reduce((s, c) => s + Number(c.amount), 0);
  const cx = 100, cy = 100, rO = 82, rI = 50;
  const pt = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  let a0 = -Math.PI / 2;
  const slices = top.map((c, i) => {
    const frac = Number(c.amount) / total;
    const a1 = a0 + frac * Math.PI * 2;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const [x0, y0] = pt(rO, a0), [x1, y1] = pt(rO, a1);
    const [x2, y2] = pt(rI, a1), [x3, y3] = pt(rI, a0);
    // A single full slice can't be drawn as one arc; nudge it closed.
    const path = frac >= 0.999
      ? `M${cx - rO} ${cy} A${rO} ${rO} 0 1 1 ${cx + rO} ${cy} A${rO} ${rO} 0 1 1 ${cx - rO} ${cy} M${cx - rI} ${cy} A${rI} ${rI} 0 1 0 ${cx + rI} ${cy} A${rI} ${rI} 0 1 0 ${cx - rI} ${cy} Z`
      : `M${x0.toFixed(2)} ${y0.toFixed(2)} A${rO} ${rO} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} L${x2.toFixed(2)} ${y2.toFixed(2)} A${rI} ${rI} 0 ${large} 0 ${x3.toFixed(2)} ${y3.toFixed(2)} Z`;
    a0 = a1;
    return `<path d="${path}" fill="${DONUT_COLORS[i % DONUT_COLORS.length]}" fill-rule="evenodd"></path>`;
  }).join("");
  const legend = top.map((c, i) => {
    const pct = Math.round((Number(c.amount) / total) * 100);
    return `<div class="row" style="padding:4px 0"><div class="grow">
      <span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${DONUT_COLORS[i % DONUT_COLORS.length]};margin-right:8px"></span>
      <b>${esc(c.name)}</b> <span class="sub">${pct}%</span></div>
      <span class="right">$${Number(c.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span></div>`;
  }).join("");
  const totalTxt = `$${Math.round(total).toLocaleString()}`;
  return `
    <h4>${esc(title || "Spending — last 30 days")}</h4>
    <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
      <svg viewBox="0 0 200 200" width="180" height="180" style="flex:0 0 auto">
        ${slices}
        <text x="100" y="96" text-anchor="middle" fill="var(--text)" font-size="20" font-weight="700">${totalTxt}</text>
        <text x="100" y="116" text-anchor="middle" fill="var(--muted, #8a93a6)" font-size="11">30-day spend</text>
      </svg>
      <div style="flex:1;min-width:200px">${legend}</div>
    </div>`;
}

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
    const [sum, list] = await Promise.all([api("/budget/summary"), api("/budget/categories")]);
    setMode();
    const bars = (list.categories || [])
      .map((c) => {
        const lim = Number(c.limit_amount) || 0;
        const spent = Number(c.spent) || 0;
        const pct = lim ? Math.min(100, Math.round((spent / lim) * 100)) : 0;
        const cls = spent > lim ? "over" : pct >= 80 ? "warn" : "";
        return `<div class="bar-row" data-id="${c.id}" style="cursor:pointer" title="Click to add $10 spent">
          <div class="top"><b>${esc(c.name)}</b><span class="amt">$${esc(spent)} / $${esc(lim)}</span></div>
          <div class="bar ${cls}"><span style="width:${pct}%"></span></div></div>`;
      })
      .join("");
    body.innerHTML = `
      <div class="tiles">
        <div class="tile good"><div class="n">$${esc(sum.remaining ?? 0)}</div><div class="l">Left this month</div></div>
        <div class="tile"><div class="n">${esc(sum.percent_used ?? 0)}%</div><div class="l">Of budget used</div></div>
        <div class="tile ${sum.over_budget?.length ? "alert" : ""}"><div class="n">${esc(sum.over_budget?.length ?? 0)}</div><div class="l">Over budget</div></div>
      </div>
      <h4>Spending by category</h4>
      <div id="bud-rows">${bars || '<p class="empty">No categories yet.</p>'}</div>
      <p class="empty" style="margin-top:8px">Click a category to add $10 spent.</p>
      <h4>Add a category</h4>
      <div class="inline-form">
        <input id="bud-name" placeholder="Category (e.g. Coffee)" />
        <input id="bud-limit" type="number" placeholder="Monthly $" style="max-width:110px" />
        <button class="btn" id="bud-add">Add</button>
      </div>`;
    body.querySelectorAll("#bud-rows .bar-row").forEach((r) => {
      r.onclick = async () => {
        await api(`/budget/categories/${r.dataset.id}/spend`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount: 10 }),
        });
        openApp(app);
      };
    });
    $("#bud-add").onclick = async () => {
      const name = $("#bud-name").value.trim();
      const limit = parseFloat($("#bud-limit").value) || 0;
      if (!name) return;
      await api("/budget/categories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, limit }),
      });
      openApp(app);
    };
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

  async jellyfin(app, body) {
    const d = await api("/jellyfin/dashboard");
    setMode(d.connected === false ? "disconnected" : "");
    const web = d.web_url;
    // Deep-link a title to its Jellyfin details/play page (when connected).
    const link = (label, id) =>
      web && id
        ? `<a href="${web}/web/#/details?id=${encodeURIComponent(id)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;text-underline-offset:2px">${esc(label)}</a>`
        : esc(label);
    const banner = d.connected === false
      ? `<p class="empty" style="margin-bottom:14px">🎬 <b>Not connected.</b> Set <code>JELLYFIN_URL</code> and <code>JELLYFIN_API_KEY</code> (docs/SETUP-JELLYFIN.md) to see your real library — then every title here opens straight in Jellyfin.</p>`
      : "";
    const openBtn = web
      ? `<a class="btn" href="${web}/web/" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block;margin-bottom:14px">▶ Open in Jellyfin ↗</a>`
      : "";
    const now = (d.nowplaying || [])
      .map((s) => `<div class="row"><span class="chip" style="background:rgba(170,92,195,.18);color:#c77dde">▶ now</span>
        <div class="grow"><b>${link(s.item, s.id)}</b><div class="sub">${esc(s.user)} · ${esc(s.device)}</div></div></div>`)
      .join("");
    const cont = (d.continue || [])
      .map((c) => `<div class="bar-row"><div class="top"><b>${link(c.name, c.id)}</b><span class="amt">${esc(c.percent)}%</span></div>
        <div class="bar"><span style="width:${Math.min(100, c.percent)}%;background:#aa5cc3"></span></div></div>`)
      .join("");
    const nextup = (d.nextup || [])
      .map((n) => `<div class="row"><div class="grow">${link(n.name, n.id)}</div></div>`).join("");
    const recent = (d.recent || [])
      .map((r) => web && r.id
        ? `<a class="pill" href="${web}/web/#/details?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener" style="text-decoration:none">${esc(r.name)}</a>`
        : `<span class="pill">${esc(r.name)}</span>`).join("");
    const c = d.counts || {};
    body.innerHTML = `
      ${banner}${openBtn}
      <div class="tiles">
        <div class="tile"><div class="n">${esc(c.movies ?? 0)}</div><div class="l">Movies</div></div>
        <div class="tile"><div class="n">${esc(c.series ?? 0)}</div><div class="l">Series</div></div>
        <div class="tile"><div class="n">${esc(c.episodes ?? 0)}</div><div class="l">Episodes</div></div>
        <div class="tile ${(d.nowplaying || []).length ? "good" : ""}"><div class="n">${(d.nowplaying || []).length}</div><div class="l">Streaming now</div></div>
      </div>
      ${now ? `<h4>Streaming now</h4><div class="rows">${now}</div>` : ""}
      <h4>Continue watching</h4>
      <div>${cont || '<p class="empty">Nothing in progress.</p>'}</div>
      <h4>Next up</h4>
      <div class="rows">${nextup || '<p class="empty">All caught up.</p>'}</div>
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

loadAppsData();
loadOverview();

// Keep the hub live — reflects the assistant's own auto-sync without a refresh.
setInterval(() => {
  if (!$("#overlay").classList.contains("open")) {
    loadAppsData();
    loadOverview();
  }
}, 20000);

// ---- Assistant HQ — the "lounge" view (secondary page /lounge.html) ---------
// The lounge where Bones dispatches worker agents to API "stations". Idle
// agents wander; working agents walk to their station, do the job, then walk
// back. Every station is clickable and opens that app's real detail view.
// This whole section only runs when the lounge canvas is present, so app.js
// can also be loaded by the command-center homepage (which reuses the modals
// above but has no canvas).
if (document.getElementById("stage")) {

const WORLD = { w: 1000, h: 640 };
const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");

// Stations around the lounge. spot = where the worker stands to work.
const STATIONS = {
  desk:     { name: "Bones' Desk", color: "#e23b5a", x: 500, y: 96,  spot: { x: 500, y: 170 } },
  gmail:    { name: "Gmail",        color: "#4aa3ff", x: 120, y: 150, spot: { x: 210, y: 190 } },
  schedule: { name: "Schedule",     color: "#7bd88f", x: 880, y: 150, spot: { x: 790, y: 190 } },
  powerbuy: { name: "PowerBuy",     color: "#ff8a5b", x: 120, y: 500, spot: { x: 210, y: 460 } },
  fitness:  { name: "Fitness",      color: "#c58cff", x: 880, y: 500, spot: { x: 790, y: 460 } },
  finance:  { name: "Finance",      color: "#5bd6c0", x: 380, y: 566, spot: { x: 400, y: 512 } },
  deals:    { name: "Deals",        color: "#a3e635", x: 620, y: 566, spot: { x: 600, y: 512 } },
  tasks:    { name: "Tasks",        color: "#f2b8d0", x: 120, y: 325, spot: { x: 210, y: 325 } },
  budget:   { name: "Budget",       color: "#f5c542", x: 880, y: 325, spot: { x: 790, y: 325 } },
  networth: { name: "Net Worth",    color: "#38bdf8", x: 300, y: 200, spot: { x: 300, y: 258 } },
  vault:    { name: "Vault",        color: "#8b98a9", x: 700, y: 200, spot: { x: 700, y: 258 } },
  jellyfin: { name: "Jellyfin",     color: "#aa5cc3", x: 500, y: 566, spot: { x: 500, y: 512 } },
  firefly:  { name: "Firefly",      color: "#e0592a", x: 750, y: 104, spot: { x: 750, y: 158 } },
};

// Maps a station to the /api/apps entry it opens when clicked.
const STATION_APP_KEY = {
  desk: "assistant",
  gmail: "gmail",
  schedule: "schedule",
  powerbuy: "powerbuy",
  fitness: "fitness",
  finance: "finance",
  deals: "deals",
  tasks: "tasks",
  budget: "budget",
  networth: "networth",
  vault: "vault",
  jellyfin: "jellyfin",
  firefly: "firefly",
};

// Same icons as the registry cards, shown on each station's "screen".
const STATION_EMOJI = {
  desk: "🧠",
  gmail: "📬",
  schedule: "🗓️",
  powerbuy: "🛒",
  fitness: "💪",
  finance: "💸",
  deals: "🏷️",
  tasks: "✅",
  budget: "📊",
  networth: "💎",
  vault: "🔐",
  jellyfin: "🎬",
  firefly: "📒",
};

// Cozy home spots around the central rug where idle workers hang out.
const HOME = [
  { x: 430, y: 390 }, { x: 570, y: 390 }, { x: 430, y: 450 }, { x: 570, y: 450 },
  { x: 500, y: 420 }, { x: 470, y: 420 }, { x: 530, y: 420 }, { x: 500, y: 450 },
  { x: 460, y: 460 }, { x: 540, y: 460 },
];

const WALK = { minX: 90, maxX: 910, minY: 150, maxY: 560 };
const rnd = (a, b) => a + Math.random() * (b - a);

let agents = [];
let t = 0;

function makeAgent(def, home) {
  const start = home || { x: rnd(400, 600), y: rnd(380, 480) };
  return {
    ...def,
    x: start.x, y: start.y,
    tx: start.x, ty: start.y,
    home,
    state: "idle",        // idle | traveling | working | returning
    facing: 1,
    bob: Math.random() * 10,
    speed: def.role === "manager" ? 42 : 70,
    pause: rnd(0.5, 2.5),
    work: 0,
    bubble: null, bubbleT: 0,
    blink: rnd(2, 6), blinking: 0,   // eye blinks
    gaze: 0, gazeT: rnd(1, 3),       // idle look-around
    dustT: 0,                        // footstep-dust throttle
    cheer: 0,                        // manager sword-raise on dispatch
    sparkT: 0,                       // work-spark throttle
  };
}

// Little floor dust puffs kicked up while walking.
const dust = [];
function puff(x, y) {
  dust.push({ x: x + rnd(-3, 3), y, life: 0.5, max: 0.5, r: rnd(3, 6) });
}

// Sparks that fly off a terminal while a worker is busy at it.
const sparks = [];
function spark(x, y, color) {
  sparks.push({
    x, y, vx: rnd(-30, 30), vy: rnd(-50, -15),
    life: 0.6, max: 0.6, color,
  });
}

async function loadRoster() {
  let roster;
  try {
    const r = await fetch("/api/assistant/agents");
    roster = (await r.json()).agents;
  } catch (e) {
    roster = [
      { id: "bones", name: "Bones", role: "manager", station: "desk", color: "#e23b5a" },
      { id: "posty", name: "Posty", role: "worker", station: "gmail", color: "#4aa3ff" },
      { id: "cal", name: "Cal", role: "worker", station: "schedule", color: "#7bd88f" },
      { id: "rep", name: "Rep", role: "worker", station: "powerbuy", color: "#ff8a5b" },
      { id: "coach", name: "Coach", role: "worker", station: "fitness", color: "#c58cff" },
      { id: "penny", name: "Penny", role: "worker", station: "finance", color: "#5bd6c0" },
      { id: "tess", name: "Tess", role: "worker", station: "tasks", color: "#f2b8d0" },
      { id: "buck", name: "Buck", role: "worker", station: "budget", color: "#f5c542" },
      { id: "scout", name: "Scout", role: "worker", station: "deals", color: "#a3e635" },
      { id: "wade", name: "Wade", role: "worker", station: "networth", color: "#38bdf8" },
      { id: "vic", name: "Vic", role: "worker", station: "vault", color: "#8b98a9" },
      { id: "milo", name: "Milo", role: "worker", station: "jellyfin", color: "#aa5cc3" },
      { id: "fitz", name: "Fitz", role: "worker", station: "firefly", color: "#e0592a" },
    ];
  }
  let wi = 0;
  agents = roster.map((a) => {
    const home = a.role === "manager" ? STATIONS.desk.spot : HOME[wi++ % HOME.length];
    return makeAgent(a, home);
  });
  renderLegend(roster);
}

function renderLegend(roster) {
  document.getElementById("legend").innerHTML = roster
    .map(
      (a) =>
        `<span class="hq-chip"><span class="hq-dot" style="background:${a.color}"></span>${a.name}` +
        `<span style="opacity:.6">· ${a.station}</span></span>`
    )
    .join("");
}

// ---- movement ---------------------------------------------------------------

function moveToward(a, dt) {
  const dx = a.tx - a.x, dy = a.ty - a.y;
  const d = Math.hypot(dx, dy);
  if (d < 2) { a.x = a.tx; a.y = a.ty; return true; }
  const step = Math.min(d, a.speed * dt);
  a.x += (dx / d) * step;
  a.y += (dy / d) * step;
  if (Math.abs(dx) > 1) a.facing = dx > 0 ? 1 : -1;
  a.bob += dt * 10;
  return false;
}

function update(dt) {
  // dust particles
  for (let i = dust.length - 1; i >= 0; i--) {
    dust[i].life -= dt;
    if (dust[i].life <= 0) dust.splice(i, 1);
  }
  // spark particles
  for (let i = sparks.length - 1; i >= 0; i--) {
    const s = sparks[i];
    s.life -= dt; s.x += s.vx * dt; s.y += s.vy * dt; s.vy += 90 * dt;
    if (s.life <= 0) sparks.splice(i, 1);
  }

  for (const a of agents) {
    if (a.bubbleT > 0) a.bubbleT -= dt;
    if (a.cheer > 0) a.cheer -= dt;

    // throw sparks off the terminal while working
    if (a.state === "working") {
      a.sparkT -= dt;
      if (a.sparkT <= 0) {
        const st = STATIONS[a.station];
        spark(st.x + rnd(-14, 14), st.y - 6, st.color);
        a.sparkT = 0.12;
      }
    }

    // blink
    a.blink -= dt;
    if (a.blink <= 0) { a.blinking = 0.12; a.blink = rnd(2.5, 6); }
    if (a.blinking > 0) a.blinking -= dt;

    // idle look-around (only when not walking)
    if (a.state === "idle" || a.state === "working") {
      a.gazeT -= dt;
      if (a.gazeT <= 0) { a.gaze = [-1, 0, 0, 1][Math.floor(rnd(0, 4))]; a.gazeT = rnd(1, 3); }
    } else {
      a.gaze = 0;
    }

    // footstep dust while moving
    const moving = a.state === "traveling" || a.state === "returning";
    if (moving) {
      a.dustT -= dt;
      if (a.dustT <= 0) { puff(a.x - a.facing * 6, a.y + 15); a.dustT = 0.15; }
    }

    if (a.state === "idle") {
      if (moveToward(a, dt)) {
        a.pause -= dt;
        if (a.pause <= 0) {
          // manager mostly hovers near its desk; workers roam the lounge
          if (a.role === "manager" && Math.random() < 0.6) {
            a.tx = STATIONS.desk.spot.x + rnd(-40, 40);
            a.ty = STATIONS.desk.spot.y + rnd(-10, 30);
          } else {
            a.tx = rnd(WALK.minX + 40, WALK.maxX - 40);
            a.ty = rnd(340, WALK.maxY);
          }
          a.pause = rnd(0.8, 3);
        }
      }
    } else if (a.state === "traveling") {
      const spot = STATIONS[a.station].spot;
      a.tx = spot.x; a.ty = spot.y;
      if (moveToward(a, dt)) { a.state = "working"; a.work = 0; }
    } else if (a.state === "working") {
      a.work += dt / 2.6; // ~2.6s of work
      if (a.work >= 1) {
        a.state = "returning";
        a.tx = a.home.x; a.ty = a.home.y;
      }
    } else if (a.state === "returning") {
      if (moveToward(a, dt)) { a.state = "idle"; a.pause = rnd(0.5, 2); }
    }
  }
}

function dispatchJob(job) {
  const a = agents.find((x) => x.id === job.agent);
  if (!a) return;
  a.state = "traveling";
  a.bubble = job.summary;
  a.bubbleT = 6;
  const mgr = agents.find((x) => x.role === "manager");
  if (mgr) { mgr.bubble = "On it, team!"; mgr.bubbleT = 3; mgr.cheer = 1; }
}

// ---- click / hover: stations open the real app modals ----------------------

let hoverStation = null;

function canvasPoint(evt) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (evt.clientX - rect.left) * (WORLD.w / rect.width),
    y: (evt.clientY - rect.top) * (WORLD.h / rect.height),
  };
}

function stationAt(mx, my) {
  for (const key in STATIONS) {
    const s = STATIONS[key];
    if (mx >= s.x - 55 && mx <= s.x + 55 && my >= s.y - 40 && my <= s.y + 60) return key;
  }
  return null;
}

canvas.addEventListener("mousemove", (e) => {
  const p = canvasPoint(e);
  hoverStation = stationAt(p.x, p.y);
  canvas.style.cursor = hoverStation ? "pointer" : "default";
});
canvas.addEventListener("mouseleave", () => {
  hoverStation = null;
  canvas.style.cursor = "default";
});
canvas.addEventListener("click", (e) => {
  const p = canvasPoint(e);
  const key = stationAt(p.x, p.y);
  if (!key) return;
  const app = APPS.find((a) => a.key === STATION_APP_KEY[key]);
  if (app) openApp(app);
});

// ---- drawing ----------------------------------------------------------------

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawFloor() {
  // warm wood floor
  ctx.fillStyle = "#2a2233";
  ctx.fillRect(0, 0, WORLD.w, WORLD.h);
  ctx.fillStyle = "#31283d";
  for (let x = 0; x < WORLD.w; x += 56) ctx.fillRect(x, 0, 28, WORLD.h);
  // central rug
  ctx.save();
  ctx.globalAlpha = 0.9;
  ctx.fillStyle = "#3c2f52";
  roundRect(360, 320, 280, 190, 26); ctx.fill();
  ctx.strokeStyle = "#553f78"; ctx.lineWidth = 6;
  roundRect(374, 334, 252, 162, 20); ctx.stroke();
  ctx.restore();
  // vignette
  const g = ctx.createRadialGradient(500, 320, 200, 500, 320, 620);
  g.addColorStop(0, "rgba(0,0,0,0)");
  g.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, WORLD.w, WORLD.h);
}

function drawCouch(x, y, w, h, color) {
  ctx.fillStyle = "#00000033";
  roundRect(x - 4, y + h - 6, w + 8, 14, 8); ctx.fill();
  ctx.fillStyle = color;
  roundRect(x, y, w, h, 12); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  roundRect(x + 8, y + 8, w - 16, h * 0.45, 8); ctx.fill();
}

function drawPlant(x, y) {
  ctx.fillStyle = "#00000033";
  ctx.beginPath(); ctx.ellipse(x, y + 20, 16, 6, 0, 0, 7); ctx.fill();
  ctx.fillStyle = "#8a5a3c";
  roundRect(x - 10, y + 2, 20, 20, 4); ctx.fill();
  ctx.fillStyle = "#4c9a5a";
  for (const dx of [-8, 0, 8]) {
    ctx.beginPath();
    ctx.ellipse(x + dx, y - 6 + Math.sin(t * 2 + dx) * 2, 7, 16, dx * 0.05, 0, 7);
    ctx.fill();
  }
}

function drawLounge() {
  // couches around the rug
  drawCouch(360, 300, 280, 26, "#4a3b66"); // top back
  drawCouch(330, 340, 26, 150, "#443560"); // left
  drawCouch(644, 340, 26, 150, "#443560"); // right
  // coffee table
  ctx.fillStyle = "#00000033";
  roundRect(452, 402, 96, 44, 10); ctx.fill();
  ctx.fillStyle = "#5b4a2e";
  roundRect(450, 396, 100, 44, 10); ctx.fill();
  ctx.fillStyle = "#6f5a38";
  roundRect(458, 402, 84, 12, 6); ctx.fill();
  // plants + water cooler
  drawPlant(300, 300);
  drawPlant(700, 300);
  ctx.fillStyle = "#2f6f8f";
  roundRect(505, 250, 22, 40, 5); ctx.fill();
  ctx.fillStyle = "#8fd7ef";
  roundRect(508, 254, 16, 16, 3); ctx.fill();
}

function drawStation(key, s) {
  const active = agents.some((a) => a.station === key && a.state === "working");
  const hovered = key === hoverStation;
  const glow = active ? 0.6 + Math.sin(t * 8) * 0.35 : 0.18;
  // desk shadow + body
  ctx.fillStyle = "#00000038";
  roundRect(s.x - 52, s.y + 26, 104, 16, 8); ctx.fill();
  ctx.fillStyle = "#241d31";
  roundRect(s.x - 50, s.y - 34, 100, 66, 10); ctx.fill();
  // glowing screen
  ctx.save();
  ctx.shadowColor = s.color;
  ctx.shadowBlur = active ? 26 : 8;
  ctx.fillStyle = s.color;
  ctx.globalAlpha = glow;
  roundRect(s.x - 38, s.y - 24, 76, 40, 6); ctx.fill();
  ctx.restore();
  // scanlines on screen
  ctx.globalAlpha = 0.25;
  ctx.fillStyle = "#0b0b12";
  for (let i = -20; i < 16; i += 6) ctx.fillRect(s.x - 36, s.y + i, 72, 2);
  ctx.globalAlpha = 1;
  // the app's icon, right on the screen
  ctx.font = "26px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(STATION_EMOJI[key] || "❔", s.x, s.y - 4);
  ctx.textBaseline = "alphabetic";
  // hover ring — this station is clickable
  if (hovered) {
    ctx.strokeStyle = "rgba(255,255,255,0.65)";
    ctx.lineWidth = 2;
    roundRect(s.x - 55, s.y - 39, 110, 74, 13); ctx.stroke();
  }
  // label
  ctx.fillStyle = "#e9e3f6";
  ctx.font = "600 14px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(s.name, s.x, s.y + 54);
  // live health dot
  const status = HEALTH[STATION_APP_KEY[key]]?.status;
  if (status) {
    const tw = ctx.measureText(s.name).width;
    ctx.beginPath();
    ctx.fillStyle = status === "up" ? "#6ee7b7" : "#ff6b6b";
    ctx.arc(s.x + tw / 2 + 12, s.y + 50, 4, 0, 7);
    ctx.fill();
  }
}

function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  const clamp = (v) => Math.max(0, Math.min(255, v));
  const r = clamp(((n >> 16) & 255) + amt);
  const g = clamp(((n >> 8) & 255) + amt);
  const b = clamp((n & 255) + amt);
  return `rgb(${r},${g},${b})`;
}

const NINJA_BODY = "#22323f"; // dark navy, like the reference

// Manager: a skeleton knight — red-helmeted skull, bone body, sword + shield.
const BONE = "#eef1f3";
const BONE_SH = "#c9d2d7";
const HELM = "#d81f4a";

function drawManager(a, x, y) {
  const dir = a.facing;

  // shield behind, on the trailing side
  const sx = x - dir * 20;
  ctx.fillStyle = "#9c1636";
  ctx.beginPath();
  ctx.moveTo(sx - 9, y + 2); ctx.lineTo(sx + 9, y + 2);
  ctx.lineTo(sx + 9, y + 12); ctx.quadraticCurveTo(sx, y + 24, sx - 9, y + 12);
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = HELM;
  ctx.beginPath();
  ctx.moveTo(sx - 6, y + 3); ctx.lineTo(sx + 7, y + 3);
  ctx.lineTo(sx + 7, y + 11); ctx.quadraticCurveTo(sx, y + 20, sx - 6, y + 11);
  ctx.closePath(); ctx.fill();

  // sword in the leading hand — raises triumphantly when dispatching
  const raise = a.cheer > 0 ? -1.2 : 0;
  ctx.save();
  ctx.translate(x + dir * 15, y + 10);
  ctx.scale(dir, 1);
  ctx.rotate(raise);
  ctx.fillStyle = HELM; // guard
  roundRect(-2, -5, 4, 12, 1); ctx.fill();
  ctx.fillStyle = BONE_SH; // pommel
  ctx.beginPath(); ctx.arc(0, 8, 3, 0, 7); ctx.fill();
  ctx.fillStyle = BONE;   // blade
  ctx.beginPath();
  ctx.moveTo(3, -4); ctx.lineTo(34, -2); ctx.lineTo(38, 0); ctx.lineTo(34, 2);
  ctx.lineTo(3, 3); ctx.closePath(); ctx.fill();
  if (a.cheer > 0) { // a glint on the raised blade
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath(); ctx.arc(30, 0, 2, 0, 7); ctx.fill();
  }
  ctx.restore();

  // legs with a walk cycle
  const mv = a.state === "traveling" || a.state === "returning";
  const ls = mv ? Math.sin(a.bob * 1.3) * 3 : 0;
  ctx.fillStyle = BONE;
  roundRect(x - 9, y + 18 - Math.max(0, ls), 7, 8, 2); ctx.fill();
  roundRect(x + 2, y + 18 - Math.max(0, -ls), 7, 8, 2); ctx.fill();

  // bone torso with ribs + spine
  ctx.fillStyle = BONE;
  roundRect(x - 12, y + 1, 24, 20, 6); ctx.fill();
  ctx.strokeStyle = BONE_SH; ctx.lineWidth = 2; ctx.lineCap = "round";
  for (const ry of [6, 11, 16]) {
    ctx.beginPath(); ctx.moveTo(x - 7, y + ry); ctx.lineTo(x + 7, y + ry); ctx.stroke();
  }
  ctx.beginPath(); ctx.moveTo(x, y + 3); ctx.lineTo(x, y + 19); ctx.stroke();

  // skull
  ctx.fillStyle = BONE;
  roundRect(x - 11, y - 18, 22, 20, 9); ctx.fill();
  // jaw / cheek notch
  ctx.fillStyle = BONE_SH;
  roundRect(x - 5, y - 1, 10, 4, 2); ctx.fill();

  // red domed helmet over the top of the skull
  ctx.fillStyle = HELM;
  ctx.beginPath(); ctx.arc(x, y - 8, 12, Math.PI, 0); ctx.fill();
  roundRect(x - 12, y - 9, 24, 4, 2); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.4)"; // helmet shine
  ctx.beginPath(); ctx.ellipse(x - 4, y - 13, 2.5, 5, -0.5, 0, 7); ctx.fill();

  // eye sockets (big, black) — blink shrinks them
  const eh = a.blinking > 0 ? 1.2 : 5;
  const g = a.gaze * 1.4;
  ctx.fillStyle = "#12181d";
  for (const ex of [-4.5, 4.5]) {
    ctx.beginPath();
    ctx.ellipse(x + ex + g, y - 4, 3, eh, 0, 0, 7); ctx.fill();
  }
  // nose
  ctx.beginPath();
  ctx.moveTo(x - 2, y + 1); ctx.lineTo(x + 2, y + 1); ctx.lineTo(x, y - 2);
  ctx.closePath(); ctx.fill();
}

// Workers: blocky ninja with a per-agent bandana + matching cape.
function drawNinja(a, x, y) {
  const dir = a.facing;
  const cape = shade(a.color, -70);

  // cape flowing out behind (trailing side), with a gentle flutter
  ctx.save();
  ctx.translate(x - dir * 13, y + 6);
  ctx.rotate(-dir * (0.18 + Math.sin(t * 3 + a.bob) * 0.05));
  ctx.fillStyle = cape;
  roundRect(-10, -15, 20, 34, 9); ctx.fill();
  ctx.restore();

  // pointy ears
  ctx.fillStyle = NINJA_BODY;
  ctx.beginPath();
  ctx.moveTo(x - 14, y - 6); ctx.lineTo(x - 8, y - 24); ctx.lineTo(x - 2, y - 8);
  ctx.closePath(); ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x + 2, y - 8); ctx.lineTo(x + 8, y - 24); ctx.lineTo(x + 14, y - 6);
  ctx.closePath(); ctx.fill();

  // legs with a little walk cycle (drawn first, so the body overlaps their tops)
  const moving = a.state === "traveling" || a.state === "returning";
  const sw = moving ? Math.sin(a.bob * 1.3) * 3 : 0;
  ctx.fillStyle = NINJA_BODY;
  roundRect(x - 10, y + 17 - Math.max(0, sw), 7, 8, 2); ctx.fill();
  roundRect(x + 3, y + 17 - Math.max(0, -sw), 7, 8, 2); ctx.fill();

  // blocky body
  ctx.fillStyle = NINJA_BODY;
  roundRect(x - 15, y - 10, 30, 30, 7); ctx.fill();

  // bandana across the face (per-agent colour)
  ctx.fillStyle = a.color;
  roundRect(x - 16, y - 3, 32, 9, 2); ctx.fill();

  // knot + two trailing tails that wiggle as they move
  const wig = Math.sin(t * 6 + a.bob) * 4;
  const kx = x - dir * 15;
  ctx.beginPath();
  ctx.moveTo(kx, y - 2); ctx.lineTo(kx - dir * 8, y - 5);
  ctx.lineTo(kx - dir * 7, y + 4); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = a.color; ctx.lineWidth = 3; ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(kx - dir * 4, y - 1);
  ctx.quadraticCurveTo(kx - dir * 14, y + 2 + wig, kx - dir * 20, y + 6 - wig);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(kx - dir * 4, y + 3);
  ctx.quadraticCurveTo(kx - dir * 13, y + 8 - wig, kx - dir * 18, y + 12 + wig);
  ctx.stroke();

  // big eyes below the bandana (blink shrinks, gaze shifts the pupils)
  const eh = a.blinking > 0 ? 1 : 5.6;
  const g = a.gaze * 1.6;
  for (const ex of [-6, 6]) {
    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.ellipse(x + ex, y + 11, 4.6, eh, 0, 0, 7); ctx.fill();
    if (a.blinking <= 0) {
      ctx.fillStyle = "#1b2730";
      ctx.beginPath(); ctx.arc(x + ex + dir * 1.4 + g, y + 12, 2.1, 0, 7); ctx.fill();
    }
  }
}

function drawAgent(a) {
  const bob = a.state === "traveling" || a.state === "returning"
    ? Math.abs(Math.sin(a.bob)) * 3 : Math.sin(t * 2 + a.bob) * 1.5;
  const x = a.x, y = a.y - bob;

  // shadow
  ctx.fillStyle = "#00000040";
  ctx.beginPath(); ctx.ellipse(a.x, a.y + 16, 15, 5, 0, 0, 7); ctx.fill();

  if (a.role === "manager") drawManager(a, x, y);
  else drawNinja(a, x, y);

  // name tag
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.font = "600 11px system-ui, sans-serif";
  ctx.textAlign = "center";
  const tw = ctx.measureText(a.name).width + 12;
  roundRect(x - tw / 2, y - 40, tw, 15, 7); ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.fillText(a.name, x, y - 29);

  // work progress ring
  if (a.state === "working") {
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(x, y + 2, 20, 0, 7); ctx.stroke();
    ctx.strokeStyle = a.color;
    ctx.beginPath(); ctx.arc(x, y + 2, 20, -Math.PI / 2, -Math.PI / 2 + a.work * 7); ctx.stroke();
  }

  // speech bubble
  if (a.bubble && a.bubbleT > 0) {
    ctx.font = "600 12px system-ui, sans-serif";
    const text = a.bubble.length > 26 ? a.bubble.slice(0, 25) + "…" : a.bubble;
    const w = ctx.measureText(text).width + 18;
    const bx = x - w / 2, by = y - 62;
    ctx.fillStyle = "rgba(255,255,255,0.96)";
    roundRect(bx, by, w, 24, 8); ctx.fill();
    ctx.beginPath();
    ctx.moveTo(x - 5, by + 24); ctx.lineTo(x + 5, by + 24); ctx.lineTo(x, by + 31);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#241d31";
    ctx.fillText(text, x, by + 16);
  }
}

function draw() {
  ctx.clearRect(0, 0, WORLD.w, WORLD.h);
  drawFloor();
  drawLounge();
  for (const key in STATIONS) if (key !== "desk") drawStation(key, STATIONS[key]);
  drawStation("desk", STATIONS.desk);
  // footstep dust (under the agents)
  for (const d of dust) {
    ctx.globalAlpha = (d.life / d.max) * 0.4;
    ctx.fillStyle = "#b9a9c9";
    ctx.beginPath();
    ctx.arc(d.x, d.y, d.r * (1.4 - d.life / d.max), 0, 7);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  // work sparks (above stations, behind agents is fine)
  for (const s of sparks) {
    ctx.globalAlpha = Math.max(0, s.life / s.max);
    ctx.fillStyle = s.color;
    ctx.beginPath(); ctx.arc(s.x, s.y, 2.2, 0, 7); ctx.fill();
  }
  ctx.globalAlpha = 1;
  // draw agents sorted by y so lower ones overlap correctly
  for (const a of [...agents].sort((p, q) => p.y - q.y)) drawAgent(a);
}

// ---- loop + data ------------------------------------------------------------

let last = performance.now();
function loop(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  t += dt;
  update(dt);
  draw();
  requestAnimationFrame(loop);
}

function fitCanvas() {
  const wrap = canvas.parentElement;
  const scale = Math.min(wrap.clientWidth / WORLD.w, wrap.clientHeight / WORLD.h);
  canvas.width = WORLD.w;
  canvas.height = WORLD.h;
  canvas.style.width = WORLD.w * scale + "px";
  canvas.style.height = WORLD.h * scale + "px";
}
window.addEventListener("resize", fitCanvas);

async function refreshSpace() {
  let s;
  try { s = await (await fetch("/api/assistant/space")).json(); } catch { return; }

  const notes = document.getElementById("notes");
  notes.innerHTML = s.memory?.length
    ? s.memory.map((m) => `<p class="note">${esc(m.content)}</p>`).join("")
    : `<p class="empty">Nothing worth flagging right now.</p>`;

  const dls = document.getElementById("deadlines");
  dls.innerHTML = s.deadlines?.length
    ? s.deadlines
        .map((d) => {
          const when = d.due_at ? String(d.due_at).slice(0, 16).replace("T", " ") : "—";
          return `<div class="dl"><span>${esc(d.title)}</span><span class="when">${esc(when)}</span></div>`;
        })
        .join("")
    : `<p class="empty">Nothing tracked yet.</p>`;

  const feed = document.getElementById("feed");
  feed.innerHTML = s.activity?.length
    ? s.activity
        .slice(0, 12)
        .map((a) => `<div><b>${esc(a.agent)}</b> ${esc(a.action)} — ${esc(a.detail)}</div>`)
        .join("")
    : `<p class="empty">Quiet on the floor.</p>`;
}

let syncing = false;
async function runSync() {
  if (syncing) return;
  syncing = true;
  const dispatchBtn = $("#dispatch");
  dispatchBtn.disabled = true;
  const original = dispatchBtn.textContent;
  dispatchBtn.textContent = "Syncing…";
  try {
    const res = await fetch("/api/assistant/sync", { method: "POST" });
    const data = await res.json();
    (data.jobs || []).forEach((job, i) => setTimeout(() => dispatchJob(job), i * 450));
    await Promise.all([loadAppsData(), loadOverview()]);
    setTimeout(refreshSpace, 4000);
  } catch (e) {
    const mgr = agents.find((a) => a.role === "manager");
    if (mgr) { mgr.bubble = "Can't reach the apps!"; mgr.bubbleT = 4; }
  } finally {
    dispatchBtn.textContent = original;
    setTimeout(() => {
      dispatchBtn.disabled = false;
      syncing = false;
    }, 1500);
  }
}

$("#dispatch").addEventListener("click", runSync);
let autoTimer = null;
$("#auto").addEventListener("change", (e) => {
  if (e.target.checked) { runSync(); autoTimer = setInterval(runSync, 12000); }
  else clearInterval(autoTimer);
});

(async function initHQ() {
  fitCanvas();
  await loadRoster();
  await refreshSpace();
  requestAnimationFrame(loop);
})();

}  // end lounge-only guard (document.getElementById("stage"))
