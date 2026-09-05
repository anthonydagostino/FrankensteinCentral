# Next Up — team lead recommendation

**Standing document.** Reviewed on a schedule against `docs/PRODUCT_IDEAS.md`,
Jira `SCRUM`, and branch state. It is a recommendation, not authorization:
`.frankenstein/PRODUCT_DIRECTIVE.md` on `control` is the only place scope
becomes real, and only the Product Owner writes it.

Last reviewed: 2026-09-05 21:51Z · Ideas at `5b3aed5` (wave 2) · control at
`7846472` (`FC-001 / awaiting_directive`, placeholder directive, never moved)

## The blocker before any of this

`d1af4e7` put 1,524 lines of money layer on
`claude/financial-import-spending-nzhx02` with no directive authorizing it.
2135 tests pass — the code is fine — but it has no handoff and nothing to be
judged against, and its agent has gone idle believing it handed off.

**Nothing else should be sequenced until that is ruled on.** A backlog written
around unruled work is fiction.

## Recommended sequence

| task | what | why here | effort |
|---|---|---|---|
| **FC-001** | Rule on the money layer | clears the only unruled work; built and green, so this is a review, not a build | — |
| **FC-002** | Wave 2 #13–16 correctness pass, plus #19 | the screen currently states things it doesn't know; #19 is a two-line prerequisite for FC-005 | S |
| **FC-003** | Wave 2 #17 — unwired inventory + consumer test | nine already-built features reachable by nobody | M |
| **FC-004** | Wave 1 #2 — infra card + restore-test age | makes the unrecoverable risk visible | M |
| **FC-005** | Wave 1 #3 Do-Next widening + wave 2 #18 pending holds | small, pure logic, and how FC-003/FC-004 become visible | S |
| **FC-006** | Wave 1 #1 — job pipeline (auth precondition, #11) | highest stakes; #26 falls out nearly free | M |
| **FC-007** | Wave 2 #20 — cash runway | the only forward-looking number, pure composition | S |

## What changed this review, and why

Wave 2 read the service code line by line and found **correctness bugs, not
feature gaps**. That reorders things.

**The correctness pass moves ahead of the infra card — reversing my last
recommendation.** Two reasons, and the second is the one that actually decided
it:

1. These are live, daily, and cheap. A workout logged after 8pm EDT is credited
   to tomorrow and a Sunday-evening one to next week (`fitness` stores naive
   UTC), so the dashboard can dock your score for a gym trip you made and then
   tell you to go. An unset goal scores as `0.0` rather than dropping out. The
   footer says `● Systems healthy` after checking 2 of 15 services. A blinked
   container renders as "no holdings — add your stocks". Every one of those is a
   violation of an honesty standard **this repo already wrote down** in
   `BUDGETS.md` and `TESTING.md` and simply never applied outside the money
   layer. That makes them consistency fixes against an agreed rule, not new
   product decisions — which is the cheapest kind of thing to authorize.

2. **The infra card does not protect the vault.** It makes the gap visible. The
   thing that actually protects it is SCRUM-35 and SCRUM-36 — create the B2
   bucket with Object Lock, install and run the first backup — both `[YOU]`
   tasks sitting in To Do. I had FC-002 leading on "only unrecoverable risk"
   grounds; that was wrong on the mechanics. Sequencing a dashboard card first
   does not reduce the risk by one day. **Those two Jira tasks are not blocked
   by the protocol, by a directive, or by any agent, and should be done this
   week regardless of what the roadmap says.** That is the single highest-value
   action available right now and it is not an FC task at all.

The Idea Team reached the same ordering by a different route — that #13–16 is
the safer opening move because it is contained, testable, and adds no services.
I agree, and add: the first directive is also the **first end-to-end test of the
protocol loop itself**, which has never run once. Shaking that down on a
pure-logic task is worth more than shaking it down on one that needs host
access, read-only mounts and `test-only` deployment authorization.

**#17 subsumes what I previously called FC-003.** I had "wire `/weekly-review`"
as a standalone XS win. Wave 2 shows it is one of nine unwired items — the
attention feed `AUDIT.md` promised, the `deadlines` the assistant files on every
sync, sleep, two dead settings fields. The consumer test proposed alongside it
is the part that stops the pattern recurring. One directive for all nine beats
mine.

