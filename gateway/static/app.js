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

async function loadBriefing() {
  const sub = $("#assistant-sub");
  try {
    const data = await fetch("/api/assistant/briefing").then((r) => r.json());
    renderBriefing(data.items);
    sub.textContent = data.summary ?? `${(data.items || []).length} things on your plate.`;
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
    await loadBriefing();
    await loadApps();
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

loadApps();
loadBriefing();
