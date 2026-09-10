# DRAFT — proposed directive for FC-002

**This file is not a directive.** It is a draft prepared at the Product Owner's
request, sitting in `docs/` on a task branch — deliberately **not** under
`.frankenstein/`, which only the protocol agent owns. It authorizes nothing: `STATE.json` still
reads `turn: product_owner` / `status: awaiting_directive`, and
`PRODUCT_DIRECTIVE.md` is untouched.

Task id `FC-002` came from `bash scripts/frankenstein-status.sh --next-id`
(highest seen: FC-001), not from memory.

## Three decisions to make before issuing

1. **Deployment Authorization.** Proposed `test-only` below. Under `none` the
   work still gets implemented, tested and pushed for review — it just can't be
   verified against the live box. Set it deliberately; a missing or
   unrecognised value is treated as `none`.
2. **Does SCRUM-66 stay in?** Requirement 5 (the portfolio "Today" label) is the
   fifth child of the epic and is XS. It is the same class of defect as R1 and
   fits the theme, but it is the one item you could strike without weakening
   the pass. Strike R5 and its acceptance row together if you want the smaller
   bite.
3. **Requirement 3 is the big one.** R3 carries the test-coverage work and is
   most of the effort in this task. If you want FC-002 to land fast, moving R3
   to FC-003 leaves a genuinely small display-honesty task — but the gym bug
   keeps miscounting until it ships, and it corrupts the score the whole time.

## How to issue it

1. Copy everything below the line into `.frankenstein/PRODUCT_DIRECTIVE.md`
   (deleting its placeholder blockquote), editing freely — it is your file.
2. Set `STATE.json`: `task_id: "FC-002"`, `turn: "claude"`,
   `status: "ready_for_implementation"`, `last_actor: "product_owner"`,
   `updated_at: <current UTC>`.
3. Commit as `[PO-DIRECTIVE] FC-002 correctness pass on the home screen`, and
   set `directive_commit` to that SHA.
4. `bash scripts/frankenstein-status.sh --check` should exit 0.

Delete this draft file once the real directive exists — two copies of a
directive is exactly the kind of disagreement `PROTOCOL.md` says stops work.

---

# Product Directive

Task ID: FC-002
Status: ready_for_implementation
Priority: High
Deployment Authorization: test-only

## Objective

Make every figure and status the home screen displays either true or explicitly
unknown. Today the screen states four things it has not verified: it reports
holdings as unconfigured when a container merely blinked, calls fifteen services
healthy after checking two, credits an evening workout to the wrong day, and
scores a goal you never set as a goal you failed.

No new features, no new services, no visual redesign. When this is done, the
numbers on the screen can be taken at face value.

## Product Context

`docs/BUDGETS.md` sets the honesty rules for the money layer — zero and unknown
are different states, suppressed values are `null` rather than `0`, a partial
window is never presented as complete — and the money layer follows them well.
The rest of the dashboard does not, and the failures are not cosmetic:

- Acting on a false "not connected" message sends you looking for a
  configuration problem that does not exist.
- A health line that says "Systems healthy" after checking two of fifteen
  services is worse than no health line, because it is trusted.
- The gym miscount feeds the `fitness` score component (weight 20) and the gym
  rule in `_do_next`, so on a Sunday night after the gym the dashboard can tell
  you to go to the gym and dock your score for not having gone.
- The daily score sits in the header all day. A score that punishes you for not
  having set a goal is one you stop looking at, and the score is the mechanism
  the whole habit layer rests on.

Every later idea in `docs/PRODUCT_IDEAS.md` lands on this screen. Their value is
capped by whether it can be believed.

## Requirements

Numbered for reference in the handoff. Each is independently testable.

### R1 — "Unreachable" and "not configured" become distinct states

1.1 The fan-out used by `build_home()` must distinguish, per service, between
**reachable and answering**, **unreachable / errored**, and **reachable but not
configured**. `_get()` currently swallows every exception and returns `{}`
(`services/assistant/app/main.py:190`), which collapses the first two into the
third.

1.2 You may introduce a tagged fetch helper alongside `_get` rather than
changing all 54 existing call sites. Only the `build_home()` path must adopt it;
other callers may keep current behaviour but must not regress.

