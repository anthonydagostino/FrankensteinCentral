# FrankensteinCentral — Product Ideas for the Product Owner

**Status: recommendations only. Not a directive, not authorized scope.**

`STATE.json` reads `turn: product_owner` / `status: awaiting_directive`, so there
is no authorized task and no product code was touched to produce this. Under
`PROTOCOL.md` Claude "may recommend technical and product considerations —
recommending is not deciding." This file is that recommendation set. It does not
modify `PRODUCT_DIRECTIVE.md` or `STATE.json`, and it does not assume the PO will
accept any of it.

Each idea below is written so it can be lifted straight into a directive if you
want it: objective, requirements, and an acceptance signal.

---

## What this is based on

I read the `production` branch end to end — `gateway/` (registry, proxy,
`index.html`, `home.js`, `app.js`), all 15 registered services, `docker-compose.yml`,
`docs/AUDIT.md`, `docs/BUDGETS.md`, `PROTOCOL.md` — plus your Jira (`SCRUM`, 56
issues across 8 epics) and the shape of your inbox. The ideas are ranked by how
much they'd change your actual day, not by how interesting they are to build.

Two things I deliberately did **not** do: re-pitch what `AUDIT.md` already has
queued as Phase 2/3, and propose anything that needs a dependency you don't
already run.

---

# Tier 1 — the three that would change your day

## 1. Make the job hunt a real sub-app instead of an orphaned HTML file

**Problem.** `gateway/static/jobs.html` is a 554-line hand-maintained static page
with `contenteditable` lists. It is reachable from exactly one place: a link
inside `lounge.html`, the *legacy* dashboard you demoted. The command center —
the page you actually open — has no idea your job hunt exists. Meanwhile the
machinery is already built and running: the gmail service classifies
`category: "interview"`, `/thread-availability` scans your **sent** mail for
availability proposals and tracks confirm/counter/decline, and Cal writes
color-coded holds into your real Google Calendar. All of that lands in the Inbox
card as a generic email row and then evaporates.

**Proposal.** A `jobs` service (Postgres, same pattern as `tasks`/`finance`) that
owns an application pipeline, auto-fed rather than hand-typed:

- States: `applied → screen → technical → onsite → offer → closed`, plus
  `rejected` and `ghosted`.
