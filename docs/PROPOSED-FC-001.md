# Proposed FC-001 — for the protocol agent to commit to `control`

**This file is not authoritative and authorizes nothing.** It is a handoff from
the team-lead session to the protocol agent, routed through the repo because
the two sessions cannot message each other directly.

## Provenance

The owner said, in his own words on 2026-09-06:

> when i click on apps and services then i click Firefly, i want everything
> shown on there (besides recent transactions) to be shown on the MAIN
> dashboard. The second thing i want on the main dashboard is my
> calendar/schedule (preferably at the top). Make these two a priority.

The directive below is that, transcribed into the protocol's format with the
supporting code facts verified. Nothing was inferred from the ideas backlog.

## What the owner decided about landing it

Asked who should commit this, he chose **the protocol agent** — `.frankenstein/`
is your lane, not the team lead's. So it is yours to write.

## Open question he routed to you

Asked whether Deployment Authorization should stay `none`, or be raised to
`test-only` or `deploy-approved`, he answered: **"ask protocol agent"**.

The draft says `none`. My reasoning, which you should overrule if the box tells
you otherwise: `none` is the protocol default and this is the first task ever
run through the loop, so a review round before anything reaches the OptiPlex is
cheap insurance. The counter-argument is real though — both features depend on
live Firefly and calendar data that is hard to fake convincingly, which is
exactly what `test-only` exists for. You know the deployment surface and the
enforcement work in flight; make the call and set the field before you commit.

## The write order

Per your own `PROTOCOL.md` control write order:

1. Commit `.frankenstein/PRODUCT_DIRECTIVE.md` (below) while `STATE.json` still
   reads `product_owner` / `awaiting_directive`. That commit cannot wake the
   worker.
2. Note its SHA.
3. Commit `STATE.json` with `directive_commit = <that SHA>`, `turn = claude`,
   `status = ready_for_implementation`, `last_actor = product_owner`,
   `updated_at` = now.

`control` was at `78d965e` when this was drafted; `STATE.json` still read
`FC-001 / product_owner / awaiting_directive` and the directive was still the
placeholder. Re-check before writing — do not clobber newer Product Owner state.

## Also still unruled

`d1af4e7` (money layer, 1,524 lines, no directive, no handoff) collides with
requirement A. The directive tells the implementer to declare the relationship
rather than overwrite it, but ruling on it remains outstanding.

---

# The directive, verbatim

# Product Directive

Task ID: FC-001
Status: ready_for_implementation
Priority: High — the Product Owner asked for both of these "ASAP"
Deployment Authorization: none

> Transcribed by the team-lead session from the human owner's own words on
> 2026-09-06. It is his scope, not Claude's: he named both items and called
> them priorities. Nothing here was inferred, extrapolated, or chosen from the
> ideas backlog. ChatGPT, as Product Owner, may amend, reorder or replace this
> at any time — this is a transcription, not a claim on the roadmap.

## Objective

Two things the owner wants on the **main dashboard** (`gateway/static/index.html`,
the command center — not the legacy lounge):

1. Everything the Firefly sub-app shows, **except recent transactions**.
2. His calendar / schedule, **preferably at the top**.

## Product Context

Both exist already and neither reaches the page he actually opens.

**Firefly.** Its detail view (`app.js` `firefly()`, from `GET /firefly/dashboard`)
renders four tiles — net worth, earned this month, spent this month, left to
spend — a spending-by-category donut for the last 30 days, an accounts list with
balances, and recent transactions. Reaching any of it costs two clicks: Apps &
services → Firefly. The main dashboard's `#cc-money` card shows a different,
smaller subset.

**Calendar.** There is no calendar card on the main dashboard at all. The only
surface is a single `🗓️ Next:` line appended to the bottom of the Big 3 card
(`renderToday`, `home.js:196`), and it is wrong twice over:

- `GET /events` (`services/schedule/app/main.py:84`) applies **no time bound** —
  `SELECT * FROM events WHERE status != 'declined' ORDER BY starts_at` returns
  every event ever recorded, ascending. `next_event` is `events[0]`
  (`assistant/main.py:722`), so it is the **oldest event on record**, not the
  next one. Filed as issue #3.
