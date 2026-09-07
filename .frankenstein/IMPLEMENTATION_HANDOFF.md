# Implementation Handoff — FC-008

Task ID: FC-008
Task branch: `claude/FC-008-weekly-calendar`
Baseline: `e7adf83` (`production`, current tip)
Implementation commit: **`5f6b9bf`**  — merges production `e7adf83`
Previous revision: `a137d6d` (answered the correction; was based on `0a5d24a`)
Authorizing control commit (epoch): **`f1a56bdb7d1ed4497de10086a4610bfb3d96f595`**
Directive commit: **`4769b630df710863b768b7bebcd6ac2918cf55a8`**
Deployment Authorization: **none** — task branch pushed for review, nothing deployed.

This is the canonical FC-008 handoff. It replaced FC-002's at these paths on
Codex's instruction ("identify FC-008 clearly as the new handoff"). **FC-002's
canonical handoff is preserved in git history at `7e250de`**, naming
implementation `5c5d64f`; FC-002 remains paused and unaccepted.

Supersedes `176e72b`, `add5b2b` and `ce713da`.

---

## Codex correction at epoch `f1a56bd` — what changed

### 1. Honest states (P1) — Codex reproduced a real bug

`schedule_state` returned `ok` for **any** truthy payload, so a schedule
service answering normally while the calendar was **disconnected** rendered
exactly like a genuinely clear week. Confirmed and fixed.

Four states now — `ok`, `unreachable`, `disconnected`, `unknown` — carried
through the real read-only path.

The connection evidence is not invented. `services/schedule/app/gcal.py`
borrows its access token from gmail's `/internal/token` because one OAuth
consent covers both accounts, so gmail's own `mode` **is** the calendar
integration's connection state. It is also the only such evidence available:
`GET /events` reads local Postgres and answers identically whether Google is
connected or not, exactly as the directive says. `main.py` passes the **raw**
gmail payload, because the pre-existing `gmail_mode` local defaults an
unreachable gmail to `"disconnected"` — which would convert "we cannot tell"
into a confident claim. `error`, `never`, missing and unrecognised shapes all
resolve to `unknown`; health is never guessed upwards.

Local commitments survive: only a total outage suppresses the grid.
`disconnected` and `unknown` render the full week with a caveat above it,
because rows already in Postgres are real whatever Google is doing.

Rendered evidence (shipped `index.html`/`home.js`/`home.css`, headless
Chromium, synthetic fixtures, `week.state` produced by the real function):

```
[disconnected] grid rendered: True | outage banner: False
[disconnected] caveat: "⚠ Google Calendar isn't connected, so only commitments
               stored here are shown. A clear day may not be a free day."
[disconnected] event chips still rendered: 14
[disconnected] status labels: ['OFFERED — AWAITING REPLY',
                               'THEY COUNTERED — NEEDS YOUR YES']
[unknown]      caveat: "◔ Couldn't confirm the calendar connection, so anything
               that lives only in Google may be missing from this week."
```

Backend coverage added: `test_a_disconnected_calendar_is_not_a_healthy_empty_week`
(asserts the exact `{"connected": false, "events": []}` shape Codex used),
`test_unestablished_connection_health_is_unknown_not_ok`,
`test_connection_evidence_is_never_inferred_from_the_wrapper`,
`test_every_state_is_one_of_the_documented_four`,
`test_local_commitments_survive_a_disconnected_calendar`. All four fail
against the reinstated two-state version.

### 2. Decoration (P1) — also a real miss

Codex was right: the toggle cleared the drifting layer while `dayCard` still
emitted the month motif unconditionally. `Decor off` now suppresses the
motifs and the header glyph too.

Verified through the shipped render path, not by inspection:

```
decor ON : flakes 14, motifs 7, days 7
decor OFF: flakes  0, motifs 0, days 7
after reload: flakes 0, motifs 0   -> persisted: True
weekday names preserved: True   dates preserved: True
status labels preserved: True   ['OFFERED — AWAITING REPLY',
                                 'THEY COUNTERED — NEEDS YOUR YES']
keyboard walk: Thursday..Wednesday (all seven)
phone 390px: days=7 motifs=0 flakes=0 page-overflow=0px
reduced-motion animation-name: none
```

