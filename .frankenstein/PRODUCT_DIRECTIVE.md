# Product Directive

Task ID: FC-008
Status: changes_requested
Priority: high
Deployment Authorization: none

## Objective

Replace the main dashboard's flat "Schedule" list with a genuine seven-day
weekly calendar: today plus the next six days, each day labelled with its day
of the week, its month, and an ordinal day of the month.

## Product Context

Anthony authorized this task directly, as Product Owner, on 2026-09-07.

This directive is recorded by Claude at Anthony's explicit instruction. It is
not a Codex-issued directive and is not a Claude-invented task. Two standing
positions are knowingly overridden by the owner, and are recorded here rather
than left for Codex to discover:

- `PRODUCT_VISION.md` priority 0 states "No feature initiative interrupts
  FC-002."
- The FC-002 directive states "No new product features" and lists new product
  work as explicitly out of scope.

**FC-002 is paused, not abandoned, and not accepted.** Its four open Codex P1
findings (readiness false positive, missing-verification clearing attention,
partial-Compose `running_commit`, malformed health entries) remain outstanding
and unreviewed. Its work is preserved on `claude/po-handoff-release` at
`f1fa382b0823100a3bc9520f104b4e793052093d`, and its directive text remains in
this file's git history at directive commit
`3c0b81d791cf41a67054b835941c7bceeeadff6e`. Codex retains acceptance authority
over FC-002 and may re-prioritize it ahead of this task.

The dashboard's current schedule card (`renderCalendar`, `gateway/static/home.js`)
renders upcoming events as one undifferentiated chronological list. It answers
"what is next" but not "what does my week look like", which is the vision's
stated question "What commitments, deadlines or conflicts are approaching?".

## Requirements

1. The schedule card on the main dashboard shows exactly seven day units:
   the current local day and the following six. Never a fixed Sunday–Saturday
   week, never a past day, never an eighth day.
2. Each day shows its weekday name, its month name, and its day of the month
   with a correct English ordinal suffix (1st, 2nd, 3rd, 4th … 11th, 12th,
   13th … 21st, 22nd, 23rd). The 11/12/13 exceptions must be correct.
3. The window rolls over at local midnight without a manual reload, and a
   window that spans two months labels the month boundary rather than hiding
   it.
4. Existing schedule behaviour is preserved, not regressed: `confirmed`,
   `pending` and `countered` holds all remain visible and visually
   distinguishable, and `countered` remains marked as needing Anthony's reply.
   Dropping non-confirmed holds was a previously fixed bug and must not return.
5. Approaching conflicts are surfaced: overlapping commitments on the same day
   are detectable at a glance.
6. Honest states, per vision principle 1. An empty day, an unreachable
   schedule service and a disconnected/unconfigured integration are three
   different states and must read differently. Absence of data is never
   rendered as an empty but healthy week.
7. Calm, fast and usable on phone and desktop, per vision principle 4. The
   seven-day layout must remain legible at narrow widths; it may change shape,
   but it may not require horizontal scrolling of the page.
8. Accessible: each day is reachable and announced with its full date, colour
   is never the sole carrier of status, and animated decoration honours
   `prefers-reduced-motion`.
9. Month-themed decoration is authorized and encouraged (for example pumpkins
   in October, snow in December). It is decoration only: it may not alter text
   contrast, obscure content, block interaction, or change what the data says.
   It must be disableable, and the setting must persist.
10. Date-dependent logic goes through the existing single clock seam and is
    tested across a calendar sweep, never against "today" — see
    `docs/TESTING.md`. Ordinals, month spans, DST days and leap days are part
    of the sweep.

## Acceptance Criteria

- Seven days render, the first is the current local day, the seventh is six
  days after today, verified at multiple synthetic clock values including a
  month boundary, a DST transition and 29 February.
- Ordinal suffixes are correct for all of days 1–31.
- `pending` and `countered` holds are present and distinguishable in the new
  layout; a regression test covers their survival.
- Empty, error and disconnected states are distinguishable in the rendered
  output and covered by tests.
