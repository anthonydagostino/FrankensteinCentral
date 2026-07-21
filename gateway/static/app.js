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
    card.onclick = () => (window.location.href = `/api/${app.key}/`);
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

loadApps();
loadBriefing();