1.3 The Money card must not render "Firefly not connected — set
FIREFLY_URL/FIREFLY_TOKEN" when Firefly is configured but unreachable. The
Portfolio card must not render "No holdings yet. Add your stocks →" when
holdings exist and `stocks` is unreachable. Both must say the source could not
be reached, and show last-known-good figures with their timestamp where the
service provides them.

1.4 Follow the contract `gmail` already implements (`mode:
disconnected | live | error`, `sync_status: healthy | failed | never`) rather
than inventing a second vocabulary.

### R2 — The systems line reflects every registered service

2.1 The `systems` field in `/home` must be derived from the full registered
sub-app set, not the two-service list at `assistant/main.py:693`.

2.2 The footer must name the services that are down. It must never read
"● Systems healthy" while any registered sub-app is unreachable.

2.3 The gateway already probes all fifteen concurrently at `GET /api/health` and
returns per-service status plus each service's `/health` detail. Reuse that
result rather than adding a second probe path; where the aggregation happens is
an implementation choice.

### R3 — One clock

3.1 `fitness` must store visit timestamps as timezone-aware values.
`services/fitness/app/main.py:85` currently writes `datetime.utcnow().isoformat()`
— a naive string with no offset.

3.2 **Existing rows must keep their meaning.** The naive values already stored
are UTC; they must be read as UTC and converted to local, not reinterpreted as
local. State in the handoff whether this was done by migration or on read.

3.3 `core._gym()` must bucket visits by the user's local day (`LOCAL_TZ`), so a
visit logged at 21:00 local counts for that local day and that local week.

3.4 `core` and `assistant` must route all date logic through a single `_today()`
clock seam, and a test must assert no bare `date.today()` / `datetime.now()`
call reappears in those modules — the rule `docs/TESTING.md` already states and
`firefly` already follows.

3.5 Day- and week-bucketing and the study streak must be exercised by a calendar
sweep of at least two years, in the style of
`services/firefly/tests/test_date_windows.py`, not against today's date.
`conftest.py`'s `load_service_module` is the existing pattern for importing a
second service's `app` package into the same pytest run.

