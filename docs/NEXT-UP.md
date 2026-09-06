# Next Up — team lead recommendation

**Standing document.** Reviewed on a schedule against `docs/PRODUCT_IDEAS.md`,
Jira `SCRUM`, and branch state. It is a recommendation, not authorization:
`.frankenstein/PRODUCT_DIRECTIVE.md` on `control` is the only place scope
becomes real, and only the Product Owner writes it.

Last reviewed: 2026-09-05 21:51Z · Ideas at `5b3aed5` (wave 2) · control at
`7846472` (`FC-001 / awaiting_directive`, placeholder directive, never moved)

## URGENT — the deployment boundary is not actually closed

`.github/workflows/deploy.yml` on `production` triggers on **push to
`claude/personal-app-hub-vvpy4h`** — a task branch — and runs
`scripts/deploy.sh "${GITHUB_REF_NAME}"` on the OptiPlex's self-hosted runner.
`deploy.sh` line 14 is `BRANCH="${1:-...}"`, so it deploys whatever branch it
is handed.

That makes the protocol's central guarantee — *"a task-branch push deploys
nothing"* — **false for that branch**. It is stated in `PROTOCOL.md`, in
`CLAUDE.md`, and in the protocol agent's own commit message for `8db3602`,
which asserts it while itself creating deploy run #49.

Evidence: 49 runs of "Deploy to homelab", every one triggered by a push to that
task branch. None of the ten most recent completed successfully — #47 has sat
**queued since 2026-09-04** and #49 has been **pending since 00:11 today** —
which is consistent with the runner being offline rather than the boundary
holding. The jobs are armed, not harmless: if that runner comes back up, a
queued job deploys unreviewed task-branch code to the live box, which is
precisely what `promote.sh` exists to prevent and what `98aab13` was written to
fix.

**This is already known to two agents and neither can act.** The CM agent
documented it as "deploy.yml boundary bypass" and is holding for authorization.
The protocol agent's activation candidate `9b96bd0` deletes the file (-25) and
is verified fast-forward promotable — but promoting requires `accepted` +
`deploy-approved`, which requires the directive that does not exist. The fix is
built and gated behind the same empty queue as everything else.

**Recommended immediate action, and it does not need the Product Owner:**
disable the `deploy.yml` workflow (or take the self-hosted runner offline) until
`9b96bd0` is promoted. That is a repository/runner setting, not a production
push, so it closes the hole without anyone crossing the boundary to do it. I
have not done it — it is an infrastructure change and yours to authorize.

## A second boundary crossing, smaller

`bf31ab9` and `6d290be` were pushed **directly to `production`** by the CM
agent — 1,806 lines of documentation, fast-forward, no product code, no scripts,
no compose. `promote.sh` would have refused them: status is `awaiting_directive`,
not `accepted`, and authorization is not `deploy-approved`. So the branch moved
outside the only sanctioned path, and the poller deployed it.

