# Implementation Handoff — FC-008

Task ID: FC-008
Task branch: `claude/FC-008-weekly-calendar`
Baseline: `0a5d24a` (`production`)
Implementation commit: `add5b2b99f9f67c42824507a7237a404817882f0`
  (supersedes `176e72b`; both are on the task branch)
Authorizing control commit (epoch): `c60c29798510224111bb8339c9aacc4655e6972e`
Directive commit: `71dba66c8814646a16b3d244e76a4a074e812c20`
Deployment Authorization: **none** — task branch pushed for review, nothing deployed.

## Where this task came from

Anthony authorized FC-008 directly, as Product Owner, on 2026-09-07, and
instructed Claude to record the directive on `control`. This is stated plainly
because it knowingly overrides two standing positions:

- `PRODUCT_VISION.md` priority 0: "No feature initiative interrupts FC-002."
- The FC-002 directive: "No new product features."

**FC-002 is paused, not abandoned and not accepted.** Nothing about FC-002 was
moved, edited or overwritten by this task, and its canonical handoff files
remain at their existing paths on this branch. It in fact advanced
independently while FC-008 was in flight: another agent published a corrected
FC-002 handoff at `5c5d64f` (handoff commit `7e250de`), and this handoff is
rebased on top of that. Codex retains acceptance authority over it and may
re-prioritize it ahead of this task.

## What was built

The main dashboard's schedule card was a flat chronological list of the next
six events (`renderCalendar`, `gateway/static/home.js`). It is now a seven-day
grid: today and the next six days, each labelled with its weekday, its month,
and an ordinal day of the month.

### Server — `services/assistant/app/dashboard.py`

All date logic lives here, in the module that already has no I/O and already
takes `now`/`local_tz` from its caller. That injection point is the clock seam
`docs/TESTING.md` requires, and it is what lets the tests pin any date.

- `week_window()` buckets events by **local date**, never by adding 24-hour
  offsets to a timestamp. A 23- or 25-hour DST day would otherwise slide an
  event into a neighbouring column.
- `schedule_state()` separates an unreachable service from an empty week,
  mirroring `firefly_state`'s three-states-not-two rule.
- `ordinal_suffix()` / `ordinal()` with the 11th/12th/13th exceptions.
- Also surfaced rather than dropped: same-day conflicts, in-progress events
  (including one that started yesterday and is still running), all-day events,
  and a count of commitments falling past the window (`beyond`).

`main.py` adds `week` to the `/home` payload and attaches `week["state"]`.

### Client — `gateway/static/home.js`, `home.css`

- Seven columns on desktop; a snap-scrolling strip below 1080px; stacked rows
  below 680px. Measured horizontal page overflow at 390px wide: **0px**.
- Month boundaries are labelled and accented instead of silently restarting
  the day numbers — a window spanning Oct/Nov or Dec/Jan says so.
- Pending and countered holds keep distinct treatment. Every status is carried
  by a **word** as well as a colour.
- One decorative theme per month (pumpkins in October, snow in December, and
  ten others). Themes set `--sn-accent` / `--sn-wash` only; `--text`,
  `--panel` and `--line` are never themed, so contrast is identical in every
  month of the year. Toggleable, persisted in `localStorage`, and stilled
  entirely by `prefers-reduced-motion`.
- The window re-fetches just after local midnight, so a tab left open
  overnight stops calling yesterday "Today".
- Removed `evWhen()`, dead once the flat list was replaced.

## Verification

Full suite on the exact implementation tree `add5b2b`:

```
1513 passed, 4 warnings in 146.35s
== javascript: unit tests ==   # tests 5   # pass 5   # fail 0
ALL TESTS PASSED
```

Baseline `0a5d24a` runs 1486 and has no JavaScript tests at all. This adds 27
Python cases and a 5-case `node --test` suite.

Diff vs baseline — 6 files, +919 / −53:

```
docs/TESTING.md                            |  23 +
gateway/static/home.css                    | 229 +++++++++++++--
gateway/static/home.js                     | 239 +++++++++++++---
gateway/static/index.html                  |   1 +
gateway/static/weekclock.js                |  37 +++   (new)
gateway/tests/weekclock.test.js            |  93 +++++++   (new)
scripts/test.sh                            |   8 +
services/assistant/app/dashboard.py        | 232 +++++++++++++++
services/assistant/app/main.py             |  10 +-
services/assistant/tests/test_dashboard.py | 338 ++++++++++++++++++++++
```

`.frankenstein/` on the task branch is **unchanged** (0 files differ from
production), so its placeholder STATE/directive/handoff still satisfy the
protocol tests.

### Dates covered by the sweep

Per `docs/TESTING.md`, none of the date logic is exercised against today.

- 800 consecutive days from 2026-01-01, asserting on every one of them: seven
  consecutive days, first is today, no past day, no repeats, and weekday /
  month / ordinal / weekend flags matching the real calendar.
- Month-boundary labelling asserted on all 800 days.
- Leap day: 2028-02-29 present, labelled "29th", Tuesday, followed by 1 March.
- DST: 2026-03-06, 2026-10-30, 2027-03-12, 2027-11-05 — each **swept hour by
  hour, all 24 hours**.
- Ordinals: every day 1–31.