3.6 Audit the other naive `datetime.utcnow()` producers (`deals`, `finance`,
`networth`, `schedule`, `tasks`, and `assistant`'s `_now()`) and **list them in
the handoff with whether each one feeds day-bucketing**. Fixing the ones that do
not is out of scope for this task; identifying them is not.

### R4 — Unset is not failed

4.1 `compute_score()` must treat a `None` component as not-yet-tracked:
excluded from the calculation and renormalised over the remaining components,
which is what its docstring already claims and what the existing zero-weight
path already does. It currently coerces `None` to `0.0`.

4.2 A day with no Big 3 set must not score identically to a day with three set
and none done. A day with no nutrition rating must not score as poor nutrition.

4.3 The score payload must state what the score was out of — how many
components were tracked — and the UI must show it.

4.4 The component keyed `tasks` measures Big 3 completion, not open tasks. Make
the UI label say what it measures. Do not change what it measures; see Product
Owner Notes.

### R5 — The portfolio change is labelled with the session it belongs to

5.1 Both quote sources return daily bars (`stooq …&i=d`, Yahoo
`interval=1d`), so the computed change is a close-to-close move for the last
completed session. The service must return the date of the bar it used.

5.2 The card must not label that figure "Today" when the bar is not today's. It
must name the session — e.g. "Last session (Fri close)".

5.3 Do not switch quote providers or add a keyed intraday source in this task.

### R6 — Every regression test is verified to fail without its fix

For each defect above, confirm the new test fails when the original bug is
reintroduced, and say so in the handoff. `docs/TESTING.md`: "A regression test
that never fails is decoration."

## Acceptance Criteria

- [ ] With the `stocks` container stopped, the Portfolio card says the source is
      unreachable and does **not** invite you to add holdings.
- [ ] With `firefly` unreachable, the Money card says so and does **not** tell
      you to set `FIREFLY_URL` / `FIREFLY_TOKEN`.
- [ ] With any one registered sub-app stopped, the footer names it and does not
      read "Systems healthy".
- [ ] With all sub-apps up, the footer reads healthy.
- [ ] A gym visit logged at 21:00 America/New_York counts for that local day.
- [ ] A gym visit logged Sunday 21:00 local counts in that week, not the next.
- [ ] Gym visits already in the database still resolve to the same real-world
      day they happened on.
- [ ] A fresh day with nothing logged does not report a low score; it reports
      that nothing is tracked yet.
- [ ] Setting three Big 3 items and completing none scores lower than setting
      none at all.
- [ ] The score payload and the UI both state how many components it was out of.
- [ ] The portfolio change names the session it refers to on a weekend.
- [ ] A calendar sweep of ≥ 2 years passes for `core` day/week bucketing and the
      study streak.
- [ ] A test asserts no bare `date.today()` / `datetime.now()` in `core` and
      `assistant` outside the clock seam.
- [ ] `bash scripts/test.sh` passes, with the new tests counted.
- [ ] Each new regression test is confirmed to fail without its fix.
- [ ] `IMPLEMENTATION_HANDOFF.md` lists the naive-`utcnow` audit from R3.6.
- [ ] `IMPLEMENTATION_HANDOFF.md` has a `## Deviations From Directive` section
      that either lists deviations or says "No deviations" explicitly.

## Explicitly Out of Scope

Do not, in this task:

- add any new service (no `infra`, no `jobs`, no `dismissals` table);
- change what `_do_next()` recommends, or add money/resale/job rules to it;
- add `tasks` or `powerbuy` to the `build_home()` fan-out;
- wire any of the built-but-unreachable features (nudges, deadlines, weekly
  review, sleep, `move_threshold_pct`);
- change the money layer's suppression semantics — it is already correct;
- change what the `tasks` score component measures;
- switch stock quote providers or add a keyed market-data source;
- add authentication, or change how the gateway binds;
- restyle or re-lay-out the home screen beyond the state text these
  requirements change;
- touch `scripts/promote.sh`, `scripts/deploy.sh`, `scripts/autopull.sh`, or the
  `production` branch;
- fix the naive `utcnow()` producers that do not feed day-bucketing (R3.6 asks
  you to list them, not fix them).

## Verification Required

Evidence expected in `IMPLEMENTATION_HANDOFF.md`:

1. `bash scripts/test.sh` output, with before/after test counts.
2. The calendar-sweep test's case count.
3. For R1 and R2: the actual rendered text in each of the three states, captured
   with a sub-app stopped. Curl output of `/api/assistant/home` is acceptable;
   a screenshot is better.
4. For R3: the reasoning and the test showing a 21:00-local visit landing on the
   correct local day and week, plus what happened to existing rows.
5. For R4: the score payload for a day with nothing tracked, and for a day with
   three Big 3 items and none done.
6. For R6: confirmation, per defect, that the test fails without the fix.
7. Under `test-only`, non-production verification on the box is authorized:
   `bash scripts/verify.sh` output is welcome (it is safe to paste — it never
   prints secrets, tokens, or email bodies). **No promotion.**

## Product Owner Notes

- Source: ideas #13, #14, #15, #16 and #33 in `docs/PRODUCT_IDEAS.md`, tracked as
  SCRUM-58, SCRUM-63, SCRUM-64, SCRUM-65 and SCRUM-66 under epic SCRUM-57
  ("Dashboard: trust the numbers").
- These are filed as defects rather than features deliberately. They are
  departures from behaviour the code already intends — in two cases from
  behaviour a docstring or a project doc explicitly promises.
- **An open question, deferred on purpose.** `core` fetches the real open-task
  count from the `tasks` service on every request and never scores or displays
  it, while the component named `tasks` actually measures Big 3. Whether open
  tasks should contribute to the daily score is a product decision, not a
  correctness one. R4.4 only requires the label to stop lying. Raise it as its
  own task if you want it changed — and note it interacts with SCRUM-89 ("One
  task system, not four").
- Scope discipline matters more than usual here. This task exists to make the
  screen trustworthy; every hour spent adding to it is an hour the rest of the
  backlog waits. If something in scope turns out to be larger than it looks,
  say so in the handoff and set `status: blocked` rather than widening.
