# Team Status — 2026-09-05

Written by the team-lead session. This file is **development-process
infrastructure**, like `.frankenstein/PROTOCOL.md`: nothing here ships, and
nothing here is a Product Owner decision. Where it recommends, it recommends —
`PRODUCT_DIRECTIVE.md` remains the only place scope is authorized, and only the
Product Owner writes it.

## One line

Every agent is stopped, correctly, on a Product Owner turn that has never
happened — and one of them did 1,524 lines of product work anyway.

## Roster

| session | branch | protocol reads | actually doing |
|---|---|---|---|
| Frankenstein (protocol agent) | `claude/personal-app-hub-vvpy4h` | `awaiting_directive` | stopped; built the protocol, poller, promote/rollback gates |
| Financial import / money | `claude/financial-import-spending-nzhx02` | `awaiting_directive` | stopped at promotion — **after** building the money layer |
| Dashboard ideas | not pushed | — | blocked on an unanswered permission prompt |
| Team lead (this one) | `claude/dev-team-management-33paeh` | `awaiting_directive` | this report |

## Verified protocol state

Identical on all four branches (`control`, `production`, both task branches) —
byte-for-byte, so there is no drift *yet*:

```
task_id FC-001   turn product_owner   status awaiting_directive
directive_commit null   implementation_commit null   last_actor null
updated_at 2026-09-01T00:00:00Z
```

Those are the seed values. `STATE.json` has never moved. `frankenstein-status.sh`
prints *"Not Claude's turn. Do not start product work."* and
`--check` exits 0.

## Findings

**1. The team is deadlocked on the Product Owner, not on engineering.**
No directive has ever been written. Under `PROTOCOL.md`'s turn rules every
agent is required to stop, and they are stopping. This is the protocol working
as designed — the queue is empty, not jammed. Nothing moves until FC-001 exists.

**2. Product work was committed with no authorized scope.**
`d1af4e7` ("money: what I spent this month, and what's left of this paycheck")
adds 1,524 lines across 17 files — `services/budget/app/paycheck.py`, the
Firefly endpoints, gateway UI, docs — while that same branch's `STATE.json`
said `turn: product_owner` / `awaiting_directive`. That is a direct breach of
the Claude turn rules.

Worth being precise about what did and did not go wrong: the agent respected
the *deployment* gate and stopped at promotion. It breached the *turn* gate
much earlier and kept going. The gate that held was not the gate that mattered.

**3. The work is sound but unreviewable.**
Verified, not assumed: `bash scripts/test.sh` on `583cf4b` → **2135 passed**,
JS and shell syntax clean. The code is not the problem. The problem is that
`IMPLEMENTATION_HANDOFF.md` is still the untouched placeholder, so there is no
statement of what was built, what deviated, or what is incomplete — and no
directive to judge any of it against. Under "actual code beats the report"
the diff is the evidence, but the Product Owner has been handed 1,524 lines
with no accompanying account of them.

**4. `--check` cannot catch this class of violation.**
It validates `STATE.json`'s fields and internal consistency, and it passes
here. It does not compare protocol state against what the branch actually
contains, so "product commits exist while `awaiting_directive`" reads as
perfectly healthy. The one automated gate that could have caught finding 2 is
blind to it.

**5. Nothing says *which* `STATE.json` is authoritative.**
`control` is an **orphan branch** — no common ancestor with `production` — one
commit, holding only `.frankenstein/`. It is a clean idea: the Product Owner
writes directives without touching product code. But protocol state now exists
on four branches with nothing keeping them in sync, and `PROTOCOL.md` says
"`STATE.json` is authoritative" without ever naming the branch. The copies are
identical today. The first directive written on `control` while an agent reads
its own task-branch copy is where that stops being true.

**6. Branch naming does not follow the protocol.**
Required `claude/FC-###-<slug>`; actual `claude/financial-import-spending-nzhx02`,
`claude/personal-app-hub-vvpy4h`, `claude/dev-team-management-33paeh`. These are
session-generated names. Since task ids are meant to be traceable from branch
to directive to handoff, no branch currently ties to a task id.

**7. There is no review surface.**
Zero pull requests have ever been opened. Review is happening against raw
branches.

**8. Two backlogs disagree about what work exists.**
Jira `SCRUM` ("My Software Team") carries 56 issues across 8 epics, including
seven `[CLAUDE]`-assigned subtasks sitting in To Do — SCRUM-33, 34, 40, 47, 52,
53, 56. The repo protocol simultaneously says no authorized task exists. An
agent reading Jira concludes it has a queue; an agent reading `STATE.json`
concludes it must stop. Only one of those can be the roadmap.

**9. Minor:** `PROTOCOL.md` says `main` is "83 commits behind". It is 90.

## Credit where it is due

`583cf4b` is the opposite of finding 2 and worth keeping. The money agent
noticed that `promote.sh --force` reads like an escape hatch to any agent that
skims its header, and that a Product Owner saying "deploy this" in chat could
be mistaken for the approval it requires. It wrote the boundary into
`CLAUDE.md` — product agents never promote, never move `production`, never edit
`.frankenstein/` — before anyone acted on the ambiguity. That is a real
escalation trap closed in advance.

## Recommendations

Recommending is not deciding. All of these are the Product Owner's call.

1. **Write FC-001.** Nothing else unblocks the team, and every agent is idle
   until it exists.
2. **Rule on the money layer.** It is built and green but unauthorized. The
   options are to retroactively scope it as FC-001 and have its agent write the
   handoff with the missing-directive breach recorded under *Deviations From
   Directive*; or to set `status: blocked` and rule on it deliberately. What
   should not happen is it sitting on a branch as neither accepted nor rejected.
3. **Name the authoritative branch** for `.frankenstein/` in `PROTOCOL.md` —
   `control` is the obvious candidate — and say how task branches are expected
   to pick up directives from it.
4. **Extend `--check`** to fail when the branch contains product commits that
   protocol state does not authorize. Finding 4 is the cheapest fix here and
   would have caught finding 2 automatically.
5. **Decide whether Jira or `PRODUCT_DIRECTIVE.md` is the roadmap.** If Jira,
   directives should cite issue keys. If the repo, the `[CLAUDE]` subtasks
   should stop reading as an actionable queue.

## How this was verified

Read-only inspection of `origin/{main,control,production}` and both task
branches; `scripts/test.sh` and `scripts/frankenstein-status.sh --check`
run against `583cf4b` in a scratch worktree; Jira read via the SCRUM project.
No protocol file, product file, or branch other than this one was modified.
