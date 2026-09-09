# FrankensteinCentral — Product Ideas for the Product Owner

**Status: a backlog of findings, not a plan of record.** Anthony decides what
gets built and in what order; nothing here is scheduled by virtue of being
written down.

*Updated 2026-09-08:* the acceptance and deployment-authorization gates were
removed on 2026-09-07, so the earlier framing of this file — "recommendations
only, awaiting a directive" — no longer describes anything real. `STATE.json`,
`PRODUCT_DIRECTIVE.md` and the turn-taking protocol are gone. What has not
changed is that each entry below is a finding with evidence, and picking one up
is a decision, not a formality.

Every idea is written so it can be worked directly: the problem with file and
line references, a proposal, an effort estimate, and an acceptance signal.

---

## Tracked in Jira

All 37 ideas are mirrored into the `SCRUM` project, labelled **`fc-ideas`**
(plus `dashboard`, a `correctness` label on the defects, and an `effort-*`
label). Five new epics, three existing ones reused:

| epic | ideas | tickets |
|---|---|---|
| **SCRUM-57** Dashboard: trust the numbers | 13, 14, 15, 16, 33 | SCRUM-58, 63–66 |
| **SCRUM-59** Close the loop on what's already built | 17, 18, 19, 24 + smaller items | SCRUM-69–73 |
| **SCRUM-60** Dashboard: job hunt | 1, 26, 27, 28, 29, 30, 31, 32 | SCRUM-74–81 |
| **SCRUM-61** Daily use and attention | 3, 4, 6, 7, 21, 22, 23, 25, 34, 35, 36 | SCRUM-82–92 |
| **SCRUM-62** Money and intelligence | 9, 10, 12, 20 | SCRUM-93–96 |
| **SCRUM-6** Data Loss Prevention *(existing)* | 2, 37 | SCRUM-67, 68 |
| **SCRUM-11** Resale Ops *(existing)* | 8 | SCRUM-97 |
| **SCRUM-8** Network and DNS *(existing)* | 11 | SCRUM-98 |

Idea #5 (the unrendered weekly review) is a row inside SCRUM-69 rather than its
own ticket. Nine of the 37 are filed as **Bug** rather than Story — the four
correctness items, plus #19, #28, #30, #32 and #33 — because they are defects
against behaviour the code already intends, not new scope.

`labels = "fc-ideas"` selects the whole set if you want to bulk-triage or bulk-
delete it.

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

# Wave 3 — the job hunt, the calendar, and one label that overstates its data

This wave came from reading `gmail/main.py`, `schedule/`, `stocks/` and
`orchestrator.py`. It has a blunt headline: **the part of this system that
handles the most important thing in your life right now is the least
intelligent part of it.** Everything else — the money layer, the budget engine,
the date-window tests — is carefully built. The job-hunt path is two regexes.

---

## Group D — your job hunt runs on a two-keyword regex

### 27. One regex gates the entire inbound booking path

Here is the whole definition of what counts as job correspondence:

```python
_INTERVIEW = re.compile(r"\binterview\b|phone screen|onsite interview", re.I)
```

And here is the gate on Bones booking anything into your calendar:

```python
for e in mails:
    if e.get("category") != "interview":
        continue                                   # assistant/main.py:1039
```

So the sentence *"Are you free Thursday at 2pm for a quick chat about the
Founding Engineer role?"* classifies as **`personal`**. It is never tagged as
an interview, never gets priority 3, never books a hold, and never files a
deadline — because it doesn't contain the literal word "interview".

None of these do either: *chat, call, screen, next steps, availability,
recruiter, hiring manager, take-home, coding challenge, assessment, HackerRank,
CodeSignal, offer, references, background check, technical round, final round.*
That's most of a real hiring pipeline.

**Proposal.** Replace the keyword test with a **job-stage classifier**:
`outreach → screen → assessment → onsite → offer → rejection → ack`. It's still
deterministic and testable — the same style as `_FINANCIAL` and `_HUMAN_ASK`,
which are both much more carefully built than `_INTERVIEW` — and it's the
natural feed for the pipeline in wave 1 #1. The classifier is the hard part of
that idea, and it belongs here regardless.

**Effort:** S · **Acceptance signal:** a corpus of your own last 30 job emails,
each asserted to the right stage, run as a test.

### 28. LinkedIn is blanket-blocked, so real recruiter contact scores zero

```python
_JOB_BOARD = re.compile(r"joinhandshake\.com|linkedin\.com|indeed\.com|…")
if _JOB_BOARD.search(sender):
    return {**msg, "category": "fyi", "needs_reply": False, "priority": 0, "automated": True}
```

The comment above it is right about the problem — bulk "you might like this
job" mail that fakes deadline language. But the rule is **domain-wide and
returns early**, before any other classification runs. A recruiter's InMail
notification, an interview request routed through LinkedIn, a hiring manager
replying to you there — all forced to `fyi`, priority 0, `needs_reply: False`,
`automated: True`. Bottom of the card, invisible to Do-Next, unbookable.

For someone actively job hunting, LinkedIn is not a noise domain; it's a
channel with a high noise ratio. Those are different problems.

**Proposal.** Keep the early-return for the genuine alert senders
(`jobalerts-noreply@linkedin.com`, `jobs-listings@linkedin.com` and friends) and
let the rest through to normal triage. Match on the sending address, not the
domain.

**Effort:** XS · **Acceptance signal:** a LinkedIn InMail notification lands as
`interview`/`personal` with `needs_reply` true; a LinkedIn job alert still lands
as `fyi`.

### 29. There is no outcome axis, so a rejection reads as personal mail

The classifier has categories for *what a message is about* and nothing for
*what it means for an application*. An ATS acknowledgement and an ATS rejection
are the same shape of text, and both fall through to `personal` at priority 2 —
above your finance mail, your bills and your deals, as though a human wrote to
you personally.

