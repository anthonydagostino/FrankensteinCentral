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

# Wave 2 — from a full read of the service code

Wave 1 came from the architecture and your backlog. This wave comes from reading
`core`, `assistant`, `gmail`, `fitness`, `budget`, `firefly` and `app.js` line by
line. It's less about new features and more about the gap between what this
system already computes and what it actually tells you — which turns out to be
large, and cheap to close.

---

## Group A — four places the dashboard states something it doesn't know

These are grouped because they're one idea wearing four hats: **the honesty rules
in `docs/BUDGETS.md` are excellent and they stop at the money layer.** "Zero and
unknown are different states" is true of far more than spending.

### 13. "Not configured" and "unreachable" are rendered as the same thing

`_get()` (`assistant/main.py:190`) swallows every exception and returns `{}` —
"a down sub-app just contributes nothing". Then:

```python
"portfolio": stocks or {"configured": False},                        # :713
connected = bool(firefly) and firefly.get("connected") is not False  # :562
```

So when `stocks` times out, the home screen tells you **"No holdings yet. Add
your stocks →"**. When `firefly` is briefly unreachable, it tells you
**"Firefly not connected — set FIREFLY_URL/FIREFLY_TOKEN"**. Both are
instructions to go fix configuration that is already correct. A transient blip
reads as a setup error, and if you act on it you'll go looking for a problem
that doesn't exist.

The right pattern is already in this repo — `gmail` is the one service that
gets it right, with `mode: disconnected | live | error` and
`sync_status: healthy | failed | never`, and a UI that distinguishes "not
connected yet" from "last refresh failed, showing last known good".

**Proposal.** Make that contract universal: `_get` returns a tagged result
(`ok` / `unreachable` / `error`) instead of `{}`, and every card renders three
states rather than two. "Couldn't reach Firefly — showing the last figures I
had, from 14:05" is a true sentence. The current one isn't.

**Effort:** S · **Acceptance signal:** stop the `stocks` container and confirm
the portfolio card says it's unreachable, not that you have no holdings.

### 14. `● Systems healthy` is computed from 2 of your 15 services

```python
down = [name for name, payload in (("core", core), ("email", emails_r)) if not payload]  # :693
```

The footer's health claim only ever looks at `core` and `gmail`. `firefly`,
`budget`, `schedule`, `stocks`, `finance`, `tasks`, `networth`, `vault`,
`deals`, `plex` and `powerbuy` can all be down and it will still say
**"● Systems healthy"** — while the cards above it quietly render idea #13's
"not configured" messages.

The gateway already has the real thing: `GET /api/health` probes all 15
concurrently and returns per-service status *and* each one's `/health` detail
payload — which the UI then throws away entirely.

**Proposal.** Feed the real aggregate into the footer, and let it expand into a
per-service list. This also becomes the natural home for idea #2's infra card.

**Effort:** XS · **Acceptance signal:** a stopped container makes the footer say
so by name.

### 15. The clock seam stops at the money layer, and it's costing you gym credit