- `build_home` keeps only `status == "confirmed"` (`assistant/main.py:696`), so
  pending 🟡 and countered 🟠 calendar holds — the entire output of the
  Gmail→Bones→Cal interview pipeline — are dropped before rendering.

So the owner has never seen a correct "next event" on his dashboard. **A
calendar card built on today's `next_event` would ship visibly wrong**, which
is why the fixes below are inside this task rather than deferred.

## Requirements

**A — Firefly on the main dashboard**

1. Surface on the command center, without leaving it: net worth, earned this
   month, spent this month, left to spend; spending by category for the last 30
   days; and the accounts list with balances.
2. **Recent transactions stay out**, as asked. Keep a route into the Firefly
   sub-app for them.
3. Obey `docs/BUDGETS.md`: unknown is `null` and renders as unknown, never `$0`;
   a partial window is never presented as complete. Issues #1 and #7 are this
   rule already broken in `/networth` and `/summary`; do not add a third site.
4. Distinguish *unreachable* from *not configured* (issue #8, wave-2 idea #13).
   A slow or down Firefly must not render as "not connected — set FIREFLY_URL",
   which sends the owner to fix configuration that is already correct.

**B — Calendar at the top**

5. Add a calendar/schedule card to the main dashboard, positioned at or near the
   top of `#cc-grid`. The owner said "preferably at the top"; treat placement
   above the fold as the requirement and exact ordering as your judgement.
6. Show what is actually coming: today's events and the next several upcoming,
   with times.
7. **Fix issue #3 as part of this work.** Upcoming means upcoming — bound the
   query by time, or select the next event by `starts_at >= now`. Do not build
   the card on `events[0]` as it stands.
8. **Show pending and countered holds distinctly** (🟡 offered / 🟠 countered /
   🟢 confirmed), rather than filtering them out. An interview slot awaiting a
   reply is exactly what he needs to see.
9. Date and time logic goes through the single clock seam and is tested across a
   calendar sweep, never against "today" — `docs/TESTING.md`. Events are the
   most timezone-sensitive thing on the page.

**C — Both**

10. The main dashboard must stay usable when either service is slow or down:
    degrade per requirement 4, never block the page.

## Acceptance Criteria

1. Opening the command center cold shows net worth, earned, spent, left to
   spend, spending by category, and account balances — no clicks.
2. Recent transactions are **not** on the main dashboard.
3. A calendar card is visible at the top of the page without scrolling, listing
   today's and upcoming events with times.
4. With an event dated in the past and one dated tomorrow present, the card
   shows tomorrow's. A test asserts this — it is the issue #3 regression.
5. A pending and a countered hold both appear, visually distinct from confirmed.
6. With Firefly stopped, the money area says it is unreachable and does **not**
   say "not connected" or render `$0` for an unknown value. A test asserts it.
7. `bash scripts/test.sh` passes in full, calendar sweep included.

## Explicitly Out of Scope

- Recent transactions on the main dashboard.
- The legacy lounge (`lounge.html`) and `jobs.html`.
- The job pipeline, infra/restore-test card, cash runway, auth — every other
  idea in `docs/PRODUCT_IDEAS.md`. This directive is these two things.
- Promotion to production. Deployment Authorization is `none`: push the task
  branch for review and stop.
- Any change under `.frankenstein/`, `scripts/promote.sh`, or the `production`
  branch.

## Verification Required

- `bash scripts/test.sh` in full, with results reported honestly.
- Named tests for acceptance criteria 4 and 6 — the two that encode bugs rather
  than features.
- A description of what the card shows against real data, or an explicit
  statement that live verification was not possible. Never report live results
  that were not observed.

## Product Owner Notes

**The money layer overlaps this.** `d1af4e7` on
`claude/financial-import-spending-nzhx02` is 1,524 lines of paycheck and
spending work that already modifies `home.js` money rendering. It was built
without a directive, has no handoff, and is still unruled — and it collides
directly with requirement A. The implementer must **not** silently re-implement
or overwrite it: state in the handoff how the two relate and what was chosen.
Ruling on `d1af4e7` is still outstanding and remains the Product Owner's.

**Baseline** is `production` at `9b96bd0`.

**Branch naming**: `claude/FC-001-dashboard-firefly-calendar`, per the protocol.

Issues #1, #3, #7 and #8 are the filed versions of the defects named above.