### Schedule states exercised

`confirmed`, `pending`, `countered`, `declined` (filtered), all-day,
zero-length, in-progress, started-yesterday-still-running, finished,
unparseable timestamp, past-the-window, empty-but-healthy, and
service-unreachable.

### Regression tests confirmed to fail without their fix

`docs/TESTING.md`: "A regression test that never fails is decoration." Each
was verified by reintroducing the bug and watching the suite go red:

| reintroduced bug | result |
|---|---|
| drop the 11/12/13 ordinal branch | 2 failed |
| filter events to `confirmed` only | 1 failed |
| make `schedule_state` always return `ok` | 1 failed |
| bucket by UTC + 86400s instead of local dates | 4 failed |
| detect conflicts per day instead of window-wide | 1 failed |
| browser rollover using +86400000 | 4 of 5 node tests failed |
| rollover firing exactly on the boundary (grace 0) | 1 node test failed |

**The DST test did not bite on its first draft.** Anchored at 9am it passed
against the very bug it claimed to guard, because an absolute-offset
implementation only moves the date within an hour of midnight. It was
rewritten to sweep all 24 hours, and only then did it fail against the bug.
Recorded here because it is exactly the failure mode that documentation
warns about.

### Rendered verification

The shipped `index.html` / `home.js` / `home.css` were rendered in headless
Chromium against a stubbed API whose `week` came from the real `week_window`.
Two defects were found this way and fixed:

1. Weekday names were being clipped ("THURSD…") by the Today pill and count
   badge — a direct failure of requirement 2. The pill moved to the date row.
2. The decoration layer was painted beneath fully opaque columns, so it was
   invisible except in the 8px gaps.

Confirmed in-browser afterwards: 7 columns render; full weekday names; the
Oct→Nov and Dec→Jan boundaries label correctly; the decor toggle flips
14 glyphs on/off and survives a reload; arrow keys walk all seven days;
`aria-label` reads "Thursday, October 29th, 2026, nothing scheduled";
`prefers-reduced-motion` resolves `animation-name: none`; 0px horizontal
overflow at 390px. All fixture data is synthetic, per vision principle 6.

## Deviations From Directive

Deviations 1 and 2 below were declared at the first handoff (`176e72b`) and
have since been **closed** in `add5b2b`. They are kept here, marked, rather
than deleted: a deviations section that quietly loses entries between
revisions is not a record.

1. ~~**Requirement 3, midnight rollover — not covered by the suite.**~~
   **CLOSED in `add5b2b`.** The calculation moved out of `home.js` into
   `gateway/static/weekclock.js`, where it takes `now` as an argument rather
   than reading the clock itself. `scripts/test.sh` now runs `node --test`
   over it: 800 days × four times of day, DST days hour by hour, and
   month/year/leap boundaries, under a pinned `TZ`. Fixing it also removed a
   real bug — the original used `+86400000`, which lands an hour off on a 23-
   or 25-hour day; the new sweep fails in 4 of 5 cases against that.

2. ~~**Requirement 5, conflicts — same-day only.**~~ **CLOSED in `add5b2b`.**
   `_mark_conflicts` now runs once over the whole window instead of per day,
   so an 11:30pm call and a 12:15am call are recognised as one collision. Each
   side is also told which direction the clash lies in (`conflict_neighbour`
   is `next` or `previous`), because labelling a 00:15 event "overlaps next
   day" when its counterpart was the night before is worse than saying
   nothing.

3. **All-day detection is heuristic.** *(still open)* A `starts_at` with no
   time component is treated as all-day. That is how a date-only calendar
   value arrives today, but the schedule service has no explicit `all_day`
   column, so an event stored as exactly midnight with a time component reads
   as a timed 12 AM event. Adding the column was out of scope.

4. **`systems.down` was not extended.** *(still open)* An unreachable schedule
   service is now honest inside the week card (`week.state`), but it still
   does not appear in the dashboard's `systems.down` list, which tracks only
   core and email. Changing that is a wider behaviour change than this
   directive authorizes.

Nothing else departed from the directive. No changes to money, budget,
Firefly, Gmail, credentials, accounts, spend or security boundaries. No
calendar writes. No promotion, no deployment.

## A second decoration test, caught the same way

The first draft of the new `node --test` suite asserted that the rollover
fires `GRACE_MS / 1000` seconds past midnight — comparing the output to the
very constant that produced it. Setting the margin to zero still passed. It
now asserts against a literal (strictly after the boundary, inside the same
minute), and fails when the margin is removed.

This is the second time in this task that a date test passed against the bug
it claimed to guard; the first was the DST case at a 9am anchor. Both are
written up in `docs/TESTING.md` so the pattern is on the record rather than
in one commit message.

## Open / not done

- FC-002 is untouched by this task. It moved on its own while FC-008 was in
  flight (now `5c5d64f`); its remaining findings are tracked in its own
  handoff, not here.
- The status publisher remains absent remotely; unchanged by this task.
- This work is **not accepted**. Deployment Authorization is `none`.

## Next move

Product Owner's. Review `176e72b` on `claude/FC-008-weekly-calendar`, then
either accept, request changes, or re-prioritize FC-002 ahead of it.
Claude stops here.
