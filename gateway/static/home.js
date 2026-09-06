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
    q("#cc-score-n").textContent = (d.score && d.score.score) || 0;
    q("#cc-briefing").innerHTML = (d.briefing || [])
      .map((b) => `<span class="cc-chip">${esch(b)}</span>`).join("");
    renderSince(d);
    renderCalendar(d);
    renderDoNext(d.do_next, d);
    renderInbox(d.inbox);
    renderMoney(d.money, d.budget);
    renderPortfolio(d.portfolio);
    renderToday(d);
    renderHealth(d);
    renderCapture(d.captures);
    q("#cc-updated").textContent = "Updated " + new Date(d.last_updated || Date.now()).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    const sys = d.systems || { healthy: true, down: [] };
    q("#cc-systems").textContent = sys.healthy ? "● Systems healthy" : "⚠ " + (sys.down || []).join(", ") + " down";
    q("#cc-systems").style.color = sys.healthy ? "var(--muted)" : "var(--imp)";
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
  function evWhen(raw) {
    if (!raw) return "";
    const d = new Date(String(raw).replace(" ", "T"));
    if (isNaN(d.getTime())) return String(raw);
    const now = new Date();
    const tmr = new Date(now); tmr.setDate(now.getDate() + 1);
    const t = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "Today " + t;
    if (d.toDateString() === tmr.toDateString()) return "Tomorrow " + t;
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + " " + t;
  }
  function renderCalendar(d) {
    const items = d.calendar || [];
    if (!items.length) {
      q("#cc-calendar").innerHTML = `<h3>Schedule</h3><p class="att-empty">Nothing coming up.</p>`;
      return;
    }
    const rows = items.map((e) => {
      const st = CAL_STATUS[e.status] || CAL_STATUS.confirmed;
      const needsYou = e.status === "countered";
      const tag = (e.status && e.status !== "confirmed")
        ? `<span class="cal-tag">${esch(st.label)}</span>` : "";
      return `<div class="cal-row${needsYou ? " needs-you" : ""}">
        <span class="cal-dot" title="${esch(st.label)}">${st.dot}</span>
        <span class="cal-t mono">${esch(evWhen(e.starts_at))}</span>
        <span class="cal-title">${esch(e.title || "Untitled")}</span>${tag}
      </div>`;
    }).join("");
    q("#cc-calendar").innerHTML = `<h3>Schedule</h3><div class="cal-rows">${rows}</div>
      <div class="hx-btns" style="margin-top:10px"><button class="hx-btn" id="cal-open">Open schedule →</button></div>`;
    const b = q("#cal-open");
    if (b) b.onclick = () => openAppKey("schedule");
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
        ? `<p class="mny-stale">📅 Financial data hasn't been imported for <b>${m.stale_days} days</b> — current-period figures unavailable</p>` : "");

    // Trailing 30 days is a rolling window; the budgets below are calendar
    // months. The labels say so explicitly so the two can't be conflated.
    const trend30 = (m.last_30_trend_pct != null)
      ? `<br><span class="mny-trend ${m.last_30_trend_pct <= 0 ? "up" : "down"}">${m.last_30_trend_pct <= 0 ? "↓" : "↑"} ${Math.abs(m.last_30_trend_pct)}% vs previous 30 days</span>`
      : "";
    // Never present a partial window as complete.
    const through30 = (m.last_30 != null && m.last_30_through && m.stale_days)
      ? `<br><span style="font-size:10px">through ${esch(m.last_30_through)} only</span>` : "";

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
    const safe = (bud.fresh && bud.budget_room != null)
      ? `<div class="mny-stat"><div class="v mono up">${money(bud.budget_room)}</div><div class="l">Budget room<br><span style="font-size:10px">remaining ${esch(bud.budget_room_scope || "across active budgets")}</span></div></div>` : "";
    const bills = (m.upcoming_bills || []).slice(0, 2).map((b) =>
      `<div class="pos"><span>${esch(b.name)}${b.days_until != null ? ` · ${b.days_until}d` : ""}</span><span class="mono">${money(b.amount, 2)}</span></div>`).join("");
    const obs = (m.observations || []).slice(0, 2).map((o) => `<li>${esch(o)}</li>`).join("");

    // The Firefly sub-app's headline figures, here so they cost no clicks.
    // A missing value renders as an em dash, never as $0 — unknown is not zero.
    const ffTiles = [
      ["Net worth", m.net_worth],
      ["Earned (mo)", m.earned],
      ["Spent (mo)", m.spent],
      ["Left to spend", m.left_to_spend],
    ].map(([l, v]) => `<div class="mny-stat"><div class="v mono">${v ? esch(v) : "—"}</div><div class="l">${esch(l)}</div></div>`).join("");

    // Spending by category, last 30 days — the sub-app's donut as bars, which
    // read better at this width and stay legible in both themes.
    const cats = (m.categories || []).slice(0, 6);
    const catMax = cats.reduce((mx, c) => Math.max(mx, Number(c.amount) || 0), 0);
    const catRows = cats.map((c) => {
      const amt = Number(c.amount) || 0;
      const pct = catMax > 0 ? Math.round((amt / catMax) * 100) : 0;
      return `<div class="cat-row"><span class="cat-n">${esch(c.name)}</span>` +
        `<span class="cat-bar"><i style="width:${pct}%"></i></span>` +
        `<span class="cat-v mono">${money(amt)}</span></div>`;
    }).join("");

    const accts = (m.accounts || []).map((a) =>
      `<div class="pos"><span>${esch(a.name)}</span><span class="mono">${a.balance != null ? money(a.balance, 2) : "—"}</span></div>`).join("");
    q("#cc-money").innerHTML = `
      <h3>Money</h3>
      ${staleLine}
      <div class="mny-hero">
        <div class="mny-stat"><div class="v">${m.last_30 != null ? money(m.last_30) : "—"}</div><div class="l">Past 30 days${trend30}${through30}</div></div>
        <div class="mny-stat"><div class="v mono">${m.today != null ? money(m.today) : "—"}</div><div class="l">Today</div></div>
        ${safe}
      </div>
      ${budLine}
      <div class="hx-btns" style="margin:10px 0 4px"><button class="hx-btn" id="money-budget">View budget →</button></div>
      ${obs ? `<ul class="mny-obs">${obs}</ul>` : ""}
      <div class="mny-hero mny-ff">${ffTiles}</div>
      ${catRows ? `<h3 style="margin-top:12px">Spending by category<span class="mny-sub"> · last 30 days</span></h3><div class="cat-rows">${catRows}</div>` : ""}
      ${accts ? `<h3 style="margin-top:12px">Accounts</h3>${accts}` : ""}
      ${bills ? `<h3 style="margin-top:12px">Upcoming bills</h3>${bills}` : ""}
      <div class="hx-btns" style="margin-top:10px"><button class="hx-btn" id="money-firefly">Recent transactions →</button></div>`;
    const ffBtn = q("#money-firefly");
    if (ffBtn) ffBtn.onclick = () => openAppKey("firefly");
    q("#money-budget").onclick = () => openAppKey("budget");
    const setup = q("#bud-setup");
    if (setup) setup.onclick = () => openSettings();
  }

  function renderPortfolio(p) {
    p = p || { configured: false };
    if (!p.configured) {
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
        <div class="mny-stat"><div class="v mono ${cls}">${arrow} ${p.day_change_pct}%</div><div class="l">Today · ${dc >= 0 ? "+" : ""}${money(dc)}</div></div>
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

    const patch = {
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
    // background refresh every 60s (skip while a modal/palette/focus is open)
    setInterval(() => {
      if (q("#overlay").classList.contains("open")) return;
      if (!q("#palette").hidden || !q("#focus").hidden) return;
      refresh(false);
    }, 60000);
  })();
})();