Your Disney rejection is the worked example: subject *"Thank You for Your
Interest — Product Software Engineer I"*, body *"we appreciate you spending some
of it applying to the role of…"*. No "interview", so not `interview`. No
financial or transactional language. No `_HUMAN_ASK` match, no trailing "?". It
lands as `personal`, priority 2, and sits near the top of your inbox card for
seven days.

A rejection is one of the few emails that should **lower** the attention it
demands, because it resolves something. Under wave 1 #1 it should close the
pipeline row and disappear.

**Effort:** XS on top of #27 · **Acceptance signal:** a rejection classifies as
a rejection, sorts below unresolved mail, and closes its application.

### 30. Twenty-five messages, silently

```python
params={"q": INBOX_QUERY, "maxResults": 25},   # gmail/main.py:421
params={"q": SENT_QUERY,  "maxResults": 25},   # gmail/main.py:598
```

No `nextPageToken` handling anywhere in the service, and no flag telling the UI
the result was capped. The sent-mail scan is the binding one: `SENT_QUERY` is
`in:sent newer_than:21d`, and during an active job hunt you will send more than
25 emails in three weeks. When you do, the **oldest** threads fall off — which
are exactly the ones whose availability proposals have been sitting unanswered
longest, and the ones the gone-quiet detector most needs.

This is the same error `docs/BUDGETS.md` forbids in the money layer, one service
over: *never present a partial window as complete.* The inbox card says "3 need
a reply" with no hint that it only looked at 25 messages.

**Proposal.** Paginate, or at minimum return `truncated: true` with the count
and render it. A number you know might be wrong is useful; a number you think is
complete is not.

**Effort:** S · **Acceptance signal:** with 40 sent messages in the window, the
availability tracker sees all 40, or the UI says it didn't.

### 31. The inbox card's entire world is seven days

`INBOX_QUERY` defaults to `category:primary newer_than:7d -from:me`. That's a
sane default for triage and a bad one for a job hunt, because the single most
important thread is the one that has been silent for **more than** seven days —
and on day 8 it simply stops existing as far as the dashboard is concerned.