`docs/TESTING.md` is one of the better documents in this repo. It describes a
real incident ("at 01:37 UTC it was September in the container and still August
in New York"), states the rule — **"One clock. All date logic goes through
`_today()`. A test asserts no bare `date.today()` / `datetime.now()` call
reappears elsewhere in the module"** — and backs it with a 1,166-case calendar
sweep in `firefly`.

That discipline was applied to three services. Only `gmail`, `budget` and
`firefly` have tests at all. `core`, `assistant` and `fitness` have none — and
those are the three that decide what the dashboard tells you to do.

Here's the concrete cost. `fitness` stores visits as **naive UTC**:

```python
when_at = (visit.when or datetime.utcnow()).isoformat()   # fitness/main.py:85
```

`core._gym()` reads that back through `_parse_day()` and buckets it with
`_week_start()` (Monday). A naive string has no offset, so the date taken is the
**UTC** date. In New York that means:

- a workout logged after **8pm EDT** is credited to **tomorrow**;
- a workout logged **Sunday evening** is credited to **next week**.

That feeds the `fitness` score component (weight 20, the second-heaviest) and
the gym rule in `_do_next`. So on a Sunday night, after you've been to the gym,
the dashboard can tell you to go to the gym — and dock your score for not
having gone.

**Proposal.** Store tz-aware timestamps, give `fitness`/`core`/`assistant` the
same `_today()` seam `firefly` has, and port the calendar-sweep test pattern.
The rule already exists and is written down; it just hasn't reached the services
that own your habits.

**Effort:** S (fix) + M (test coverage) · **Acceptance signal:** a visit logged
at 9pm local counts for today, asserted on every day of a two-year sweep.

### 16. The daily score treats "you didn't set a goal" as "you failed"

`compute_score()` (`core/main.py`) says in its own docstring: *"`null`
components are treated as not-yet."* The code immediately below does:

```python
ratio = 0.0 if ratio is None else max(0.0, min(1.0, float(ratio)))
```

`null` becomes `0.0` — scored as a total miss. So on a day you never set a Big 3,
`big3_total` is 0, the `tasks` component is 0, and you lose its full 20 points —
indistinguishable from setting three items and doing none of them. Same for
nutrition before you've rated the day: you're marked as eating badly until you
say otherwise, which is exactly the "`$0` vs unknown" error `BUDGETS.md` forbids
one service over.

The mechanism to fix it already exists — `compute_score` drops zero-weight
components and renormalises to 100. Unset components should drop out the same
way.

Two smaller things in the same function while you're in there: the component
named **`tasks` is actually Big 3** (`comp["tasks"] = big3_done / big3_total`),
and the genuine open-task count is fetched from the tasks service on every
request and then never scored or shown. One of those should change name; the
other should change purpose.

**Proposal.** Unset ⇒ excluded and renormalised, and the score displays what it
was out of ("74, from 4 of 5 tracked"). A score you can't trust is a score you
stop looking at, and this is the number sitting in your header all day.

**Effort:** S · **Acceptance signal:** a fresh day with nothing logged reads as
"nothing tracked yet", not as a low score.

---

## Group B — things that are built, tested, running, and connected to nothing

### 17. The unwired inventory

I went looking for one of these and found nine. Every row is code that exists
and executes, whose output no user can reach:

| what | where | status |
|---|---|---|
| `_nudges()` — severity-tagged attention feed with actions | `core/main.py:~355` | computed on every `/today`; the string "nudges" appears nowhere in `assistant` or the frontend |
| `deadlines` table — interviews, deadline emails, bills | `assistant`, written on every sync | readable only via `/space`, i.e. **only on the legacy lounge** |
| `GET /weekly-review` | `core/main.py:623` | zero frontend references |
| Sleep — column, model, `POST /sleep`, score component | `core` | score weight defaults to **0**, no UI control anywhere |
| `market.move_threshold_pct` | settable in ⚙ Settings, saved to `core` | read by nothing — "Alert on move ≥ 3%" produces no alert |
| `finance.low_balance` | `DEFAULT_SETTINGS` | consumed nowhere |
| `captures.kind` (+ `CapturePatch.kind`) | `core` schema | UI only ever writes `'note'` |
| `focus_sessions.label` | `core` schema | UI only ever writes `'Study'` |
| `jobs.html` | `gateway/static/` | linked only from the legacy lounge (wave 1, #1) |

`core._nudges()` is the painful one. `AUDIT.md` §3 promised a "unified **Needs
Attention** feed (severity Important/FYI)". It was built — with per-item icons,
severity, detail lines and typed actions — and the home screen renders a single
`do_next` instead. You have the feed. It's just never asked for.

The `deadlines` one is close behind: the assistant extracts interview times and
bill due dates on every sync and files them, and the only page that can display
them is the canvas dashboard you deliberately demoted.

**Proposal.** One directive that closes all nine, plus the thing that stops it
recurring: **a test that fails when a `DEFAULT_SETTINGS` key or a service
endpoint has no consumer.** It's a grep-level check and it would have caught
every row in this table. A settings field that silently does nothing is worse
than a missing one — you configure it, you believe it's on, and you stop
watching for the thing it was supposed to catch.

**Effort:** M for all nine, XS each · **Acceptance signal:** the consumer test
is green, and turning on the Big 3 nudge makes it appear.

### 18. Pending calendar holds — the entire point of the Gmail→Bones→Cal
pipeline — are filtered off the home screen

```python
events = [e for e in schedule.get("events", []) if e.get("status", "confirmed") == "confirmed"]  # :696
```

Your README describes the pipeline's whole value as the **pending** state: 🟡
proposed by you, 🟠 they countered, 🟢 confirmed. `next_event`, the "Head to X"
rule and the "Get ready for X" rule in `_do_next` all read this filtered list, so
they only ever see 🟢.

The result: Bones scans your sent mail, finds the three interview slots you
offered, writes three colour-coded holds into your real Google Calendar — and the
dashboard shows you none of them and never mentions them. The most sophisticated
thing this system does is invisible on its own home screen.

**Proposal.** Surface pending distinctly: "3 slots offered to EliseAI, awaiting
reply" and "they countered — Thursday 2pm needs your yes". Pending-awaiting-you
should be able to win Do-Next; pending-awaiting-them should not.

**Effort:** S · **Depends on:** nothing — the data is already in the payload
being discarded

### 19. `build_home` never fetches tasks or PowerBuy

The fan-out at `:676` gathers 14 endpoints. `TASKS_URL` and `POWERBUY_URL` are
both configured in `docker-compose.yml`, both used elsewhere in the service, and
neither is in the list. So the home screen is structurally incapable of showing
an open task or an expiring unpaid resale buy — which is the mechanical reason
wave 1's #3 and #8 exist. Worth stating separately because it's a two-line fix
that unblocks both.

**Effort:** XS

---

## Group C — new capability worth building

### 20. Cash runway — the one number your situation actually calls for

You are job hunting, your income includes resale, and you have a full ledger
(`firefly`), account balances (`networth`), bills (`finance`) and budgets
(`budget`) already wired into the same aggregator. Nothing computes the number
that combines them: **liquid balances ÷ trailing average monthly burn = months of
runway.**

Every other money figure on this dashboard is a rear-view mirror — what you spent,
what's due, what a category has left. Runway is the only forward-looking one, and
it's the one that changes decisions: whether to take the contract, how hard to
push on offers, whether the NAS purchase (SCRUM-32/54) waits a month.

Do it with the honesty rules already established: runway is `null` when the
ledger is stale, never an optimistic number; it shows the burn window it used;
resale proceeds are separated from salary so you can see runway with and without
them.

**Effort:** S — pure composition over data already in `build_home`
**Acceptance signal:** one number, with its inputs visible, that goes `null`
rather than lying when the ledger hasn't been imported.

### 21. Evening mode should be a different screen, not a different colour

`AUDIT.md` Phase 1 promises "new Today homepage with **morning/evening modes**".
What shipped: `assistant` computes `mode` (morning/day/evening), `home.js` sets
`document.body.setAttribute("data-mode", …)`, and `home.css:25` has exactly one
rule keyed off it. The content is identical at 7am and 11pm — the palette shifts.

The card order and the calls to action are what should change:

- **Morning** — lead with the day: next event, Big 3 entry, what's due, the one
  thing to do first.
- **Evening** — lead with the close-out: log sleep (idea #17 makes it loggable),
  rate nutrition, tick or roll over the Big 3, set tomorrow's, and on Sunday lead
  with the weekly review that already exists.

This is the cheapest way to make the dashboard something you open *twice* a day
instead of once, and it closes an AUDIT promise that's currently only cosmetic.

**Effort:** S · **Depends on:** #17 (sleep control, weekly review)

### 22. Let Bones take input by text, not just answer questions

`_telegram_listen_loop()` is already running: long-polling, owner-only (`chat_id
!= TELEGRAM_CHAT_ID` is dropped), no inbound firewall exposure, answering through
the same `/ask` the web box uses. That's a finished two-way channel.

It is read-only. You can ask Bones what's due; you can't tell it anything.

**Proposal.** Accept the same verbs the ⌘K palette already implements —
`gym`, `water 24`, `study 45`, `capture <text>`, `big3 done 2`. The command
parser exists in `home.js`; the transport exists in `assistant`. This matters
specifically because your logging moments (leaving the gym, drinking water, an
idea on the train) are exactly the moments you are not in front of the OptiPlex —
and a habit tracker you can only reach from one desk is a habit tracker you'll
abandon.

**Effort:** S · **Depends on:** nothing new
**Acceptance signal:** text "gym" from outside the house, watch the score move.

### 23. Where the week actually went

`focus_sessions` already has a `label` column; the UI hardcodes `"Study"`. Let
the focus timer take a label — Study / Applications / Resale / FrankensteinCentral
— and the weekly review can answer a question nothing currently can: *where did
my hours go?* For someone splitting time between an exam, a job hunt, a resale
operation and building this, that breakdown is more actionable than the total.

**Effort:** XS · **Depends on:** #17 (weekly review wired)

### 24. Show the dashboard its own deploy state

You have a full deployment protocol — `production` branch, `promote.sh`,
`autopull.sh`, a test gate that keeps the previous build running on failure, and
`~/.frankenstein/deployed.json` recording `running_commit`, `last_attempt_commit`,
`last_result` and `last_success_at`. All of it is only visible by SSH-ing in and
running `frankenstein-status.sh`.

The dashboard should show its own version: what commit is running, when it
deployed, whether the last attempt failed and left you on an older build. That
last case is the one that matters — a failed deploy is currently silent from
the UI, so the box can sit on a stale build for days while you assume your fix
is live.

Pairs naturally with idea #14's expanded systems footer.

**Effort:** XS · **Note:** read-only display; no promotion controls in the UI —
`promote.sh` should stay the only path, per `PROTOCOL.md`.

### 25. Streaks for more than study

`_study()` computes a consecutive-day streak, and it's the only one. Gym weeks
hit, days the score cleared a threshold, water goal met — same query shape, and
streaks are the single cheapest retention mechanic there is. "Best week ever" and
"you've hit your gym goal 4 weeks running" are the sentences that make you not
want to break the chain.

**Effort:** XS · **Depends on:** #15 (otherwise evening workouts silently break
the chain, which is worse than having no streak)

### 26. Turn a booked interview into a prepared one

When Cal books an interview, the system knows the company, the thread, and the
time. `jobs.html` holds your research on those companies. Nothing joins them.

Surface, on the event and the day before: your notes on that company, the
original JD from the thread, who you've spoken to, and what you asked last time.
And — draft-only — a follow-up email for an application that's gone quiet
(wave 1, #1). `PROTOCOL.md` lists **sending email** as a high-risk action
requiring explicit approval, and it's right to; a draft sitting in Gmail waiting
for you to hit send is not that, and it removes the part you actually procrastinate
on.

**Effort:** M · **Depends on:** wave 1 #1

---

# If I had to pick five

In order:

1. **#2 — infra & restore test.** Still first. It's the only item across both
   waves that protects something you can't get back, and your own `Data Loss
   Prevention` epic has been saying so since you created it.
2. **#13–16 as one correctness pass.** The dashboard currently tells you your
   holdings aren't set up when a container blinked, calls 15 services healthy
   after checking 2, credits Sunday-night workouts to next week, and scores an
   unset goal as a failed one. Everything else in this document is worth less
   while the numbers on the screen can't be taken at face value — and each fix
   is small.
3. **#17 — the unwired inventory.** Nine features you already paid for,
   including the attention feed `AUDIT.md` promised and the deadlines the
   assistant files on every sync. Highest ratio of value to new code in the
   document, and the consumer test stops the pattern coming back.
4. **#1 — the job pipeline.** Highest stakes, ~80% already built, and #18 and
   #26 fall out of it nearly free.
5. **#20 — cash runway.** One number, composed entirely from data already in
   `build_home`, and the only forward-looking figure on the whole dashboard.

A reasonable first directive is #2 or the #13–16 pass. #13–16 is the safer
opening move under the protocol: contained, testable, no new services, and it
makes every later idea land on a screen you trust.

---

_Prepared by Claude (implementation engineer) under `PROTOCOL.md`. No product
code, `PRODUCT_DIRECTIVE.md`, or `STATE.json` was modified. The roadmap is the
Product Owner's._
