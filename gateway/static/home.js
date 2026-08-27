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
    // score pill
    const sc = (d.score && d.score.score) || 0;
    q("#cc-score-n").textContent = sc;
    // briefing
    q("#cc-briefing").innerHTML = (d.briefing || [])
      .map((b) => `<span class="cc-chip">${esch(b)}</span>`).join("");
    renderDoNext(d.do_next, d);
    renderScore(d.score);
    renderBig3(d.big3);
    renderAttention(d.attention);
    renderMoney(d.money);
    renderPortfolio(d.portfolio);
    renderHealth(d.health);
    renderCapture(d.captures);
    // footer
    q("#cc-updated").textContent = "Updated " + new Date(d.last_updated || Date.now()).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    const sys = d.systems || { healthy: true, down: [] };
    q("#cc-systems").textContent = sys.healthy ? "● Systems healthy" : "⚠ " + (sys.down || []).join(", ") + " down";
    q("#cc-systems").style.color = sys.healthy ? "var(--muted)" : "var(--imp)";
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

  function renderScore(score) {
    score = score || { score: 0, parts: {} };
    const parts = score.parts || {};
    const bars = Object.keys(parts).map((k) => {
      const p = Math.round((parts[k].ratio || 0) * 100);
      return `<div class="score-bar"><span>${esch(k)}</span><div class="track"><div class="fill" style="width:${p}%"></div></div></div>`;
    }).join("");
    q("#cc-score").innerHTML = `
      <h3>Today's score</h3>
      <div class="score-ring" style="--p:${score.score || 0}"><div class="hole">${score.score || 0}</div></div>
      <div class="score-bars">${bars || '<span class="att-empty">No components tracked.</span>'}</div>`;
  }

  function renderBig3(items) {
    items = items || [];
    let inner;
    if (!items.length) {
      inner = `<div class="big3-empty">
        <input class="cc-input" id="big3-input" placeholder="Your 3 wins for today, comma-separated" />
        <button class="hx-btn" id="big3-save">Set</button></div>`;
    } else {
      inner = items.map((b) =>
        `<div class="big3-item ${b.done ? "done" : ""}" data-id="${b.id}">
          <div class="big3-box">${b.done ? "✓" : ""}</div><div class="t">${esch(b.text)}</div></div>`).join("");
    }
    q("#cc-big3").innerHTML = `<h3>Your Big 3</h3>${inner}`;
    if (!items.length) {
      q("#big3-save").onclick = async () => {
        const v = q("#big3-input").value.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 3);
        if (!v.length) return;
        await post("/core/big3", { items: v }); refresh(true);
      };
    } else {
      q("#cc-big3").querySelectorAll(".big3-item").forEach((el) =>
        (el.onclick = async () => { await post("/core/big3/" + el.dataset.id + "/toggle", {}); refresh(true); }));
    }
  }

  function renderAttention(items) {
    items = items || [];
    const rows = items.map((it) => {
      const act = it.action ? `<button class="att-act" data-id="${esch(it.id)}">${esch(actionLabel(it.action))}</button>` : "";
      return `<div class="att-item ${it.severity}">
        <span class="att-dot"></span><span class="att-ic">${it.icon || "•"}</span>
        <div class="att-main"><div class="t">${esch(it.title)}</div><div class="d">${esch(it.detail || "")}</div></div>
        ${act}</div>`;
    }).join("");
    q("#cc-attention").innerHTML = `<h3>Needs attention</h3>
      <div class="att">${rows || '<div class="att-empty">Nothing needs you right now. 🎉</div>'}</div>`;
    q("#cc-attention").querySelectorAll(".att-act").forEach((b) => {
      const it = items.find((x) => x.id === b.dataset.id);
      b.onclick = () => handleAction(it.action);
    });
  }

  function renderMoney(m) {
    m = m || {};
    const cats = (m.top_categories || []);
    const max = Math.max(1, ...cats.map((c) => c.amount || 0));
    const catRows = cats.map((c) =>
      `<div class="cat"><span>${esch(c.name)}</span><div class="track"><div class="fill" style="width:${Math.round((c.amount / max) * 100)}%"></div></div><span class="mono">${money(c.amount)}</span></div>`).join("");
    const bills = (m.upcoming_bills || []).map((b) =>
      `<div class="pos"><span>${esch(b.name)}${b.days_until != null ? ` · ${b.days_until}d` : ""}</span><span class="mono">${money(b.amount, 2)}</span></div>`).join("");
    const obs = (m.observations || []).map((o) => `<li>${esch(o)}</li>`).join("");
    q("#cc-money").innerHTML = `
      <h3>Money</h3>
      <div class="mny-hero">
        <div class="mny-stat"><div class="v">${m.today_spent != null ? money(m.today_spent) : "—"}</div><div class="l">Spent today</div></div>
        <div class="mny-stat"><div class="v">${m.net_worth || "—"}</div><div class="l">Net worth</div></div>
        <div class="mny-stat"><div class="v mono">${m.left_to_spend || "—"}</div><div class="l">Left this month</div></div>
      </div>
      ${obs ? `<ul class="mny-obs">${obs}</ul>` : ""}
      ${catRows ? `<div class="cats">${catRows}</div>` : ""}
      ${bills ? `<h3 style="margin-top:14px">Upcoming bills</h3>${bills}` : ""}
      ${!m.connected ? '<p class="att-empty" style="margin-top:8px">Connect Firefly to see live spending.</p>' : ""}`;
  }

  function renderPortfolio(p) {
    p = p || { configured: false };
    if (!p.configured) {
      q("#cc-portfolio").innerHTML = `<h3>Portfolio</h3>
        <p class="att-empty">No holdings yet. Add them with <b>⌘K → set stocks</b> (or Core settings) to track value & daily movers.</p>`;
      return;
    }
    const dc = p.day_change || 0, cls = dc >= 0 ? "up" : "down", arrow = dc >= 0 ? "▲" : "▼";
    const mv = p.movers || {};
    const moverRow = (x, label) => x ? `<div class="pos"><span>${label} <b>${esch(x.symbol)}</b> <span class="${x.change_pct >= 0 ? "up" : "down"}">${x.change_pct >= 0 ? "+" : ""}${x.change_pct}%</span></span><span class="mono ${x.day_change >= 0 ? "up" : "down"}">${x.day_change >= 0 ? "+" : ""}${money(x.day_change)}</span></div>` : "";
    const positions = (p.positions || []).filter((x) => x.available).map((x) =>
      `<div class="pos"><span><b>${esch(x.symbol)}</b> <span class="${x.change_pct >= 0 ? "up" : "down"}">${x.change_pct >= 0 ? "+" : ""}${x.change_pct}%</span></span><span class="mono">${money(x.value)}</span></div>`).join("");
    q("#cc-portfolio").innerHTML = `
      <h3>Portfolio</h3>
      <div class="mny-hero">
        <div class="mny-stat"><div class="v mono">${money(p.value)}</div><div class="l">Value</div></div>
        <div class="mny-stat"><div class="v mono ${cls}">${arrow} ${money(Math.abs(dc))} (${p.day_change_pct}%)</div><div class="l">Today</div></div>
        ${p.total_gain != null ? `<div class="mny-stat"><div class="v mono ${p.total_gain >= 0 ? "up" : "down"}">${p.total_gain >= 0 ? "+" : ""}${money(p.total_gain)}</div><div class="l">Total gain</div></div>` : ""}
      </div>
      ${moverRow(mv.up, "Top")}${moverRow(mv.down, "Worst")}
      ${positions}`;
  }

  function renderHealth(h) {
    h = h || {};
    const st = h.study || {}, gym = h.gym || {}, w = h.water || {}, nut = h.nutrition || {};
    const studyPct = st.goal_min ? Math.min(100, Math.round((st.today_min / st.goal_min) * 100)) : 0;
    const waterPct = w.goal ? Math.min(100, Math.round((w.oz / w.goal) * 100)) : 0;
    const fmtH = (m) => `${Math.floor((m || 0) / 60)}h ${(m || 0) % 60}m`;
    const focusBtns = (st.presets || [25, 45, 60]).map((m) => `<button class="hx-btn" data-focus="${m}">${m}m</button>`).join("");
    const waterBtns = (w.presets || [8, 16, 24]).map((oz) => `<button class="hx-btn" data-water="${oz}">+${oz}</button>`).join("");
    const rating = nut.rating;
    const nutBtns = ["poor", "okay", "good"].map((r) => `<button class="hx-btn ${rating === r ? "on" : ""}" data-nut="${r}">${r[0].toUpperCase() + r.slice(1)}</button>`).join("");
    const exam = st.exam;
    q("#cc-health").innerHTML = `
      <h3>Health &amp; discipline</h3>
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
    if (app && typeof openApp === "function") openApp(app);
  }
  async function handleAction(a) {
    if (!a) return;
    if (a.type === "focus") return startFocus(a.minutes || 45, a.label || "Study");
    if (a.type === "water") { await post("/core/water", { oz: a.oz || 16 }); toast(`+${a.oz || 16} oz`); return refresh(true); }
    if (a.type === "gym") { await post("/core/gym", {}); toast("Workout logged 💪"); return refresh(true); }
    if (a.type === "big3") { await post("/core/big3/" + a.id + "/toggle", {}); return refresh(true); }
    if (a.type === "gmail") return openAppKey("gmail");
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
      { ic: "🔄", label: "Refresh dashboard", hint: "sync", run: () => refresh(true) },
      { ic: "🦴", label: "Open Bones' lounge", hint: "fun", run: () => (location.href = "/lounge.html") },
    ];
    // one command per app -> opens its modal
    Object.values(APPSMAP).forEach((a) => base.push({ ic: a.icon || "▦", label: "Open " + a.name, hint: a.key, run: () => openAppKey(a.key) }));
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
  q("#cc-apps").onclick = openPalette;
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
  });

  // ---- boot -----------------------------------------------------------------
  setInterval(paintClock, 1000);
  (async function boot() {
    await loadApps();
    await refresh(true);
    // background refresh every 60s (skip while a modal/palette/focus is open)
    setInterval(() => {
      if (q("#overlay").classList.contains("open")) return;
      if (!q("#palette").hidden || !q("#focus").hidden) return;
      refresh(false);
    }, 60000);
  })();
})();