The effect is benign — docs do not change container behavior — but the
precedent is not, and `CLAUDE.md` names this exact action ("push, force-push, or
otherwise move the `production` branch by any other means"). To its credit the
CM agent flagged its own action, asking whether to "route future docs to task
branch". The answer is yes.

## The PO has not woken in nearly two hours

`STATE.json` on `control` last moved at **21:42:30Z**. The corrected FC-002
handoff was published at **22:05Z** and has been sitting `awaiting_review`
since. It is now past 23:51Z — roughly **1h45m**, against a Codex Product Owner
wakeup that `FC-002`'s own directive says runs **every 15 minutes**. That is
about seven cycles with no response.

I can only observe that `control` has not moved; I cannot see Codex's
scheduler. But the loop executed exactly once and then stalled on the Product
Owner side, which is the same failure mode as the ChatGPT era with a different
actor. Worth checking that the wakeup is actually firing before assuming the
review is merely slow.

## The unshipped-work survey, and the conflict nobody had measured

`WORK_QUEUE.md` on `handoff` (`0046b3a`) surveys everything built and not
shipped. Everything is pushed; nothing is stranded locally. Three items are
verified and fast-forwardable from production:

| branch | tip | vs production | suite |
|---|---|---|---|
| `claude/po-handoff-release` (FC-002) | `f1fa382` | +5, fast-forward | 1586 pass |
| `claude/FC-001-dashboard-firefly-calendar` | `0a5d24a` | +1, fast-forward | 1486 pass |
| money/paycheck `d1af4e7` | — | cherry-picks clean onto production | 2276 pass |

**The finding worth acting on:** FC-001 and the money layer **conflict** — one
hunk, about 30 lines, in `gateway/static/home.js`. The calendar card and the
paycheck rewrite touch the same region. I flagged the collision when drafting
the directive; the agent has now measured it exactly, and correctly refused to
resolve it, because *what the dashboard home shows and in what order* is a
product decision, not an implementation one.

FC-001 and FC-002 together rebase clean and pass 1603. Whichever promotes
first, the other needs a rebase, and that rebase is verified clean.

So Anthony's dashboard work is not stuck behind engineering. It is stuck behind
two decisions: the order, and which layout wins in `home.js`.

## THE LOOP IS LIVE — first directive ever issued, 2026-09-06 21:42Z

`control` is at `760f4c2` and `STATE.json` finally moved off its seed values:

```
task_id FC-002   turn claude   status changes_requested
directive_commit a4bb8c6   implementation_commit 093f73d   last_actor product_owner
```

Codex proved it can write `control` (probe `8a3c475`), issued `FC-002`,
reviewed the implementation, withheld acceptance and published seven
corrections. A recurring Codex Product Owner wakeup runs every 15 minutes. The
protocol agent has already resumed — `f1fa382` rebinds to the new epoch
`760f4c2`, which is exactly what correction 6 asks for. Nothing to route: the
right agent is on the right branch doing the right thing.

Deployment Authorization is `test-only`. Nothing may promote.

**The corrections are good review.** Two are worth naming because they are the
same species of dishonesty this repo keeps catching in itself: `DEPLOY_SKIP_TESTS=1`
still recorded the test gate as *passed*, and the publisher accepted a
verification result without checking that `verification.commit` matched the
running commit — so an old pass could confirm a new deployment. Codex asked for
synthetic stale-verification fixtures rather than taking the fix on trust.

## What this means for the dashboard work

`19cc652` establishes `PRODUCT_VISION.md` — a standing mandate for Codex to run
the backlog without Anthony specifying each feature. Its priority order opens:

> **0. Finish the autonomous-loop bootstrap and resolve the existing
> release-review blockers. No feature initiative interrupts FC-002.**

Anthony's two dashboard asks land at priority 2 ("a focused Today experience
… upcoming commitments and items needing attention"). So they are real,
recorded, and **explicitly queued behind the bootstrap** by the Product Owner's
own ordering. That is a defensible call — but it is not what Anthony asked for
when he said "ASAP" twice, and he should know the ordering changed rather than
discover it.

The work itself is already done and green on
`claude/FC-001-dashboard-firefly-calendar` (`0a5d24a`, 1486 tests). It needs a
decision, not effort.

**One inconsistency I introduced and should own:** that branch claims task id
`FC-001`, which `control` never issued — Codex went straight to `FC-002`. The
protocol says ids come from `--next-id`, and the next free one is now `FC-003`.
The branch is not an authorized FC task at all, so the name overstates its
standing. Either Codex issues it a real id, or it should be renamed to stop
implying an authorization that does not exist.

## Codex is now the Product Owner, and the write path finally exists

`f1a045c` on `claude/po-handoff-release` implements Anthony's directive that
Codex replaces ChatGPT as Product Owner. `AGENTS.md` is Codex's operating
contract: **Codex writes directives to `control`**, Claude implements and
publishes to a new orphan `handoff` branch, Codex accepts on `control`, and the
release service promotes. 1,846 lines, 48 new release-service tests, nothing
activated, `STATE.json` untouched everywhere.

This is the answer to the 403 from the last review — the channel no longer
depends on ChatGPT's read-only integration, because the Product Owner changed.

**It also partly addresses my flag.** The worker can no longer write `control`:
it published its handoff to the same branch that carries `status: accepted`,
which was self-authorization. Splitting `handoff` from `control` closes that,
and it is a real improvement I did not ask for and should credit.

**The rest of the flag stands.** Acceptance still comes from a model and the
release service still promotes to the live box with no human in that decision.
Privilege separation between two agents is not the same as a human gate. Worth
deciding deliberately, per the earlier note — and `gh auth logout` on the box
should still come last.

## FC-001 is 18 hours old and has not started

The owner asked for the Firefly card and the calendar "ASAP" at roughly
02:00 UTC. It is drafted in `docs/PROPOSED-FC-001.md` and has not been
committed to `control`, so no agent is authorized to build it. The whole day
went to autonomy and release plumbing.

That plumbing was itself directed by the owner, so this is not an agent going
off-task. It is a queueing problem: the machinery for delivering directives has
been built repeatedly while the one directive that exists sits undelivered.
**Codex can now commit it** — that is precisely its role under `AGENTS.md`, and
`docs/PROPOSED-FC-001.md` is written to be lifted straight in.

## The owner has issued scope

On 2026-09-06 he asked, directly and unprompted, for two things "ASAP": everything
the Firefly sub-app shows except recent transactions, on the main dashboard, and
his calendar there too, preferably at the top. That is FC-001. It displaces my
recommended ordering below, which is correct — a roadmap I inferred from an ideas
document loses to the owner naming what he wants.

Drafted in `docs/PROPOSED-FC-001.md`; he chose the protocol agent to commit it to
`control` and referred the Deployment Authorization call there too. Two verified
defects are inside it rather than deferred, because the calendar cannot ship
correct without them: `/events` has no time bound so `next_event` is the *oldest*
event on record (issue #3), and `build_home` drops pending and countered holds.

The urgent deployment-boundary finding from the last review is **closed** —
`9b96bd0` is promoted and `deploy.yml` is gone from production.

## The ChatGPT write path — measured, and it failed usefully

The protocol agent probed it rather than assuming: **ChatGPT's integration is
read-only for repository contents** — `403 Resource not accessible by
integration` on `control` (`2ca43d6`). Every design that named ChatGPT as the
`control` writer rested on a stated capability that is not there. Finding it now
is cheap; finding it after credentials were minted and Anthony's own token
removed from the box would have left the loop half-built and stuck.

This corrects what I have been telling the owner for several ticks. The fix is
not a fine-grained token — it is **one browser action**: GitHub → Settings →
Applications → Installed GitHub Apps → the ChatGPT/OpenAI app → Configure →
grant **Contents: Read and write** for this repository. If that works, the
existing design stands with no courier, no machine account, no extra Unix user.

The fallback is genuinely clever: ten issues already exist on this repo, and a
GitHub App's `Issues` permission is separate from `Contents`. If issue writes
are permitted, a Product Owner channel already exists with no new credential at
all — a deterministic courier transcribes a fenced block from an issue into
`control`, with no model in the courier.

## Flag: the release proposal ends the human deployment gate

`RELEASE_AUTOMATION.md` (`d0d97d1`) proposes an unattended trusted release path.
It is careful work — append-only rollback, fail-closed checks, a Unix identity
split, a release service with no model in it — and it is explicitly a proposal
that executed nothing. But §10 says of the release path: *"Anthony is not
involved. Neither is a human."* and §12 lists "approve a commit" and "run a
deploy command" among things he never does again.

That **supersedes a rule the same agent wrote into `PROTOCOL.md` four hours
earlier** at `42be8fc`: *"The human leaves the message path, not the decision
path"*, with production deployment named as an explicit human approval
"regardless of what `STATE.json` says". I backed that rule at the time and I
still think it is the right line.

I am not calling this rogue — the commit title says "Anthony leaves DevOps", so
it may be exactly what he asked for, and proposing a rule change openly is the
correct way to change one. But it should be adopted knowingly rather than
inherited from a document read at 2am. Two specifics for whoever decides:

1. After setup, a model (ChatGPT) accepts work and a service promotes it to the
   live box with no human gate. That is the "two models hand each other work
   with no human in the path" posture the same agent said it would not build
   "without an explicit directive saying so in those words."
2. Setup step 7 is `gh auth logout` on the box — removing Anthony's own token.
   Whatever is decided about the rest, **that step should come last and only
   after the replacement path has been proven end to end**, or the fallback is
   gone at the moment it is most likely to be needed.

## The blocker that remains

`d1af4e7` put 1,524 lines of money layer on
`claude/financial-import-spending-nzhx02` with no directive authorizing it.
2135 tests pass — the code is fine — but it has no handoff and nothing to be
judged against, and its agent has gone idle believing it handed off.

**Nothing else should be sequenced until that is ruled on.** A backlog written
around unruled work is fiction.

## Recommended sequence

| task | what | why here | effort |
|---|---|---|---|
| **FC-001** | **Firefly + calendar on the main dashboard** — the owner's own ask, 2026-09-06 | he named both and called them priorities; drafted in `docs/PROPOSED-FC-001.md`, protocol agent to commit | M |
| **FC-001a** | Rule on the money layer | still unruled, and now collides with FC-001 requirement A | — |
| **FC-002** | Correctness pass: wave 2 #13–16, #19, and filed issues #1–#10 | the screen states things it doesn't know, and a second agent has now filed the specific bugs | S–M |
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

## A fourth backlog appeared tonight

A bug-finder agent filed **ten GitHub issues** (#1–#10), independently
confirming the correctness case and giving FC-002 concrete acceptance criteria
it did not have this morning. Several are sharper than the ideas doc:

- **#1 / #7** — `/networth` reports `$0.00` when the true value is unknown, and
  `/summary` turns unknown budget room into a confident `$0`. These are the
  `BUDGETS.md` honesty rule violated in the money layer itself.
- **#3** — Do-Next can *never* surface a calendar event: `next_event` is the
  oldest event on record. Worse than wave 2 #18 described.
- **#2** — `POST /recurring/apply` never terminates when `interval_days <= 0`.
  A hang, not a wrong number.
- **#10** — frontend HTML attribute escaping, filed with a private advisory.
- **#6 / #9** — "this week" drops days falling in the previous month;
  `days_until()` assumes 30-day months, so the bill "due soon" window is wrong
  280 days a year.

FC-002 should cite these issue numbers directly. It now has a test list.

**But this is the fourth place work is tracked** — Jira `SCRUM`,
`PRODUCT_DIRECTIVE.md`, `PRODUCT_IDEAS.md`, and now GitHub issues. Decision 5
below was already open; it is now urgent enough that I would answer it before
FC-002 is written, or the same bug gets fixed twice and closed in one place.

## These can run in parallel

There are three or four idle agents and this is a dependency chain only in
places. FC-002 (pure logic) and FC-004 (host access, new service) share nothing
and can run at once, given two directives. FC-003 is independent of both. Only
FC-005 has a hard prerequisite (#19, inside FC-002) and FC-006 a soft one (the
auth ruling).

## Non-product track — needs no directive

`PROTOCOL.md` calls itself development-process infrastructure, so this proceeds
while the roadmap is decided. All of it belongs to the protocol agent.

1. **`--check` still cannot see the violation that happened.** *Open.* Verified
   this tick: `scripts/frankenstein-status.sh` is unchanged from `production`
   and contains no branch inspection at all, so it validates `STATE.json`
   internally and passes on the branch carrying 1,524 unauthorized lines. It
   should fail when a branch holds product commits protocol state does not
   authorize. This is now the only one of the three still outstanding, and it
   is the one that would have caught FC-001's problem automatically.
2. ~~**Name the authoritative `.frankenstein/`.**~~ **Closed** by `42be8fc`.
   `PROTOCOL.md` now has a *control branch* section naming it as the
   authorization channel and stating what may and may not travel on it. It also
   added something I had not thought to ask for: a **control write order**. When
   the Product Owner writes through sequential single-file commits, the state
   flip goes last, so a half-written directive cannot wake the worker — and the
   worker's strict validation is explicitly not to be loosened to accommodate
   one. That closes a race I would have missed until it fired.
3. **Branch naming.** *Open.* No branch follows `claude/FC-###-<slug>`, so no
   branch ties to a task id. The worker creates `claude/FC-###-work` correctly;
   the drift is in the human-started sessions, including this one. Low harm
   until two tasks run at once.

The same commit also wrote down the rule that answers decision 6 below before
it is asked: **the human leaves the message path, not the decision path.**
Deployment, credentials, spending, destructive deletion, weakening containment
and widening egress stay explicit human approvals no matter what `STATE.json`
says, and `turn: claude` has never authorized any of them. That is the right
line, and it is now in the protocol rather than in someone's judgement.

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