**#19 turned out to be a prerequisite I didn't know about.** `build_home` never
fetches `tasks` or `powerbuy`, so Do-Next is *structurally* incapable of
surfacing an open task or an expiring resale buy. Widening the rules without
that two-line fix would do nothing. It rides along in FC-002.

**Unchanged:** the auth argument from the last review still stands, and wave 2
strengthens it — #20 (runway) and #26 (interview prep with company notes) put
more of your financial and professional life on the same unauthenticated page.
Auth stays a precondition on FC-006, not a task jumping the queue.

## These can run in parallel

There are three or four idle agents and this is a dependency chain only in
places. FC-002 (pure logic) and FC-004 (host access, new service) share nothing
and can run at once, given two directives. FC-003 is independent of both. Only
FC-005 has a hard prerequisite (#19, inside FC-002) and FC-006 a soft one (the
auth ruling).

## Non-product track — needs no directive

`PROTOCOL.md` calls itself development-process infrastructure, so this can
proceed while the roadmap is decided. Both belong to the protocol agent.

1. **`--check` cannot see the violation that happened.** It validates
   `STATE.json` internally and passes on the branch carrying 1,524 unauthorized
   lines. It should fail when a branch holds product commits protocol state does
   not authorize.
2. **Name the authoritative `.frankenstein/` in `PROTOCOL.md`.**
   `docs/AUTONOMOUS-WORKER.md` is unambiguous that `control` is the source of
   truth and the worker materializes it onto task branches. `PROTOCOL.md` never
   says so. Close that gap before a directive on `control` meets an agent
   reading its own copy.
3. **Branch naming.** No branch follows `claude/FC-###-<slug>`, so no branch
   ties to a task id. The worker creates `claude/FC-###-work` correctly; the
   drift is in the human-started sessions.

## What I need from the Product Owner

1. **The money layer**: retroactively scope as FC-001 with the missing directive
   recorded under *Deviations From Directive* — or `blocked`, and rule
   deliberately. Leaving it unruled is not an option.
2. **Confirm or reorder FC-002 → FC-007.**
3. **Deployment Authorization per task.** Default `none`. FC-004 likely needs
   `test-only` to read host disk and SMART.
4. **A ruling on auth (#11)** before FC-006 is scoped.
5. **Jira or the repo as roadmap?** Seven `[CLAUDE]` subtasks sit in To Do while
   the repo says no task exists. If Jira is the roadmap, directives should cite
   issue keys.
6. **Should the loop run unattended?** Raised by the protocol agent in
   `.frankenstein/CLAUDE_STATUS.md` on `control`, and it needs an answer
   independent of the roadmap. Its recommendation is to wire the Claude-side
   poller and leave the Product Owner side manual. **I back that.** Closing both
   halves means two models handing each other work with no human in the path —
   a materially different risk posture, and not one to arrive at as a side
   effect of convenience. The asymmetry is the point: Claude picking up already
   authorized work is a scheduling change, whereas a model issuing the
   authorization is a governance change.

Note that none of the above blocks SCRUM-35/36. Those are human tasks and the
real fix for the one risk that can cost something permanently.

## How a decision reaches the team

The channel is now half-built. `control` carries
`.frankenstein/CLAUDE_STATUS.md` as of `1ff7ccb` — a status message that
deliberately touches no protocol state, so the Product Owner can read the
team's position straight from the repo instead of having it relayed.

**The read half works. The write half does not**, and the protocol agent has
named the blocker precisely: ending a Product Owner turn means committing to
`control`, and a read-only GitHub connector cannot commit. It needs a
fine-grained token scoped to this repository, or a container with the repo
connected. That is the whole of what "the PO connection is established" means,
and it is a credentials decision, not an engineering one.

Until then a decision relayed in any form works — a human commits it to
`control` and the loop starts. The directive must carry exactly one well-formed
`Task ID: FC-###` line matching `STATE.json.task_id`, with `STATE.json` set to
`turn: claude` / `status: ready_for_implementation`. That is the wake condition
in `docs/AUTONOMOUS-WORKER.md`; every other combination is a logged no-op.
