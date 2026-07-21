// Assistant HQ — a little top-down lounge where the manager (Frank) dispatches
// worker agents to API "stations". Idle agents wander; working agents walk to
// their station, do the job, then walk back. All rendered on a canvas.

const WORLD = { w: 1000, h: 640 };
const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");

// Stations around the lounge. spot = where the worker stands to work.
const STATIONS = {
  desk:     { name: "Frank's Desk", color: "#f5c542", x: 500, y: 96,  spot: { x: 500, y: 170 } },
  gmail:    { name: "Gmail",        color: "#4aa3ff", x: 120, y: 150, spot: { x: 210, y: 190 } },
  schedule: { name: "Schedule",     color: "#7bd88f", x: 880, y: 150, spot: { x: 790, y: 190 } },
  powerbuy: { name: "PowerBuy",     color: "#ff8a5b", x: 120, y: 500, spot: { x: 210, y: 460 } },
  fitness:  { name: "Fitness",      color: "#c58cff", x: 880, y: 500, spot: { x: 790, y: 460 } },
};

// Cozy home spots around the central rug where idle workers hang out.
const HOME = [
  { x: 430, y: 400 }, { x: 570, y: 400 }, { x: 430, y: 470 }, { x: 570, y: 470 },
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
  };
}

async function loadRoster() {
  let roster;
  try {
    const r = await fetch("/api/assistant/agents");
    roster = (await r.json()).agents;
  } catch (e) {
    roster = [
      { id: "frank", name: "Frank", role: "manager", station: "desk", color: "#f5c542" },
      { id: "posty", name: "Posty", role: "worker", station: "gmail", color: "#4aa3ff" },
      { id: "cal", name: "Cal", role: "worker", station: "schedule", color: "#7bd88f" },
      { id: "rep", name: "Rep", role: "worker", station: "powerbuy", color: "#ff8a5b" },
      { id: "coach", name: "Coach", role: "worker", station: "fitness", color: "#c58cff" },
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
        `<span class="chip"><span class="dot" style="background:${a.color}"></span>${a.name}` +
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
  for (const a of agents) {
    if (a.bubbleT > 0) a.bubbleT -= dt;

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
  if (mgr) { mgr.bubble = "On it, team!"; mgr.bubbleT = 3; }
}

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
  // label
  ctx.fillStyle = "#e9e3f6";
  ctx.font = "600 14px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(s.name, s.x, s.y + 54);
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

// Manager keeps the round body + crown.
function drawManager(a, x, y) {
  ctx.fillStyle = a.color;
  roundRect(x - 12, y - 4, 24, 24, 9); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.18)";
  roundRect(x - 8, y - 2, 16, 8, 4); ctx.fill();
  ctx.fillStyle = "#f4d9b8";
  ctx.beginPath(); ctx.arc(x, y - 12, 9, 0, 7); ctx.fill();
  ctx.fillStyle = a.color;
  ctx.beginPath(); ctx.arc(x, y - 14, 9, Math.PI, 0); ctx.fill();
  ctx.fillStyle = "#2a2233";
  ctx.beginPath(); ctx.arc(x + a.facing * 2 - 3, y - 12, 1.5, 0, 7); ctx.fill();
  ctx.beginPath(); ctx.arc(x + a.facing * 2 + 3, y - 12, 1.5, 0, 7); ctx.fill();
  ctx.fillStyle = "#ffd85e";
  ctx.beginPath();
  ctx.moveTo(x - 7, y - 20); ctx.lineTo(x - 4, y - 27); ctx.lineTo(x, y - 21);
  ctx.lineTo(x + 4, y - 27); ctx.lineTo(x + 7, y - 20); ctx.closePath();
  ctx.fill();
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

  // blocky body
  ctx.fillStyle = NINJA_BODY;
  roundRect(x - 15, y - 10, 30, 30, 7); ctx.fill();

  // legs
  roundRect(x - 11, y + 18, 7, 6, 2); ctx.fill();
  roundRect(x + 4, y + 18, 7, 6, 2); ctx.fill();

  // bandana across the face (per-agent colour) with a little side knot
  ctx.fillStyle = a.color;
  roundRect(x - 16, y - 3, 32, 9, 2); ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x - dir * 15, y - 2); ctx.lineTo(x - dir * 24, y - 5);
  ctx.lineTo(x - dir * 22, y + 5); ctx.closePath(); ctx.fill();

  // big eyes below the bandana
  for (const ex of [-6, 6]) {
    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.ellipse(x + ex, y + 11, 4.6, 5.6, 0, 0, 7); ctx.fill();
    ctx.fillStyle = "#1b2730";
    ctx.beginPath(); ctx.arc(x + ex + dir * 1.4, y + 12, 2.1, 0, 7); ctx.fill();
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

function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

async function refreshSpace() {
  let s;
  try { s = await (await fetch("/api/assistant/space")).json(); } catch { return; }

  const notes = document.getElementById("notes");
  notes.innerHTML = s.memory?.length
    ? s.memory.map((m) => `<p class="note">📝 ${esc(m.content)}</p>`).join("")
    : `<p class="empty">No notes yet. Hit Dispatch.</p>`;

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

let dispatching = false;
async function dispatch() {
  if (dispatching) return;
  dispatching = true;
  const btn = document.getElementById("dispatch");
  btn.disabled = true;
  try {
    const res = await fetch("/api/assistant/sync", { method: "POST" });
    const data = await res.json();
    (data.jobs || []).forEach((job, i) => setTimeout(() => dispatchJob(job), i * 450));
    setTimeout(refreshSpace, 4000);
  } catch (e) {
    const mgr = agents.find((a) => a.role === "manager");
    if (mgr) { mgr.bubble = "Can't reach the apps!"; mgr.bubbleT = 4; }
  } finally {
    setTimeout(() => { btn.disabled = false; dispatching = false; }, 1500);
  }
}

document.getElementById("dispatch").addEventListener("click", dispatch);
let autoTimer = null;
document.getElementById("auto").addEventListener("change", (e) => {
  if (e.target.checked) { dispatch(); autoTimer = setInterval(dispatch, 12000); }
  else clearInterval(autoTimer);
});

(async function init() {
  fitCanvas();
  await loadRoster();
  await refreshSpace();
  requestAnimationFrame(loop);
})();