### 3. CI — the bootstrap's approach, not skip-widening

Namespaces are enabled at the **runner**, the checkout is unshallowed so the
deploy-boundary refs resolve, and `FRANKENSTEIN_REQUIRE_SANDBOX=1` makes an
incapable runner fail loudly rather than skip containment.

Ported surgically. The rest of `claude/po-handoff-release`'s worker tests
assert a `claude-worker.sh` that exists only on that branch (`HANDOFF_BRANCH`,
`"authorization changed"`), and its `test_protocol.py` rework is bootstrap
infrastructure — importing either is what the directive forbids. One test from
the bootstrap's deploy-boundary diff was also left out deliberately:
`test_an_undeterminable_desired_commit_reports_pending` asserts a
`frankenstein-status.sh` improvement that lives on that branch, so porting it
without its script would have been importing unrelated work.

Proven in both directions here:

| condition | result |
|---|---|
| namespaces available, `REQUIRE_SANDBOX=1` | **129 passed, 0 skipped** — containment actually exercised |
| namespaces unavailable, `REQUIRE_SANDBOX=1` | **18 failed** — loud, not skipped |
| namespaces unavailable, unset | visible SKIPs, no failures |

An earlier attempt (`ac0d4af`, cherry-picking the skip-widening fix) was
pushed and then reverted in `ce713da`: it cleared only 18 of 19 and traded
away the containment coverage FC-002 requires.

### 4. Binding

`.frankenstein/AUTHORIZING_CONTROL_COMMIT` (`f1a56bd`) and
`.frankenstein/TASK_BRANCH` added to the task branch; canonical handoff,
STATE.json, TASK_BRANCH and AUTHORIZING_CONTROL_COMMIT published here.

### 5. Acceptance wording

Noted; the "today plus six days" rule was already what the code and the sweep
assert, on every one of 800 days.

## Full suite on `a137d6d`

```
1518 passed, 0 skipped, 4 warnings in 122.96s
== javascript: unit tests ==  # tests 5  # pass 5  # fail 0
ALL TESTS PASSED
```

Zero skips: the containment tests now run here rather than skipping.

## ⚠ The merged candidate is stale

`.frankenstein/FC-008/MERGED_CANDIDATE.md` proposes
`8be0cd2` on `claude/FC-008-week-plus-money` as the promotion candidate. It
merges FC-008 at **`ce713da`**, which predates this correction — so **it does
not contain either P1 fix Codex asked for**. It should be re-merged onto
`a137d6d` before it is considered for acceptance or promotion. Flagged rather
than silently re-merged, because that branch belongs to another session.

---


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

The pre-merge revision `a137d6d` ran 1518 against the old baseline `0a5d24a`
(which ran 1486 and had no JavaScript tests at all). `5f6b9bf` runs **2325
passed, 0 skipped** because it now also carries the money layer's paycheck
suites, which arrived with production.

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

## CI is red on this branch, and it is not this branch's

Run 34078322229 on `add5b2b` failed: **19 failed, 1457 passed**. Every failure
is in `tests/test_claude_worker.py` and `tests/test_deploy_boundary.py` — the
autonomous-worker containment suite. None is in code FC-008 touches; the diff
does not modify either file.

Demonstrated rather than asserted, by reproducing with a failing `unshare` on
PATH so the host cannot create namespaces:

| tree | namespaces | result |
|---|---|---|
| production `0a5d24a` (the then-baseline) | unavailable | **18 failed**, 135 passed, 37 skipped |
| this branch `add5b2b` | unavailable | **18 failed**, 135 passed, 37 skipped |
| this branch `add5b2b` | available | **190 passed**, 0 failed |

Identical on the baseline and on this branch: inherited, not introduced.
GitHub's hosted runners cannot create mount/PID/network namespaces, and the
worker correctly refuses to run a child unconfined on such a host.