The `deadlines` table would survive this (it's persistent), but it's unwired
(wave 2 #17), and the pipeline that would survive it doesn't exist yet (wave 1
#1). So today, silence is indistinguishable from resolution.

**Proposal.** Keep the 7-day window for the *triage feed* and give the job
pipeline its own persistent state, seeded from a wider query. Freshness and
memory are different jobs and shouldn't share a window.

**Effort:** S · **Depends on:** wave 1 #1

---

## Group E — the calendar can actually hurt you

### 32. Bones writes to your real Google Calendar with no overlap check and no end time

I grepped `services/schedule/` for `conflict`, `overlap`, `duration`, `travel`
and `reminder`. Zero hits, in `main.py` and `gcal.py` both.

The `events` table *has* an `ends_at` column and the `Event` model *has* an
optional `ends_at` field — but the booking path never sets it:

```python
json={"title": f"Interview — {e['from']}", "starts_at": when,
      "source": "gmail", "external_id": ext_id, "thread_id": thread_key}
```

So every automatically booked interview is a zero-duration point with no end,
and nothing anywhere checks whether it lands on top of something else. Bones
will cheerfully write a 🟡 hold for Thursday 2pm into your real calendar when
you already have a confirmed interview at Thursday 2pm, and neither the
dashboard nor Google will say a word.

Multiply that by the several companies wave 1 #1 assumes you're talking to, and
the failure mode isn't cosmetic — it's double-booking two interviews and finding
out live.

**Proposal.** Populate `ends_at` with a sensible default (45 or 60 minutes,
configurable), detect overlaps before writing, and surface collisions on the
pending card: *"this slot collides with your confirmed 233 Analytics screen."*
Don't refuse the write — you may genuinely want to offer overlapping slots to
different companies — but never write one silently.

**Effort:** S · **Acceptance signal:** two overlapping holds produce a visible
collision warning, asserted in a test.

---

## Group F — one more label that overstates what it knows

### 33. "Portfolio · what changed · Today" is end-of-day data

Both quote sources are daily bars:

```python
url = f"{STOOQ_BASE}/q/d/l/?s={_norm(symbol)}&i=d"        # i=d — daily
params={"range": "2d", "interval": "1d"}                   # Yahoo fallback
```

`_stooq_quote` takes the **last two rows of the daily CSV** and calls the
difference `change` / `change_pct`. The home screen renders that as
**`▲ 1.4%` / `Today · +$212`** under the heading *"Portfolio · what changed"*.

During market hours that number is generally the **previous completed session**,
not today. On a Saturday it's Friday-vs-Thursday, labelled "Today". Over a long
weekend it's three days stale, labelled "Today". And no as-of date is returned
by the service or shown by the card, so there's no way to tell from the screen
which it is.

This is the third instance of the same pattern (with #13 and #30) and the reason
I keep flagging it: the money layer already knows how to do this right, and the
discipline hasn't propagated. `docs/BUDGETS.md` would not permit this label.

**Proposal.** Return an `as_of` date from the quote source and render it —
*"Last session (Fri close) · +1.4%"*. If you want a genuinely intraday figure
that's a different (keyed) data source and a separate decision; the labelling fix
is worth doing either way and costs almost nothing.

**Effort:** XS · **Acceptance signal:** open the dashboard on a Sunday and the
portfolio card says which session it's showing.

---

## Group G — capability that isn't there at all

### 34. Nothing can be dismissed, snoozed, or ignored

I grepped the whole product for `snooze`, `dismiss`, `ignore` and `mute`. Every
hit is `re.IGNORECASE`. There is no way to tell this system "not now" or "not
ever" about anything.

Consequences you'll have felt: an email you've consciously decided not to answer
stays `needs_reply` at the top of the card for seven days. Do-Next recomputes
from scratch on every load with no memory of what it already told you or what
you already did about it, so it can re-suggest the thing you just handled, and
flip between two recommendations as the clock crosses `evening_start_hour`. The
"Since you last checked" bar is the only dismissible thing on the page, and its
dismissal doesn't survive a refresh.

An attention system that can't be told "handled" trains you to stop reading it.
That's the mechanism by which dashboards die, and it's the one thing `AUDIT.md`
§2 — an otherwise sharp diagnosis of why the old homepage wasn't sticky —
doesn't mention.

**Proposal.** A `dismissals` table in `core`: key, scope (`today` / `until` /
`forever`), reason, timestamp. Every attention item and every Do-Next
recommendation carries a stable key and a snooze affordance. Dismissals are
data — the weekly review can tell you what you keep snoozing, which is usually
the most honest signal in the whole system.

**Effort:** S · **Acceptance signal:** snooze the top item, refresh, and it's
still gone.

### 35. Kiosk mode — you already own the hardware and you're buying it stands

Your backlog has a 2012 MacBook Pro and a Kali Lenovo being set up as
**always-on nodes** (SCRUM-24, SCRUM-25), laptop stands for both (SCRUM-28), and
clamshell mode with the lid shut (SCRUM-44, SCRUM-48). You are, in other words,
assembling always-on screens.

Nothing in `gateway/static/` serves them. The command center assumes a person
sitting in front of it: hover states, a command palette, click-to-open modals,
30-second cache, small type.

**Proposal.** A `/wall` view. Big type, no interaction, high contrast, auto-refresh,
and only what's worth glancing at from across the room: next event, what needs a
reply, today's score, days-since-last-restore-test (idea #2), spend vs. budget,
and whether anything is down. It's a second renderer over the `/home` payload
that already exists — no new backend at all.

This is the idea most specific to you in the whole document. Most people don't
have spare always-on machines and stands on order.

**Effort:** S · **Depends on:** `/home` (exists)

### 36. iOS Shortcuts as the phone path

Wave 1 #7 argued for a PWA. This is the cheaper, complementary half. You're on
iOS; the logging verbs are all single POSTs that already exist
(`/core/water`, `/core/gym`, `/core/focus`, `/core/capture`, `/core/big3`).

A published Shortcut per verb gives you *"Hey Siri, log gym"*, a home-screen
widget, and a share-sheet capture — with no service worker, no HTTPS
requirement beyond Tailscale, and no new code beyond a documented endpoint list.
Same reasoning as #22 (Telegram input): the moments you need to log something
are the moments you're not at the OptiPlex.

**Effort:** XS (docs + a Shortcut) · **Depends on:** Tailscale (SCRUM-48)

### 37. The life-OS's own database is not in the backup plan

Wave 1 #2 was about the dashboard *reporting* backup state. This is narrower and
more embarrassing: the `db_data` and `gmail_token` volumes hold your gym history,
every focus session, Big 3 history, captures, calendar events, net-worth
accounts, budget definitions and **the Gmail refresh token** — and they live on
the same single OptiPlex covered by the same `Data Loss Prevention` epic that has
never had a restore test.

`docker compose down -v` is documented in your own README as the way to start
clean. There is no documented way to take a backup first.

You already have SCRUM-33, *"[CLAUDE] Write the database dump script"*, sitting in
the backlog. My argument is that it isn't homelab housekeeping — it's a product
requirement. The thing that makes a life OS worth using is that it accumulates,
and everything it has accumulated is currently one disk away from zero.

**Proposal.** A `pg_dump` on a timer into the same B2 bucket as everything else,
the token volume included, and the restore path written down and **tested once**.
Then surface the result on the infra card from #2.

**Effort:** S · **Acceptance signal:** a restore into a scratch database that
comes back with your gym history intact — performed once, then dated on the
dashboard.

---

## Smaller things from this pass

- **`extract_datetime` has no year and no timezone.** `orchestrator.py` parses
  "Thursday at 2pm" out of email text; worth confirming what it does in late
  December and what timezone it assumes, given the interview it books goes into
  your real calendar.
- **No quiet hours on notifications.** With `AUTO_SYNC_SECONDS` set and
  `NOTIFY_ON_SYNC=true`, a notable event at 3am texts you at 3am.
- **`AUTO_SYNC_SECONDS=0` ships in `.env.example`.** Every proactive behaviour in
  the system — digests, auto-sync, "since you last checked" — is off by default,
  which means the dashboard only knows things when you're already looking at it.
- **`_HOME_TTL = 30s` with no background refresh** means the first load after a
  gap is always the slow one, fanning out to 14 services.
- **`activity` logs what the agents did, never what you did.** For the weekly
  review, the second half is the interesting one.

---

# Wave 4 — the charts don't answer the question you have while looking at them

Prompted by the PO: *"when I hover over certain aspects of the pie chart it shows
more information."* Right instinct, and the code makes the case better than the
suggestion does — because the pattern being asked for **already exists one card
over**, and because the donut has two defects underneath the missing hover that
are worth more than the hover itself.

## 38. The spending donut has no interaction layer, and the pattern is already in the repo

**Problem.** `spendingDonut()` (`gateway/static/app.js:122`) renders each
category as a bare path:

```js
return `<path d="${path}" fill="${DONUT_COLORS[i % DONUT_COLORS.length]}" fill-rule="evenodd"></path>`;
```

(`app.js:145`.) No `<title>`, no `data-*`, no hover state, no focus handler, no
cursor change.
The slices are decoration. The legend beside them gives name, percentage and
dollar amount — so hovering could never tell you *less*, but it also can't tell
you the thing you actually want, which is **what's inside the slice**.

An HTML chart is interactive by default; the hover layer is part of the
deliverable, not an upgrade. The only form that legitimately skips it is a bare
stat tile with no plot — which correctly describes the score ring
(`home.css:132`, a `conic-gradient` meter) and the `.hx-track` habit bars. Those
are fine as they are. The donut is not.

**And the answer is already written.** The budget card does exactly what's being
asked for:

```html
<div class="vcard st-${b.state}" data-bid="${b.id}" title="Click for transactions">
  …
  <div class="bdetail" hidden> … per-budget transactions … </div>
```

Budget "vessels" carry a hover hint, a stable id, and a hidden drill-down
holding the real transactions. The donut needs the same treatment, and `firefly`
already holds the transactions to fill it.

**Proposal.**

1. **Hover and focus on every slice** — category, amount, share of total, and
   the same detail on keyboard focus, not hover alone. The mark is the hit
   target; the hovered slice lifts (a slight lighten or a surface ring) so it's
   visibly responding. Value leads, category name follows.
2. **Click drills down** to that category's transactions for the window, the way
   `.bdetail` already does for budgets.
This got more important on 2026-09-07: production `e7adf83` ("Money card: the
real pie chart, not bars") promoted this same donut onto the **home screen's
Money card** (`home.js:399`), so it is no longer tucked inside a modal — it is
one of the first things on the page, and still inert.

3. **Open up "Other".** Everything past the top 8 rolls into one slice with no
   way to expand it — and that is precisely where unexamined spending hides.
   Make it clickable into the full list.
4. **Insert category names with `textContent`, not string concatenation.**
   Category names come from Firefly, i.e. from your own transaction
   descriptions — untrusted input into DOM.
5. **Fewer segments.** Part-to-whole at a glance holds to about six; past
   roughly seven colour classes adjacent categories blur regardless of palette.
   Consider top-5-plus-Other in the donut and the full breakdown in the
   drill-down table.
6. **A table view.** Nothing a tooltip shows should be reachable only by
   hovering — the legend covers this partly today; a table makes it complete
   and gives the chart an accessible fallback (the `<svg>` currently has no
   `role` or `aria-label`).

**Why it matters here.** "$412 on Groceries" is a number you can't act on. "$412
on Groceries, and $180 of it was one Costco run on the 14th" is. The dashboard
already has both halves and doesn't join them.

**Effort:** S · **Depends on:** `firefly` transactions (exists), the `.bdetail`
pattern (exists)

**Acceptance signal:** hover any slice and see what it is; click it and see what
made it up; open "Other" and find nothing hidden.

## 39. The donut's colours fail a colour-blindness check, and change meaning between visits

Two separate defects in one line of code. Both are computable rather than
matters of taste, so I computed them.

**39a — colour is assigned by rank, not by category.**

```js
const sorted = [...items].sort((a, b) => b.amount - a.amount);
…
fill="${DONUT_COLORS[i % DONUT_COLORS.length]}"
```

The index is the category's **position in this render**, so if Groceries
overtakes Rent between two visits, the two swap colours. The rule this breaks is
a hard one: colour follows the entity, never its rank — a change in the data
must not repaint the survivors. Today the chart quietly tells you something
untrue about identity every time your spending order shifts, which is the whole
point of a categorical palette.

Fix: hash or map the category name to a fixed slot, so a category keeps its
colour for as long as it exists.

**39b — the palette fails validation on the dark surface.**

I ran the ten `DONUT_COLORS` through a palette validator against the panel
background (`#161a23`):

```
[FAIL] Lightness band     9 of 10 outside the band
[FAIL] Chroma floor       #f2b8d0 → 0.073 (reads gray)
[FAIL] CVD separation     worst adjacent #c58cff ↔ #4aa3ff  ΔE 1.9 (protan)
[PASS] Normal-vision floor  worst adjacent ΔE 15.3
[PASS] Contrast vs surface  all 10 ≥ 3:1
```

The one that matters: **`#c58cff` (purple) and `#4aa3ff` (blue) are ΔE 1.9 apart
under protanopia** — effectively the same colour — and they sit at adjacent
indices, so they are *always* neighbouring slices once you have five or more
categories. Normal-vision separation is 15.3, barely over the floor of 15, so
this is marginal even with full colour vision.

Fix: re-step the palette against the dark surface and re-run the validator until
the checks pass, rather than eyeballing replacements. Secondary encoding —
direct labels and the 2px surface gap between slices — is what makes a
borderline pair legal, and the donut currently has neither.

**Effort:** XS (colour stability) + S (palette re-step and validation)

**Acceptance signal:** the validator passes on the dark surface, and a category
keeps its colour across a month where its rank changes.

---

# Wave 5 — auditing the code that shipped this week

Most of waves 1–3 landed while this document was being written: the unreachable
/ not-configured split, the systems footer, the clock seam, the score, the
as-of label, the nudges feed, the deadlines table, the weekly review, sleep,
snooze, deploy state, recurring detection, and a `tests/test_no_orphans.py`
guard for the unwired-feature pattern. The new modules are good work —
`donut.js`, `evcolor.js`, `weekclock.js` and `health.js` are pure, unit-tested,
and honest in their docstrings about what they can and can't prove.

So this wave audits **the new code**, which nobody has reviewed yet. Four of the
six findings are in files that did not exist a week ago.

## 40. Generated event hues have no minimum separation — two events in a week can be the same colour

`evcolor.js` hashes an event title to a hue:

```js
var HUE_FLOOR = 25, HUE_SPAN = 320;
return "hsl(" + hueOf(event.title || "untitled") + ", 62%, 68%)";
```

The reasoning in its docstring is right on every point it addresses — status
beats identity, red is reserved for overlaps, fixed saturation and lightness so
"no event shouts over its neighbour purely because of where it landed on the
wheel". But it is silent on the one property a categorical palette exists to
provide: that two entries are **telling apart**. A hash over a continuous hue
range guarantees nothing about the gap between the handful of values you
actually get.

Ten realistic titles for one of your weeks:

```
Standup                   hue  61   #dee07b
Grocery run               hue  78   #c2e07b
Call with Kieran          hue  80   #bee07b
Dentist                   hue  89   #afe07b
Laundry                   hue 117   #80e07b
Gym                       hue 203   #7bb9e0
Study block               hue 290   #cf7be0
Interview — EliseAI       hue 295   #d87be0
RHCSA practice            hue 312   #e07bcc
Interview — 233 Analytics hue 323   #e07bb9
```

Six of the nine adjacent gaps are under 25°. **"Grocery run" and "Call with
Kieran" are 2° apart.** Through the validator against the dark panel:

```
[FAIL] Lightness band        all 10 outside the band
[FAIL] Chroma floor          #7bb9e0 reads gray
[FAIL] CVD separation        #bee07b ↔ #c2e07b  ΔE 0.1 (protan)
[FAIL] Normal-vision floor   #bee07b ↔ #c2e07b  ΔE 0.5 — below 15, hard to
                             tell apart even with full colour vision
```

ΔE 0.5 on the **normal-vision** check is worse than the donut's problem, which
was CVD-only. This one is broken for everybody.

And it is worse in a second way: it is **data-dependent**. Whether your calendar
is legible this week depends on the words you typed into it. It will come and
go without any code change, and it cannot be reproduced from the source.

**Proposal.** Keep the two good rules — status wins, red reserved — and stop
generating hues. Hash the title to an **index into a fixed, validated palette**
instead of to a continuous hue. That preserves everything the file set out to
do (stable per title, never positional) and adds the guarantee it is missing,
because the palette's pairs were checked once, in advance.

**Effort:** S · **Acceptance signal:** the validator passes on the set of
colours the week grid can actually emit — which becomes a finite set you can
test.

## 41. The donut still has the exact bug the calendar just fixed

`evcolor.js` states the rule explicitly:

> A hue is derived from the title, never from the row's position — so a
> recurring commitment is the same colour every week, and reordering a day
> never repaints it.

`donut.js:69`, in the same repo, in the same week:

```js
var sorted = items.slice().sort(function (a, b) { return b.amount - a.amount; });
…
color: COLORS[i % COLORS.length],
```

`i` is still the index in the amount-sorted array, so categories still swap
colours when spending order changes. And `COLORS` is byte-identical to the old
`DONUT_COLORS`, so the CVD failure stands too: `#c58cff` ↔ `#4aa3ff`, ΔE 1.9
under protanopia, at adjacent indices.

SCRUM-99 (hover) shipped and shipped well. **SCRUM-100 did not ship at all**,
and it is the half that was mislabelling the data. The fix is now nearly free:
the stable-hash-to-identity pattern is already written and unit-tested one file
over.

**Effort:** XS · **Depends on:** #40's fixed palette, which both files should
share

## 42. Three colour systems, no shared contract, and a guard test that would be cheap

Colour decisions now live in at least three places that don't know about each
other: `donut.js` (a fixed list, indexed by rank), `evcolor.js` (generated HSL),
and the CSS tokens (`--accent`, `--imp`, `--up`, `--down`) plus the budget
vessel states. Nothing reconciles them, and the reserved-red convention that
`evcolor.js` documents is enforced only inside `evcolor.js`.

The team has already established the right idiom for this. `tests/test_no_orphans.py`
exists because "who consumes this?" was expensive to answer a year later, and
its docstring says the point is "to make the question one you answer when it is
cheap." The same argument applies exactly to colour, and the machinery is
already in place: `scripts/test.sh` runs `node --test gateway/tests/*.test.js`,
and there are five such files already.

**Proposal.** One palette module both charts import, and a
`gateway/tests/palette.test.js` that runs the six checks — lightness band,
chroma floor, CVD separation, normal-vision floor, contrast — over every
categorical palette in the repo, and fails the build. Then a bad colour is a red
suite, not something someone eventually notices.

**Effort:** S · **Acceptance signal:** introducing a failing pair turns the suite
red.

## 43. A module that fails to load is silent in one place, loud in another, and fatal in four

Seven scripts now load as plain globals in sequence, with no `defer` and no
integrity check:

```html
<script src="/weekclock.js"></script>  <script src="/evcolor.js"></script>
<script src="/health.js"></script>     <script src="/deploy.js"></script>
<script src="/donut.js"></script>      <script src="/app.js"></script>
<script src="/home.js"></script>
```

Three different behaviours if one doesn't arrive:

- **Silent.** `spendingDonut()` returns `""` when `Donut` is undefined, and
  `DONUT_COLORS` falls back to `[]`. The money card renders with no chart and no
  explanation — indistinguishable from having no spending data, which is the
  precise `docs/BUDGETS.md` failure this project has spent weeks eliminating
  everywhere else.
- **Loud.** `home.js:1157` checks `typeof openApp !== "function"` and says
  *"Stale page detected — hard-refresh (Ctrl+Shift+R)"*. Exactly right.
- **Fatal.** `EventColor`, `WeekClock`, `SystemsHealth` and `Deploy` are used
  unguarded. A missing one throws a `ReferenceError` mid-render, and since the
  cards are painted in one pass, everything after the throw simply never
  appears — a blank dashboard with an error only in the console.

The `Cache-Control: no-cache` middleware exists precisely because browsers were
serving week-old JS across deploys, so a partially-updated page is a failure
mode this codebase has already met once.

**Proposal.** A tiny registry: after load, assert every expected global is
present, and show one honest banner naming what's missing. Wrap each card's
render in a try/catch so one bad card degrades to "this card failed" instead of
taking the page with it. The loud message already exists — generalise it.

**Effort:** S · **Acceptance signal:** delete one `<script>` tag and the page
still renders, with a banner naming the missing module.

## 44. The generated hue carries no decodable meaning, and the cheapest fix may be to delete it

Worth asking before anyone fixes #40: **what is the event colour for?**

Status colours earn their place — amber for pending, orange for countered carry
a fact. But the generated hue encodes a hash of the title, and there is no
legend anywhere in the week grid. A reader cannot decode it. It does not group
(two unrelated events can be near-identical, per #40), it does not rank, and it
does not indicate status. It provides one genuine affordance — the same
recurring commitment looks the same each week — at the cost of the
distinguishability problem in #40.

For ≥2 categories a legend is always present, or identity is carried by
something other than colour alone. Here identity is already carried by the
title text, which is right there in the block.

**Proposal.** Consider one neutral event fill plus the existing status colours,
and spend the colour budget where it means something — per-calendar (work vs
personal vs interviews) with a legend, which would let you see the shape of your
week at a glance. That is a real encoding. A title hash is not.

**Effort:** XS to remove, S to re-encode · **Note:** this is a product call, not
a defect. #40 fixes the mechanism; this asks whether the mechanism should exist.

## 45. `id="cc-weekly"` appears twice on production, so one weekly card is dead DOM

Not a design question — a live defect on `e6e773f`:

```
33:  <section class="cc-card cc-wr" id="cc-weekly" hidden></section>
47:  <section class="cc-card"       id="cc-weekly" hidden></section>
```

`home.js:279` reads it with `q("#cc-weekly")`, i.e. `querySelector`, which
returns **only the first match**. The card at line 47, in the right-hand column,
can never be written to and will stay `hidden` forever.

This is merge residue — two branches each added a weekly-review card in a
different position and both survived. Duplicate ids are invalid HTML, so nothing
warns; it simply picks one.

**Proposal.** Delete whichever placement is wrong (the top `cc-wr` one is the
one currently working), and add a duplicate-id assertion to the DOM tests — a
three-line check in the same idiom as `test_no_orphans.py`, catching the next
one automatically. With this many agents merging into one static page it will
happen again.

**Effort:** XS

---

# Wave 6 — the safety nets are now the whole story

`CLAUDE.md` is explicit about what replaced the approval layer:

> **What still stops a bad deploy.** Two things, and neither is an approval —
> nobody has to be asked: the test gate … and fast-forward only. … Those are
> safety nets. Removing the approval layer did not remove them, and "ship
> faster" is not a reason to switch them off.

That is a defensible trade, and the scripts implementing it are unusually well
reasoned — `autopull.sh` in particular refuses to act on a stale ref, refuses to
infer a running SHA, and documents the live wedge that taught it to. But when
two mechanisms are all that stand between a bad push and a dead dashboard, their
gaps stop being technical debt and become the risk model. Nobody has audited
them since the gate came off, so this wave does.

Five of the six are in `scripts/`. The last one is the human-facing consequence.

## 46. A deploy is recorded as "success" without ever checking the stack came up

`deploy.sh` runs the test gate, then:

```bash
$DC up -d --build --remove-orphans
docker image prune -f >/dev/null 2>&1 || true
record "success" "$(git rev-parse HEAD)"
```

`docker compose up -d` returns when containers have been **started**, not when
they are **serving**. A container that starts and immediately crash-loops
satisfies it. So `deployed.json` gets `running_commit: <sha>` for a stack that
may be entirely down.

The consequence is worse than a wrong label, because `autopull.sh` reads that
field as ground truth:

```bash
if [ -n "$RUNNING" ] && [ "$RUNNING" = "$DESIRED" ]; then
  exit 0  # the desired commit is the one actually running — nothing to do
fi
```

DESIRED equals RUNNING, so the poller concludes it has converged and **stops
retrying**. The box sits on a broken deploy and the deployment system believes
it succeeded. That is precisely the wedge class `autopull.sh`'s own docstring
describes having already been burned by — *"Comparing HEAD (the earlier
behavior) made that state look converged and permanently suppressed the retry.
That wedge happened live during the protocol bootstrap."* The comparison was
fixed; the thing being compared still isn't verified.

It also makes the deploy card (idea #24, now shipped) confidently report a
commit as running when nothing is.

**Proposal.** After `up -d`, poll for a bounded window (30–60s) and record
`success` only if the stack is actually up; otherwise record a distinct
`started_unhealthy` result, leave `running_commit` unchanged, and let the poller
retry. Needs #49.

**Effort:** S · **Acceptance signal:** deploy a commit whose container exits on
boot; `deployed.json` does not claim it is running, and the poller tries again.

## 47. The test gate has an off switch that leaves no trace

```bash
# Set DEPLOY_SKIP_TESTS=1 to force a deploy past this (emergencies only).
if [ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]; then
```

An escape hatch is reasonable — emergencies are real, and a gate with no
override gets removed rather than used carefully. The problem is that it is
**invisible afterwards**. `record "success"` writes an identical entry either
way: same shape, same fields, no marker. Nothing downstream — not
`frankenstein-status.sh`, not the deploy card, not you — can distinguish a
tested deploy from an untested one, an hour later or a month later.

`CLAUDE.md` names the test gate as one of two things protecting production and
says not to switch it off, but does not mention that the switch exists. Someone
reading only `CLAUDE.md` would not know to look for it.

**Proposal.** Keep the hatch. Record it: `"tests": "passed" | "skipped"` in
`deployed.json`, surfaced on the deploy card as a warning that persists until
the next tested deploy replaces it. An override you can see is a safety net; one
you can't is a hole.

**Effort:** XS

## 48. The gate is defined by the code it is gating

`deploy.sh` resets the working tree to production and *then* runs
`scripts/test.sh` from that tree. So the commit under test supplies the test
runner that judges it. A change that weakens `test.sh` — narrowing a glob,
deleting a test file, an `|| true` in the wrong place — disables the net for
every subsequent deploy, and passes its own gate while doing so.

This is inherent to self-testing and not a flaw on its own; what changed is that
the reviewer who would have caught it in a diff is gone, and several agents now
push here daily. CI runs the same suite on every push, but nothing prevents a
promotion while CI is red, and `promote.sh` never consults it.

**Proposal.** A small invariant test pinning the *shape* of the gate: `test.sh`
still invokes both pytest and `node --test`; the expected test files exist; the
collected test count does not fall below a floor. It cannot stop a determined
change, but it turns a silent weakening into a red suite — which is the same
bargain `tests/test_no_orphans.py` already makes, and that file's docstring
makes the case better than I can: the point is "to make the question one you
answer when it is cheap."

**Effort:** S

## 49. Sixteen services, one healthcheck

Only `db` declares a `healthcheck:` in `docker-compose.yml`. `gateway`,
`assistant`, `core`, `gmail`, `firefly`, `schedule`, `budget`, `stocks`,
`finance`, `tasks`, `networth`, `deals`, `vault`, `plex` and `powerbuy` have
none — so `depends_on` on those falls back to `service_started`, which means
"the process exists", and Compose has no opinion about whether any of them ever
answered a request.

Every one of these services already exposes `/health`, and the gateway already
probes all fifteen. The information exists; Compose just isn't told.

This is what makes #46 hard to fix cleanly, and it has a second cost: the
staggered boot ordering the compose file carefully expresses is mostly
decorative, because `service_started` is satisfied the instant a container
exists.

**Proposal.** A `healthcheck:` per service hitting its own `/health`. Three
lines each, and it makes "is the stack up?" answerable by `docker compose ps`
rather than by inference.

**Effort:** S · **Unlocks:** #46

## 50. Seven services have no tests at all

With the review layer gone, the suite is the only thing that reads a change
before it ships. These services have zero test files:

```
deals   finance   networth   plex   powerbuy   tasks   vault
```

Some of that is fine — `plex` and `deals` are low-stakes readers. But
`networth` holds account balances and applies recurring contribution rules,
`finance` owns bills and due dates, `powerbuy` is resale income, and `tasks`
feeds the score. Those four are money or state, untested, in a system that now
ships on green-suite-plus-fast-forward.

Note the asymmetry this creates: an untested service **cannot fail the gate**.
The more of the system that has no tests, the less the one remaining safety net
actually covers, and nothing surfaces that. A suite that is green because it
never looked is indistinguishable from one that is green because it checked.

**Proposal.** Not blanket coverage — the four that touch money or state, and a
line in the deploy output naming how many services the suite actually exercised.

**Effort:** M · **Acceptance signal:** `networth`'s recurring-contribution rule
and `finance`'s due-date arithmetic are swept across a calendar, per
`docs/TESTING.md`.

## 51. You have no idea what changed on your own dashboard

The human consequence of everything above. Several agents now ship to production
daily; production moved 49 commits in one day while this document was being
written, and again while wave 5 was. You find out what changed by noticing it.

There is a deploy card showing the running SHA — that answers *which* commit,
not *what happened*. And this repo has unusually good commit subjects: *"One
weekly card was dead DOM, and nothing could have told us"*, *"Money card: the
real pie chart, not bars"*, *"Calendar: real colours, Google events as the
content, and a Calendar connection that works"*. Those are already release
notes; nothing renders them.

**Proposal.** A "what changed" card, fed by commit subjects between the last
SHA you acknowledged and the running one, folded into the existing
since-you-last-checked mechanism so it clears when you've seen it. Filter to
subjects that aren't merges.

This is also the honest counterweight to removing the review layer. Nobody reads
the diffs before they ship any more — which is the point — but that only works
if you can see afterwards what shipped. Right now you can't.

**Effort:** S · **Depends on:** the deploy record (exists), the
since-last-checked mechanism (exists)
**Acceptance signal:** open the dashboard after a day away and read, in one
card, what changed about it.

---

# Wave 7 — the security surface

Everything below is from reading `gateway/app/main.py`, `docker-compose.yml`
and `services/gmail/app/main.py` on production `44b951d`. I have not run any of
it against your box; each item includes the one command that confirms or
refutes it in a few seconds, and I'd rather you verify than take my word.

The context that makes this wave worth its length: **this repository is
public**, and the project has otherwise been scrupulous about credentials.
`verify.sh` "never prints secrets, email bodies, or tokens". The `plex` service
notes "the token never reaches the browser". `firefly` "never sends the token to
the browser". `vault` "stores nothing". That care is real and consistent — and
one route defeats all of it.

## 52. `/api/gmail/internal/token` returns a live Google access token to anyone who can load the dashboard

The gateway proxies anything, to any registered service, with no path filter and
no authentication:

```python
@app.api_route("/api/{app_key}/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def proxy(app_key: str, path: str, request: Request):
    sub = REGISTRY.get(app_key)
    ...
    target = f"{sub.url}/{path}"
```

`gmail` registers as `gmail` and exposes:

```python
@app.get("/internal/token")
async def internal_token():
    """Access token for OTHER sub-apps on the internal docker network only …
    Not linked from the UI and not meaningful to call from outside the
    compose network."""
    ...
    return {"access_token": token, "scopes": granted_scopes(), ...}
```

The docstring describes an intent. Nothing enforces it. `/internal/` is a naming
convention, and the proxy has never heard of it, so:

```
GET http://<box>:8080/api/gmail/internal/token
```

returns a live OAuth access token carrying `gmail.modify` **and**
`calendar.events` — read and modify the whole inbox, read and write the
calendar. The gateway has no auth (confirmed: the only match for "auth" in
`gateway/app/` is a comment), so there is nothing to get past.

Verify or refute in one command, from any other machine on the network:

```bash
curl -s http://<box-ip>:8080/api/gmail/internal/token | head -c 120
```

If that returns `access_token`, it is real. If it returns `not connected`, the
route is still open and will start returning one the moment Gmail is connected.

**Proposal.** Two changes, both small:

1. The proxy refuses any path segment `internal` (and returns 404, not 403 —
   don't confirm the route exists).
2. `/internal/token` requires a shared secret injected into both containers by
   compose. The internal network is a convenience, not a boundary; a token this
   powerful should be asking for a credential.

Then rotate — see #56. A token that has been reachable should be treated as
having been reached.

**Effort:** XS for the fix · **Priority:** this is the one item in the whole
document I would do today.

## 53. And it isn't only "someone on your network" — it's any website you visit

The escalation that makes #52 urgent rather than theoretical.

There is no `TrustedHostMiddleware`, no `CORSMiddleware`, no `Host` validation
anywhere in the gateway or any service (confirmed: zero matches). A
no-authentication HTTP service on a LAN, with no `Host` header check, is the
textbook target for **DNS rebinding**: a page you visit resolves its own domain
to your box's private address after the page loads, and from then on the
browser treats requests to it as same-origin. Same-origin means the script can
*read the response*.

So the exposure isn't limited to devices on your network. It's any page loaded
in any browser on any device on your network — which includes an ad iframe.

Two independent mitigations, both cheap: validate the `Host` header against an
allowlist (a rebinding attack must send an attacker-controlled `Host`, so this
breaks it), and put the dashboard behind Tailscale-only binding plus single-user
auth — which is SCRUM-98, filed in wave 1 and still open. That ticket was
written when the argument was "net worth is on the page". The argument is now
"a Google access token is on the page".

**Effort:** XS (Host allowlist) · **Related:** SCRUM-98

## 54. All seventeen services publish host ports, so the gateway is not a boundary

`docker-compose.yml` publishes a host port for every single service — 8080–8099,
and:

```yaml
  db:
    ports:
      - "5432:5432"
```

Postgres is on the network. `.env.example` ships `POSTGRES_USER=frank` /
`POSTGRES_PASSWORD=frank`, so if those defaults were kept, the database holding
your gym history, focus sessions, Big 3, captures, calendar, net-worth accounts
and budget definitions is one `psql -h <box> -U frank` away for anything on the
LAN.

```bash
grep POSTGRES_PASSWORD .env      # is it still 'frank'?
```

The rest of the range means every "internal" service contract — not just
`/internal/token` — is directly reachable without even going through the
gateway. The gateway's role as the single front door is a diagram, not a
control.

Worth knowing while fixing this: on Linux, Docker's published ports insert rules
into its own iptables chain that **bypass a host `ufw` policy**. A firewall you
believe is protecting these ports quite likely isn't.

**Proposal.** Bind everything except the gateway to `127.0.0.1` (`"127.0.0.1:8083:8000"`)
so the ports stay available for debugging on the box and vanish from the
network, and drop the `db` publish entirely — nothing outside compose needs it.
That is a one-line-per-service change and it removes most of this wave's surface
in a single commit.

**Effort:** XS

## 55. "Internal" needs to be a control, not a convention — and a test can hold it

#52 is one instance of a general gap: the codebase distinguishes internal from
external endpoints **by name**, and nothing enforces the distinction. The next
service that adds an `/internal/` route inherits the same exposure silently.

The project already has the right pattern for turning a convention into a check.
`tests/test_no_orphans.py` exists because "who consumes this?" was expensive to
answer late, and its docstring says the point is "to make the question one you
answer when it is cheap." `tests/test_wire_names.py` and
`tests/test_deploy_boundary.py` are the same instinct.

**Proposal.** A `tests/test_internal_not_proxyable.py`: enumerate every route in
every service, and assert that anything under `/internal/` is rejected by the
gateway proxy. It fails the moment someone adds a new one, which is the only
time it's cheap to fix.

**Effort:** S

## 56. There is no credential-rotation runbook, and after #52 you need one

`docs/SETUP-GMAIL.md`, `SETUP-FIREFLY.md`, `SETUP-PLEX.md` and `SETUP-VAULT.md`
all explain how to obtain and install a credential. None explains how to
**revoke and replace one**, which is the procedure you need when something has
been exposed, when a laptop is sold — you have nine resale MacBooks — or when
the move happens and hardware changes hands.

Concretely, right now: the Google OAuth token from #52 should be revoked at
`myaccount.google.com/permissions`, and the client secret rotated in the Cloud
console. Neither step is written down anywhere in the repo.

**Proposal.** One `docs/ROTATION.md`: for each credential, where it lives, how to
revoke it, how to mint a replacement, what breaks while it's rotating, and how
to confirm the new one took. Then the infra card (SCRUM-67) can show the age of
each credential, which turns rotation from a thing you remember into a thing you
can see.

**Effort:** S · **Related:** SCRUM-67, SCRUM-68

## 57. The repository is public, which changes the calculus rather than the severity

None of the above is *caused* by the repo being public — the exposure is the
same either way. What changes is that the map is published: the exact route, the
port assignments, the default database credentials, and the docstring explaining
what the endpoint returns are all readable by anyone, and indexed.

This matters more than usual for you specifically. You are job hunting, and a
public repository of this quality is exactly the thing a hiring manager clicks
through to — so the population reading this code carefully is not hypothetical,
and it is not all friendly.

That cuts both ways, and it is worth saying plainly: this codebase reads
extremely well. The honesty rules in the money layer, the calendar-sweep tests,
the docstrings that explain *why* rather than *what* — those are the marks of a
strong engineer, and they're publicly visible too. Which is exactly why the one
open credential route is worth closing before more people go looking.

**Proposal.** Fix #52 and #54 first. Then decide, deliberately rather than by
default, whether this repo stays public — and if it does, add a `SECURITY.md`
saying how to report something, because people will find things.

**Effort:** XS

---

# Where I'd start

Across all three waves, in order:

1. **#2 + #37 — the data that can't be recovered.** One directive: back up the
   ledger, the vault and the dashboard's own Postgres, restore-test it once, and
   put the result on the screen. Everything else here is an improvement; this is
   the only one that's insurance.
2. **#13–16 — the correctness pass.** The screen currently says holdings aren't
   configured when a container blinked, calls 15 services healthy after checking
   2, credits Sunday-night workouts to next week, and scores an unset goal as a
   failure. Small, contained, and everything downstream depends on trusting the
   numbers.
3. **#17 — the unwired inventory.** Nine built-and-unreachable features,
   including the attention feed `AUDIT.md` promised, plus a consumer test so it
   doesn't recur. Best value-to-new-code ratio in the document.
4. **#27–31 + #1 — the job hunt.** The classifier first, because it's the hard
   part and the pipeline is mostly plumbing once stages exist. #32 (calendar
   collisions) rides along, and it's the one with a genuinely bad failure mode.
5. **#34 — dismiss and snooze.** Cheap, and it's what stops the attention model
   going stale and taking your trust with it.

Then the additive ones — #20 (cash runway), #35 (kiosk), #21 (evening mode) — in
whatever order suits the week.

**Suggested first directive:** #13–16 as a single correctness pass. It's the
safest opening move under the protocol — contained scope, no new services,
testable acceptance criteria, `Deployment Authorization: test-only` — and it
makes every later idea land on a screen you can believe.

---

_Prepared by Claude (implementation engineer) under `PROTOCOL.md`. No product
code, `PRODUCT_DIRECTIVE.md`, or `STATE.json` was modified. The roadmap is the
Product Owner's._
