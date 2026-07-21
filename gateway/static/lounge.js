// Assistant HQ — a little top-down lounge where the manager (Bones) dispatches
// worker agents to API "stations". Idle agents wander; working agents walk to
// their station, do the job, then walk back. All rendered on a canvas.

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
  finance:  { name: "Finance",      color: "#5bd6c0", x: 500, y: 566, spot: { x: 500, y: 512 } },
  tasks:    { name: "Tasks",        color: "#f2b8d0", x: 120, y: 325, spot: { x: 210, y: 325 } },
  budget:   { name: "Budget",       color: "#f5c542", x: 880, y: 325, spot: { x: 790, y: 325 } },
};

// Cozy home spots around the central rug where idle workers hang out.
const HOME = [
  { x: 430, y: 390 }, { x: 570, y: 390 }, { x: 430, y: 450 }, { x: 570, y: 450 },
  { x: 500, y: 420 }, { x: 470, y: 420 }, { x: 530, y: 420 },
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