**This does not block the deployment gate.** `deploy.sh` runs
`scripts/test.sh` on the OptiPlex, which *can* create namespaces — the third
row above is that case, and it is green.

### A fix was tried here and backed out

`ac0d4af` cherry-picked `485e3b9` from `claude/ci-runner-capability`, which
widens the `needs_sandbox` guard. Pushing it proved two things and it was
reverted in `ce713da`:

1. It does not finish the job — 19 failures became 1. The survivor,
   `test_mismatched_running_commit_reports_pending`, has an unrelated cause:
   the status helper resolves `origin/production` from the ambient checkout,
   which exists on the box but not in a single-branch CI checkout.
2. It is the weaker of two competing fixes and not FC-008's to make.
   `claude/po-handoff-release` fixes the same failures by enabling the
   namespaces at the runner, so the containment assertions still *run* in CI,
   and separately makes the deploy-boundary test hermetic. FC-002 requires
   containment coverage; widening the guard trades it away. Carrying a rival
   edit to that file here would collide with the branch that owns it.

`tests/test_claude_worker.py` on this branch is now byte-identical to
production. CI here goes green when `claude/po-handoff-release` lands.

## Production moved under this branch — merged, not asserted

While FC-008 was in flight, production advanced `0a5d24a` -> `e7adf83` (the
money / Firefly pie work, promoted on Anthony's direct instruction by the
protocol-agent session). FC-008 stopped being a fast-forward.

`5f6b9bf` merges `e7adf83` in rather than restating an ancestry claim that had
become false. A merge, not a rebase: this branch is published and other
sessions reference its SHAs.

Two conflicts, both resolved against what the code does:

- `docs/TESTING.md` — purely additive both sides. All four coverage rows kept
  (their paycheck-engine and service-wiring rows, my dashboard and weekclock
  rows), plus the browser-side date-logic section.
- `gateway/static/home.css` — production **deleted** the `.cat-*` category-bar
  rules, because the Money card now draws a real pie chart. Took the deletion
  rather than resurrecting dead CSS: verified the merged `home.js` references
  none of `cat-row`/`cat-bar`/`cat-rows`/`cat-n`/`cat-v`. Kept `.wk-caveat`,
  which is mine and live.

`home.js` and `services/assistant/app/main.py` merged automatically. Checked
rather than assumed: `render()` calls both `renderWeek` and `renderMoney`, the
home payload still carries `week_window` / `schedule_state` alongside the
paycheck brief, and the week grid was re-rendered in headless Chromium after
the merge — seven columns, conflict flags, and pending/countered labels all
intact.

**Full suite on the merged tree: 2325 passed, 0 skipped, + 5 node tests,
ALL TESTS PASSED.** Fast-forward onto `e7adf83` re-verified.

Note for whoever promotes: Codex has published `PO_REVIEW_e7adf83.md` on
control with two P1 calculation findings against the commit that is now live.
Those belong to the money layer, not FC-008, and the protocol-agent session
is handling them — but they are findings against **running** code.

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

3. **The task branch keeps the placeholder `STATE.json`.** *(new, and a
   knowing departure from correction 4)* Codex noted the candidate "retains
   FC-001 placeholder STATE.json". Replacing it with the live authorization
   snapshot fails `tests/test_protocol.py` on this branch — that suite asserts
   the task branch carries a neutral placeholder, and the rework that relaxes
   it (+129 lines) lives on `claude/po-handoff-release`, which this directive
   forbids importing. The binding intent is met instead by
   `AUTHORIZING_CONTROL_COMMIT` and `TASK_BRANCH`, which the protocol gate
   accepts (21 passed). Resolving the contradiction properly needs the
   protocol-test change to land with FC-002; it is not this task's to make.

4. **All-day detection is heuristic.** *(still open)* A `starts_at` with no
   time component is treated as all-day. That is how a date-only calendar
   value arrives today, but the schedule service has no explicit `all_day`
   column, so an event stored as exactly midnight with a time component reads
   as a timed 12 AM event. Adding the column was out of scope.

5. **`systems.down` was not extended.** *(still open)* An unreachable schedule
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
