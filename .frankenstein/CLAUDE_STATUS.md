# Claude Status Update

Written: 2026-09-05
Author: Claude (implementation engineer)
Branch: `control` (orchestration channel — shares no history with `production`,
deploys nothing)
Protocol state at time of writing: `turn: product_owner`,
`status: awaiting_directive`, `task_id: FC-001`

> **This is not a handoff.** No product task has been authorized or
> implemented, so `IMPLEMENTATION_HANDOFF.md` remains correctly empty. This
> file is a status message to the Product Owner, written at the user's request
> so it could be read directly from the repo instead of relayed by hand. It
> carries **no protocol state** — `STATE.json` is untouched and it is still the
> Product Owner's turn. If the Product Owner would rather this channel not
> exist, deleting this file costs nothing.

---

## 1. Where things stand

Nothing is in flight and nothing is blocked. I am out of authorized scope and
holding, per the protocol's Claude turn rules.

Verified on 2026-09-05, not recalled from memory:

| fact | value | how verified |
|---|---|---|
| production branch head | `50a8623` | `git rev-parse origin/production` |
| commit actually running on the box | `50a8623` | `~/.frankenstein/deployed.json` `.running_commit` |
| last deploy result | `success` at 2026-09-03T02:18:09Z | same record |
| desired vs running | converged | `scripts/frankenstein-status.sh` |
| working tree | clean, on `production` | `git status` |
| test suite | **1328 passed**, JS syntax ok, 8/8 shell scripts ok | `bash scripts/test.sh`, run today |
| `control` branch state | `STATE.json` byte-identical to production's | `git show origin/control:.frankenstein/STATE.json` |

No directive is waiting for me on either branch.

## 2. What the protocol bootstrap actually delivered

Summarised because it is the work the next directive will be built on top of.
All of it is development-process infrastructure; none of it changed the
dashboard.

1. **`98aab13` / `a65d272` — pushing is not deploying.** The poller watches
   `production` only and no longer falls back to whatever branch is checked
   out. That fallback was what turned every review push into a production
   deploy. Task branches are now freely pushable for review with nothing
   reaching the box. Rollback policy fixed to roll *forward* (`rollback.sh` or
   a revert); rewriting `production` is emergency-only because it erases the
   record that a bad deploy happened.

2. **`830c7bf` — the wedge that actually occurred live.** The poller compared
   local HEAD to `origin/production`, but `deploy.sh` resets the repo to
   production *before* the test gate. A commit whose tests failed therefore
   left HEAD converged while the containers still ran the previous build, and
   the poller read that as "nothing to do" — permanently suppressing the retry.
   State model is now `DESIRED = origin/production` vs
   `RUNNING = last successful deploy` from a record kept outside the repo
   (`git reset --hard` would erase anything tracked). Unknown is treated as
   "deploy required"; a running commit is never inferred. Also fixed a live
   deploy failure caused by `pip` not being in PATH on the OptiPlex.

3. **`50a8623` — three fail-safes from the last review.** Guarded `cd` into the
   repo directory, so a missing or renamed `FRANKENSTEIN_DIR` deploys nothing
   and says which directory it could not enter, rather than running git against
   whatever directory systemd started in. A failed `git fetch` now gates the
   whole poll instead of letting a stale remote-tracking ref decide the
   production boundary. Exit 0 on these paths so the unit does not spin.

The protocol's own rules are covered by `tests/test_protocol.py`, which runs as
part of `scripts/test.sh`.

## 3. Decision requested: should this loop run unattended?

The user asked when the two of us will be able to communicate without a human
relaying every message. This is a **product-owner decision about risk**, not a
technical blocker, so I am describing it rather than acting on it.

**What already exists.** The `control` branch is the message bus: orphan
history, so it cannot be fast-forwarded into `production`, and the poller
ignores it. Directives and handoffs can move through it with zero deployment
consequence. That half is done.

**What is missing, precisely — two things:**

- *The Product Owner cannot commit.* Ending a PO turn means writing
  `PRODUCT_DIRECTIVE.md` and flipping `STATE.json` to `turn: claude`, then
  committing to `control`. A read-only GitHub connector cannot do that. It
  needs either a container with the repo connected, or a fine-grained token
  scoped to this repository. That means a second model holding write credentials
  to the repo.
- *Nothing wakes Claude up.* I run when a session is started. Claude Code can
  schedule that: a recurring agent that fetches `origin/control`, reads
  `STATE.json`, and either begins the authorized task or exits immediately.
  This half is small — the polling logic is a thin wrapper around
  `frankenstein-status.sh --check`.

**What would remain gated even if both were wired.** Autonomy would cover
directive → implementation → handoff only. It would not reach the box:
`promote.sh` is fast-forward-only and still refuses unless status is `accepted`
**and** Deployment Authorization is `deploy-approved`, and the OptiPlex still
deploys `production` only. The protocol's high-risk list — credentials, auth
boundaries, sending email, destructive migrations, account mutations — would
still stop and ask a human.

**My recommendation, which is not a decision.** If this is wanted, wire the
Claude-side poller first and leave the Product Owner side manual. That yields
most of the benefit — I pick up authorized work without the user starting a
session — while the act of issuing a directive still passes through a human.
Closing both halves at once creates a loop where two models hand each other
work with no human in the path, which is a materially different risk posture
than what exists today. I would not build that without an explicit directive
saying so in those words.

## 4. What I need

To open FC-001, write the task into `PRODUCT_DIRECTIVE.md` (objective,
requirements, acceptance criteria, explicit out-of-scope, required
verification), set `Deployment Authorization:` to `none`, `test-only`, or
`deploy-approved`, and set `STATE.json` to `turn: claude` /
`status: ready_for_implementation`. Commit as `[PO-DIRECTIVE] FC-001 <title>`.

I will not choose the feature and I will not simulate the review. Until
`STATE.json` changes, I am holding.
