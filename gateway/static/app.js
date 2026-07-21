const $ = (sel) => document.querySelector(sel);

function tickClock() {
  const el = $("#clock");
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

async function loadApps() {
  const [apps, health] = await Promise.all([
    fetch("/api/apps").then((r) => r.json()),
    fetch("/api/health").then((r) => r.json()).catch(() => ({})),
  ]);

  const grid = $("#apps");
  grid.innerHTML = "";
  for (const app of apps) {
    const h = health[app.key]?.status ?? "unknown";
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="card-top">
        <span class="card-icon">${app.icon}</span>
        <h3>${app.name}</h3>
      </div>
      <p>${app.description}</p>
      <span class="status"><span class="dot ${h}"></span>${h}</span>
    `;
    card.onclick = () => openApp(app);
    grid.appendChild(card);
  }
}

function renderBriefing(items) {
  const list = $("#briefing");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    list.innerHTML = `<li>Nothing needs you right now. Nice.</li>`;
    return;
  }
  for (const it of items) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="src">${it.source ?? "assistant"}</div>${it.message}`;
    list.appendChild(li);
  }
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
    { n: o.emails_to_reply ?? 0, l: "Emails to reply", cls: o.emails_to_reply ? "warn" : "" },
    { n: `$${o.expected_profit ?? 0}`, l: "Expected profit", cls: "good" },
    { n: o.unpaid ?? 0, l: "Unpaid buys", cls: o.unpaid ? "warn" : "" },
    { n: o.bills_due ?? 0, l: "Bills due soon", cls: o.bills_due ? "alert" : "" },
    { n: o.open_tasks ?? 0, l: "Open tasks", cls: o.open_tasks ? "warn" : "" },
    { n: `$${o.budget_left ?? 0}`, l: "Budget left", cls: o.budget_over ? "alert" : "good" },
    { n: o.today_focus ?? "Rest", l: "Today's workout", cls: "" },
    { n: evt, l: "Next up", cls: "" },
  ];
  el.innerHTML = tiles
    .map((t) => `<div class="ov ${t.cls}"><div class="n">${t.n}</div><div class="l">${t.l}</div></div>`)
    .join("");
}

async function loadBriefing() {
  const sub = $("#assistant-sub");
  try {
    const data = await fetch("/api/assistant/briefing").then((r) => r.json());
    renderBriefing(data.items);
    const base = data.summary ?? `${(data.items || []).length} things on your plate.`;
    sub.textContent = `${base} · synced ${timeAgo(data.synced_at)}`;
  } catch (e) {
    sub.textContent = "Assistant is offline.";
    renderBriefing([]);
  }
}

$("#sync-btn").onclick = async () => {
  const btn = $("#sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    await fetch("/api/assistant/sync", { method: "POST" });
    await Promise.all([loadBriefing(), loadApps(), loadOverview()]);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync now";
  }
};

// ---- app detail modal -------------------------------------------------------

const esc = (s) =>
  String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const api = (path, opts) => fetch(`/api${path}`, opts).then((r) => r.json());
const fmtDate = (s) => (s ? String(s).slice(0, 16).replace("T", " ") : "—");

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
  $("#modal-mode").textContent = mode ? `${mode} data` : "";
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
    body.innerHTML = `
      <h4>Needs a reply</h4>
      <div class="rows">${rows || '<p class="empty">Inbox is clear. 🎉</p>'}</div>`;
  },

  async schedule(app, body) {
    const data = await api("/schedule/events");
    setMode();
    const rows = (data.events || [])
      .map(
        (ev) => `<div class="row"><div class="grow"><b>${esc(ev.title)}</b>
          <div class="sub">from ${esc(ev.source || "manual")}</div></div>
          <span class="right">${esc(fmtDate(ev.starts_at))}</span></div>`
      )
      .join("");
    body.innerHTML = `
      <h4>Upcoming</h4>
      <div class="rows">${rows || '<p class="empty">No events yet.</p>'}</div>
      <h4>Add an event</h4>
      <div class="inline-form">
        <input id="ev-title" placeholder="Title (e.g. Dentist)" />
        <input id="ev-when" type="datetime-local" />
        <button class="btn" id="ev-add">Add</button>
      </div>`;
    $("#ev-add").onclick = async () => {
      const title = $("#ev-title").value.trim();
      const when = $("#ev-when").value;
      if (!title || !when) return;
      await api("/schedule/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, starts_at: when, source: "manual" }),
      });
      openApp(app);
    };
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
          <div class="grow" style="${tk.done ? "opacity:.5;text-decoration:line-through" : ""}">${esc(tk.title)}</div></div>`
      )
      .join("");
    body.innerHTML = `
      <h4>To-do${data.count ? ` (${data.count})` : ""}</h4>
      <div class="rows" id="task-rows">${rows || '<p class="empty">Nothing to do. 🎉</p>'}</div>
      <p class="empty" style="margin-top:8px">Tap a task to toggle done.</p>
      <h4>Add a task</h4>
      <div class="inline-form">
        <input id="task-title" placeholder="What needs doing?" />
        <button class="btn" id="task-add">Add</button>
      </div>`;
    body.querySelectorAll("#task-rows .row").forEach((r) => {
      r.onclick = async () => {
        await api(`/tasks/tasks/${r.dataset.id}/toggle`, { method: "POST" });
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

  async assistant(app, body) {
    const data = await api("/assistant/briefing");
    setMode();
    const rows = (data.items || [])
      .map(
        (it) => `<div class="row"><span class="chip">${esc(it.source || "assistant")}</span>
          <div class="grow">${esc(it.message)}</div></div>`
      )
      .join("");
    body.innerHTML = `
      <p>${esc(data.summary || "")}</p>
      <a href="/lounge.html" class="btn" style="display:inline-block;text-decoration:none;margin:4px 0 8px">🎮 Open Assistant HQ</a>
      <h4>Current briefing</h4>
      <div class="rows">${rows || '<p class="empty">Nothing yet — hit Sync.</p>'}</div>`;
  },
};

async function renderGeneric(app, body) {
  setMode();
  body.innerHTML = `<p>${esc(app.description)}</p>`;
}

async function askAssistant() {
  const input = $("#ask-input");
  const out = $("#ask-answer");
  const q = input.value.trim();
  if (!q) return;
  out.textContent = "Thinking…";
  try {
    const data = await api(`/assistant/ask?q=${encodeURIComponent(q)}`);
    out.textContent = data.answer || "Not sure about that one.";
  } catch {
    out.textContent = "Couldn't reach the assistant.";
  }
}
$("#ask-btn").onclick = askAssistant;
$("#ask-input").addEventListener("keydown", (e) => e.key === "Enter" && askAssistant());

loadApps();
loadBriefing();
loadOverview();

// Keep the hub live — reflects the assistant's own auto-sync without a refresh.
setInterval(() => {
  if (!$("#overlay").classList.contains("open")) {
    loadBriefing();
    loadOverview();
  }
}, 20000);
