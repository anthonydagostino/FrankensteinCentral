# Next Up — team lead recommendation

**Standing document.** Reviewed on a schedule against `docs/PRODUCT_IDEAS.md`,
Jira `SCRUM`, and branch state. It is a recommendation, not authorization:
`.frankenstein/PRODUCT_DIRECTIVE.md` on `control` is the only place scope
becomes real, and only the Product Owner writes it.

Last reviewed: 2026-09-05 · Ideas reviewed at `0c3fd73` · control at `7846472`
(`FC-001 / awaiting_directive`, placeholder directive, never moved)

## The blocker before any of this

`d1af4e7` put 1,524 lines of money layer on
`claude/financial-import-spending-nzhx02` with no directive authorizing it.
2135 tests pass — the code is fine — but it has no handoff and nothing to be
judged against, and its agent has gone idle believing it handed off.

**Nothing else should be sequenced until that is ruled on**, because it is
already the largest uncommitted change in the repo and it silently widens the
blast radius of idea #11 (see below). A backlog written around unruled work is
fiction.

## Recommended sequence

| task | what | why here | effort |
|---|---|---|---|
| **FC-001** | Rule on the money layer | clears the only unruled work; it's built and green, so this is a review, not a build | — |
| **FC-002** | Idea #2 — infra card + restore-test age | the only item that can cost something unrecoverable | M |
| **FC-003** | Idea #5 — wire `/weekly-review` | finished, tested, has no front door; free | XS |
| **FC-004** | Idea #3 — widen Do-Next | small, pure logic, and it's how FC-002 and FC-005 become visible | S |
| **FC-005** | Idea #1 — job pipeline | highest stakes, ~80% of the plumbing exists | M |

### Where I differ from `PRODUCT_IDEAS.md`

The Idea Team picked **#2, #1, #3**. I agree on #2 leading and largely on the
shape, with two changes:

**#5 moves up, ahead of #1 and #3.** It is XS. `GET /weekly-review` is built,
tested, and referenced by nothing in `gateway/static/` — it is a finished
feature with no front door. Shipping a done thing before starting a new one is
almost always the right order, and it costs a day at most.

**#11 (auth) is underrated at Tier 3, and the money layer is why.** The audit's
"anyone on the LAN can open it — acceptable for home" was a fair call when the
page showed container health. `d1af4e7` just added paycheck and spending detail
to a page already showing net worth, balances, password-health metadata and the
inbox — and idea #1 would add every company you're interviewing with. That is
now a single unauthenticated page holding most of your financial and
professional life, on hardware about to move to new networks. I would not build
#1 before deciding #11. I am not proposing it as a task ahead of FC-002; I am
flagging that **FC-005 should carry an auth precondition** rather than being
sequenced innocently after it.

**Not disputed:** #6 (four task systems) is real but is a decision, not a build
— retiring `tasks` is XS and should ride along with whichever task touches the
registry. #9 (`/ask` with a model) needs an egress decision that is genuinely
the PO's and shouldn't be bundled into anything else.

## Non-product track — needs no directive

`PROTOCOL.md` calls itself development-process infrastructure, so this work is
outside product scope and can proceed while the roadmap is still being decided:

1. **`--check` cannot see the violation that happened.** It validates
   `STATE.json` internally and passes on the branch carrying 1,524 unauthorized
   lines. It should fail when a branch holds product commits that protocol
   state does not authorize. This is the fix that would have caught FC-001's
   problem automatically.
2. **Name the authoritative `.frankenstein/`.** `docs/AUTONOMOUS-WORKER.md` is
   unambiguous that `control` is the source of truth and the worker
   materializes it onto task branches. `PROTOCOL.md` never says so. The doc and
   the worker agree; the protocol is silent. Close that gap in `PROTOCOL.md`
   before a directive on `control` meets an agent reading its own copy.
3. **Branch naming.** No branch follows `claude/FC-###-<slug>`, so no branch
   ties to a task id. The worker already creates `claude/FC-###-work` correctly;
   the drift is in the human-started sessions.

Both 1 and 2 belong to the protocol agent — it owns `.frankenstein/` and
`scripts/`.

## What I need from the Product Owner

1. **The money layer**: retroactively scope it as FC-001 and have its agent
   write the handoff with the missing directive recorded under *Deviations From
   Directive* — or `blocked`, and rule on it deliberately. Either is fine;
   leaving it unruled is not.
2. **Confirm or reorder FC-002 → FC-005.**
3. **Deployment Authorization per task.** Default `none`. FC-002 likely needs
   `test-only` to read host disk and SMART.
4. **A ruling on #11 (auth)** before FC-005 is scoped, per the argument above.
5. **Jira or the repo as roadmap?** Seven `[CLAUDE]` subtasks sit in To Do while
   the repo says no task exists. If Jira is the roadmap, directives should cite
   issue keys; if the repo is, those subtasks should stop reading as a queue.

## How a decision reaches the team

Write it to the `control` branch — `.frankenstein/PRODUCT_DIRECTIVE.md` with
exactly one well-formed `Task ID: FC-###` line matching `STATE.json.task_id`,
and `STATE.json` set to `turn: claude` /
`status: ready_for_implementation`. That is the wake condition in
`docs/AUTONOMOUS-WORKER.md`; every other combination is a logged no-op. Until
the Product Owner can write that branch directly, a decision relayed in any
form works — a human commits it to `control` and the loop starts.