- Rows are seeded from Gmail: an ATS acknowledgement ("Thank you for your
  interest in…") opens a row; an `interview`-category thread advances it; a
  rejection closes it. You correct what it gets wrong; you don't type the rest.
- **Gone-quiet detection** — the single highest-value bit. Any application with
  no inbound movement in N days (default 10) surfaces as an action, because the
  failure mode in a job hunt is silence, not rejection.
- Links each row to the Cal events Bones already books, so an interview on the
  calendar and the application it belongs to are the same object.
- A Pipeline card on the home screen: counts by stage, what's gone quiet, next
  interview.

Retire `jobs.html` into it — keep the pros/cons research notes as a per-company
notes field so nothing you wrote is lost.

**Why you specifically.** This is the highest-stakes thing in your life right
now and it is the one thing the dashboard is structurally blind to. It is also
cheap: ~80% of the data plumbing already exists in `gmail` and `schedule`.

**Effort:** M · **Depends on:** `gmail` (exists), `schedule` (exists), `db`
**Acceptance signal:** open the hub cold and see, without clicking anything, how
many applications are live and which one has gone quiet longest.

---

## 2. Let the dashboard watch the machine it lives on

**Problem.** `docs/AUDIT.md` §3 promises an IA branch called
`Home — services · infra health`. What shipped is a single footer string:
`● Systems healthy`, computed from `/health` on its own containers. It answers
"are my containers up" and nothing else.

Your Jira says the real risks are elsewhere, and the dashboard can see none of
them:

| Jira | Risk | Dashboard knows? |
|---|---|---|
| SCRUM-6 (epic) | "a self-hosted password vault exists on exactly one disk with no backup" | no |
| SCRUM-14 | T1 data not backed up to B2 | no |
| SCRUM-15 / 30 | restore has **never** been test-restored | no |
| SCRUM-16 | Docker log rotation broken, logs eating the disk | no |
| SCRUM-17 | unsure whether Watchtower / gravity-sync are even running | no |
| SCRUM-22 | containers still stranded on the 3040 Micro | no |

There is a `vault` service — it reports password *health* (weak, reused, no-2FA).
It does not report whether the vault still exists tomorrow. That's inverted: a
weak password is an annoyance, an unbacked-up vault on one disk is
unrecoverable.

**Proposal.** An `infra` service and one home-screen card. Not a Grafana — five
facts you'd actually act on:

1. **Disk free** on the OptiPlex, and the largest Docker log files (closes
   SCRUM-16 by making the damage visible instead of measured once by hand).
2. **SMART status** per drive.
3. **Last successful backup** — Backblaze B2 / Backrest run time and result.
4. **Last successful *restore test*, with the answer "never" rendered in red.**
   This is the card. A backup you have never restored is a belief, not a backup,
   and the number of days since the last verified restore is the one metric that
   makes you go fix it.
5. **Containers not running that should be** — a declared set, diffed against
   reality, so a silently dead container stops being invisible.

**Why you specifically.** You are running a homelab out of mismatched
hardware — a 2012 MacBook with no battery, a Kali Lenovo, a 3040 Micro, an
OptiPlex SFF — and about to move (the `Move Proofing` epic). Your own backlog
says the vault is your top unrecoverable risk. The dashboard runs on this
hardware and currently cannot tell you it's about to lose itself.

**Effort:** M · **Depends on:** host access from the container (read-only mounts /
a small agent), nothing external
**Acceptance signal:** the home screen states, in words, how many days since the
last verified restore — and it says "never" until one happens.

---

## 3. Do-Next is blind to money, bills, resale, and the job hunt

**Problem.** `_do_next()` in `services/assistant/app/main.py:392` is a
first-match-wins chain of six rules: imminent event → important email → soon
event → study pace → gym → Big 3 → water. It's well built and explainable. But
look at what can never win, no matter how bad it gets:

- a bill due tomorrow (`finance` has `upcoming_bills` — it renders in the Money
  card and is invisible to Do-Next)
- a blown budget (`budget.worst` — same: rendered, never actionable)
- an unpaid or expiring resale purchase (`powerbuy` exposes exactly these two
  alerts and nothing consumes them)
- an application that's gone quiet (idea #1)
- a failing backup (idea #2)

So on a day when rent is due, a buy is expiring, and you're 20 minutes behind on
study pace, the dashboard tells you to study. The money and resale signals are
*already computed* — they just have no path into the recommendation.

**Proposal.** Two changes:

1. Extend the chain with money/resale/job rules at the right severity. A bill due
   inside 48h and an expiring unpaid buy should outrank study pace; a mild budget
   overage should not.
2. Replace first-match-wins with a scored ranking, and show the reasoning. A
   small **"why this?"** affordance that expands to "beat: study (behind 22 min),
   gym (2/4 this week)". Right now the rule that fires is explainable but the
   rules that *didn't* are invisible, and that's what makes you distrust the
   recommendation.

**Why you specifically.** Your income is partly resale and your money layer is
the most carefully built part of this system (`docs/BUDGETS.md` has genuinely
good honesty rules — zero vs. unknown, never present a partial window as
complete). None of that rigor reaches the one line on the page that tells you
what to do.

**Effort:** S · **Depends on:** nothing new — all inputs are already fetched in
`build_home()`
**Acceptance signal:** a test where a bill is due in 24h and study is behind, and
the bill wins, with the study rule listed as beaten.

---

# Tier 2 — what makes it sticky

## 4. "Since you last checked" should follow you between machines

`renderSince()` in `home.js` stores its snapshot in `localStorage` under
`cc_snap`, and only fires after a 15-minute gap. On one machine it's a nice
touch. You have a 2019 MacBook as a daily driver, a 2012 MacBook always-on, a
Kali Lenovo, an OptiPlex, and a phone — so in practice every device tells you a
different story about what changed, and a new browser tells you nothing.

Move the snapshot server-side into `core` (a `seen` table keyed by nothing more
than a device id you don't even need to expose), and widen it beyond the current
5 fields to include the things you'd actually want to have missed: new interview
mail, a stage change in the pipeline, a failed backup, a bill that crossed into
its due window.

**Effort:** S · **Acceptance signal:** check the hub on your phone, then open it
on the MacBook — the second one doesn't re-report what the first already showed
you.

## 5. Weekly review is built, tested, and invisible

`core` exposes `GET /weekly-review` (`services/core/app/main.py:623`). Nothing in
`gateway/static/` references it — I grepped. It is a finished feature with no
front door.

Render it, and give it a moment: Sunday evening the home screen leads with the
week — study hours vs. goal, gym vs. goal, spend vs. budget, score trend,
applications moved. The daily score already tracks the inputs; the weekly view is
what turns a score into a habit. This is the cheapest win in the document.

**Effort:** XS · **Acceptance signal:** it's on the screen on a Sunday without
you navigating to it.

## 6. You have four task systems; pick one

Right now: Jira `SCRUM` (56 issues, 8 epics, where your real projects live), the
`tasks` service, `core`'s Big 3, and `core`'s quick-capture. Big 3 and capture
have distinct jobs — today's commitment, and a scratchpad — and should stay. But
`tasks` and Jira are the same tool twice, and the one you actually maintain is
Jira.

Two honest options:

- **Mirror.** `tasks` becomes a read-through view of Jira issues assigned to you,
  and capture gets a "promote to Jira" action. The dashboard stops being a second
  backlog and starts being the front door to the real one.
- **Retire.** Drop `tasks` from the registry, point the card at Jira.

Either is better than both. The failure mode of four inboxes is that you trust
none of them.

**Effort:** M (mirror) / XS (retire) · **Acceptance signal:** exactly one place
answers "what's open".

## 7. Put it on your phone properly

The responsive CSS is there (`home.css` breaks to one column at 900px, `.hx` at
560px), so this is *not* a "it's broken on mobile" complaint. What's missing is
everything that makes a phone use it: no `manifest.json`, no service worker, no
install-to-home-screen, no offline render of the last-known-good home payload, no
web push. Bones can text you via Telegram/Twilio, which is good, but a text is
not a dashboard.

A manifest + a service worker caching the last `/home` response would mean you
open it from your home screen, see yesterday's state instantly, and watch it
update — instead of a white page whenever you're off the LAN.

**Effort:** S · **Depends on:** HTTPS (Tailscale, already in your backlog at
SCRUM-48) · **Acceptance signal:** it launches from the home screen and renders
something useful with the box unreachable.

---

# Tier 3 — the compounding bets

## 8. Treat resale as the income stream it is

`docs/AUDIT.md` grades `powerbuy` "Low/niche". Your Jira disagrees: SCRUM-21 is
coconutBattery across **9 resale MacBooks**, SCRUM-20 is resolving failed Amazon
orders, and there's a whole `Resale Ops` epic. The service already exposes
expected profit, unpaid, not-delivered and expiring-soon and the home screen
shows none of it.

Give it cost basis and aging: capital tied up, margin per unit, days held, and
which units are aging past the point where they earn their shelf space. Battery
health per unit (you already collected it) is the natural grade field.

**Effort:** M · **Acceptance signal:** you can answer "how much cash is sitting
in unsold hardware" without opening a spreadsheet.

## 9. `/ask` should actually answer

`GET /ask` (`main.py:779`) is keyword intent-matching — `has("budget", "spending",
"spent", …)` — over a fan-out of every service. It's a sensible offline
fallback and it's honest about being one. `AUDIT.md` Phase 3 already wants a
JARVIS layer; here's the shape I'd argue for:

Keep the deterministic layer as **ground truth** and let a model do language
only. Every number in an answer must come from a service response, never from the
model; the model picks which services to read and phrases the result. If a
service is stale or down, the answer says so rather than smoothing over it —
which is the same rule `docs/BUDGETS.md` already applies to money (`null` is not
`0`, a partial window is not a complete one). That constraint is what would make
it trustworthy enough to use.

**Effort:** M · **Depends on:** an API key and an egress decision (a real one —
this is the first thing in the stack that would talk to the outside world on your
behalf)
**Acceptance signal:** ask it something whose data source is down, and it tells
you the source is down instead of guessing.

## 10. Turn the exam counter into a plan

`core`'s study block already computes `exam.days_left`, `remaining_hours` and
`weekly_needed_hours` — it knows you're behind before you do. But it stops at
telling you. Cal can write to your real Google Calendar. Close the loop: propose
study blocks in your actual free time, write them as holds, and let the focus
timer log against them. `weekly_needed_hours` becomes a schedule instead of a
number that makes you feel bad.

**Effort:** M · **Depends on:** `schedule` + gcal (both exist)

## 11. Decide about auth before the move, not after

`docs/AUDIT.md` states the gap plainly: "the dashboard itself has no auth (anyone
on the LAN can open it — acceptable for home, noted)". I confirmed it — there is
no auth anywhere in `gateway/app/`. That was a fair call when the page showed
container health.

It shows something else now: net worth, account balances, spending, password
health metadata, your inbox, and — if idea #1 lands — every company you're
talking to. Meanwhile Tailscale is on your backlog (SCRUM-48) and you're planning
a move, which means new networks and, at some point, a guest on one of them.

I'd pair single-user auth with binding the gateway to the Tailscale interface
rather than `0.0.0.0`. Not urgent this week; considerably worse to add after an
incident than before one.

**Effort:** S · **Acceptance signal:** the hub is unreachable from a device that
isn't yours, tested from one.

## 12. Fold recurring-charge detection into the money card

SCRUM-19 is "run `find_recurring.py` on 13 months of card CSVs" — a script you
run by hand, once, and whose output goes stale immediately. The `firefly` service
already holds every transaction, and `finance` already models bills and
subscriptions. Detect recurrence continuously instead: new subscription
appeared, a known one changed price, one you thought you cancelled charged again.

This also retires a manual Jira task by making it a property of the system.

**Effort:** S · **Depends on:** `firefly` (exists), `finance` (exists)

---

# Smaller things worth a line each

- **`networth` duplicates `firefly`.** `AUDIT.md` says so itself ("High (dup of
  firefly)"). Two cards that can disagree about your net worth is worse than one.
- **The registry is a hardcoded Python list.** Fine at 15 services; every new
  sub-app is a code change plus a compose change plus a renderer. Config-driven
  registration would make adding one a 2-minute job.
- **`deals` is inbox noise dressed as a feature.** Rated Low in the audit, and
  it competes for space with things that matter. Consider demoting it out of the
  registry rather than maintaining it.
- **The 15s proxy timeout in `gateway/app/main.py` is per-request**, but
  `build_home()` fans out to 15 services — worth confirming the home endpoint
  can't be held hostage by one slow service.
- **`AUTO_SYNC_SECONDS` defaults to `0`** (manual only). If auto-sync isn't on in
  your `.env`, every "since you last checked" and every notification depends on
  you opening the page — which defeats both.
- **Move Proofing has no representation.** You have an epic for it. A mode that
  tracks what's unplugged, what's re-racked, and what hasn't come back up would
  be genuinely useful for exactly one month of your life, which may or may not be
  worth building.

---

# If I had to pick three

**#2 (infra & restore-test), #1 (job pipeline), #3 (Do-Next widening)** — in that
order.

#2 because an unbacked-up password vault on a single disk is the only item here
that can cost you something you cannot get back, and your own backlog has been
saying so since the `Data Loss Prevention` epic was created.

#1 because it's the highest-stakes thing you're doing and most of it is already
built and wired to nothing.

#3 because it's small, it's pure logic, and it makes the other two show up where
you'll actually see them.

---

_Prepared by Claude (implementation engineer) under `PROTOCOL.md`. No product
code, `PRODUCT_DIRECTIVE.md`, or `STATE.json` was modified. The roadmap is the
Product Owner's._