- `bash scripts/test.sh` passes in full on the final tree.
- No change to money, budget, Firefly, Gmail or credential handling.

## Explicitly Out of Scope

Calendar write operations of any kind; new third-party integrations; new
credentials, accounts, spend or security-boundary changes; promotion or
deployment; changes to the FC-002 release-infrastructure work; editing the
paused FC-002 handoff.

## Verification Required

Full suite exit status and counts, the exact implementation SHA, the calendar
sweep's covered dates, and an explicit statement of which schedule states were
exercised.

## Product Owner Notes

Deployment Authorization is `none`: push the task branch for review, deploy
nothing. Codex retains acceptance authority and may reorder this against
FC-002. Claude implements and stops at the handoff; it does not accept this
work.

Repository is public (vision principle 6): any sample or fixture data must be
synthetic. No real commitments, names or account details.

## Codex review and correction — add5b2b

Codex now explicitly owns this correction under the standing product mandate. Continue the existing FC-008 weekly-calendar branch from add5b2b99f9f67c42824507a7237a404817882f0; preserve its work and current production ancestry. This correction is issued after the handoff branch's PROMOTE_REQUESTS.md reported the candidate ready for review. It is not authorization for another feature, bootstrap activation, promotion or deployment.

Independent review evidence:
- 44 assistant dashboard tests and all 5 JavaScript clock tests passed locally.
- GitHub Actions run 34078322229, job 101608680446 failed: 19 failed, 1457 passed, 37 skipped. Eighteen failures concern unavailable worker namespaces; one is test_mismatched_running_commit_reports_pending. A reported local green suite does not supersede the failed clean CI run.
- The candidate has no AUTHORIZING_CONTROL_COMMIT, retains FC-001 placeholder STATE.json, and has an empty implementation handoff. Canonical handoff still reports FC-002.
- schedule_state({"connected": false, "events": []}) independently returns ok, exactly like a healthy empty schedule. The renderer only distinguishes unreachable from normal, so requirement 6 remains incomplete.
- The decoration toggle clears the drifting layer only; dayCard still emits the month motif unconditionally. Requirement 9 asks that decoration be disableable.

Required correction:
1. Carry an honest schedule/integration state through the actual read-only data path and render healthy-empty, unreachable, disconnected/unconfigured, and unknown distinctly. The current events endpoint does not establish integration connection health; do not invent that evidence or infer connected from a nonempty wrapper object. Use existing connection metadata where available; where it cannot be established, say unknown rather than showing a healthy clear week. Preserve local commitments when an external integration is disconnected. Add backend and rendered-output regression coverage for these states.
2. Make Decor off suppress seasonal decorative motifs as well as animated glyphs, persist after refresh, and preserve status/date information. Exercise the real rendering path, including pending/countered labels, keyboard access, narrow layout and reduced motion. Provide synthetic visual verification at phone and desktop widths; no private calendar data in evidence.
3. Make the full CI gate pass on the exact bound candidate in a fresh Linux checkout. Bounded scope expansion: incorporate the necessary CI namespace-capability setup and hermetic deployment-status test corrections already reviewed in the bootstrap, with containment tests actually exercised. Do not use unsandboxed execution, widen skips to hide failures, or import unrelated release infrastructure/product changes. Report any remaining clean-checkout failure accurately.
4. Bind this task to the new control STATE.json epoch and its directive_commit, with matching FC-008 metadata. Publish the exact final SHA and completed canonical handoff, TASK_BRANCH and AUTHORIZING_CONTROL_COMMIT. Preserve the historical FC-002 handoff in git history; identify FC-008 clearly as the new handoff. Run the full suite after binding, then stop.
5. Keep the seven-day acceptance rule unambiguous: today plus six days. The previous wording about five days after tomorrow was inconsistent with that rule and is corrected above.

FC-002 remains paused and unaccepted; its latest review is PO_REVIEW_5c5d64f.md. No queued money/paycheck feature is authorized by this directive. No direct promotion, bypass, credential/account changes, spending, or host activation is authorized. Only Codex may accept; only the deterministic release service may promote after a separate valid approval.
