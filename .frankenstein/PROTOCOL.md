# FrankensteinCentral — Product Owner ↔ Claude Protocol

**Protocol version: 1**

This is **development-process infrastructure**, not a FrankensteinCentral
feature. Nothing here ships to users or affects the running dashboard.

Its purpose: move Product Owner decisions and implementation handoffs through
the repository instead of relying on a human to relay every message by hand.
The repo is the shared source of truth.

```
Product Owner writes directive
  → Claude reads directive
    → Claude implements + tests
      → Claude writes handoff
        → Product Owner reviews repo state + handoff
          → Product Owner accepts / requests changes / issues next directive
            → repeat
```

## The four files

| file | owner | purpose |
|---|---|---|
| `.frankenstein/PROTOCOL.md` | shared | these permanent rules |
| `.frankenstein/PRODUCT_DIRECTIVE.md` | **Product Owner** | the authorized scope of the current task |
| `.frankenstein/IMPLEMENTATION_HANDOFF.md` | **Claude** | what was built, tested, deviated, and still open |
| `.frankenstein/STATE.json` | shared | **authoritative** turn and status |

No protocol state lives anywhere else.

---

## Responsibilities

### Product Owner

- Owns product direction and the roadmap.
- Defines requirements and acceptance criteria.
- Approves or rejects work.
- Decides what happens next, and when.
- May inspect the implementation directly — diff, tests, running behavior.
- May request corrections.
- **Does not defer roadmap ownership to Claude.**

### Claude

- Implementation engineer.
- Executes **only** approved scope.
- May recommend technical and product considerations — recommending is not deciding.
- **Does not independently choose the next product feature.**
- Tests changes.
- Reports honestly, including failures and things not done.
- Records deviations explicitly.
- **Stops after the handoff** unless the directive explicitly authorizes continued work.

---

## STATE.json

```json
{
  "protocol_version": 1,
  "task_id": "FC-001",
  "turn": "product_owner",
  "status": "awaiting_directive",
  "directive_commit": null,
  "implementation_commit": null,
  "last_actor": null,
  "updated_at": "2026-09-01T00:00:00Z"
}
```

| field | meaning |
|---|---|
| `protocol_version` | schema version of this protocol (integer) |
| `task_id` | human-readable sequential id, `FC-###` |
| `turn` | whose move it is |
| `status` | where the task stands |
| `directive_commit` | SHA of the commit carrying the current directive, or `null` |
| `implementation_commit` | SHA of the implementation being reviewed, or `null` |
| `last_actor` | `product_owner`, `claude`, or `null` |
| `updated_at` | UTC ISO-8601, e.g. `2026-09-01T14:03:00Z` |

**Allowed `turn` values**

```
product_owner
claude
none
```

**Allowed `status` values**

```
awaiting_directive        no authorized task exists yet
ready_for_implementation  directive written, Claude may begin
implementing              Claude is mid-task
awaiting_review           Claude handed off; Product Owner's move
changes_requested         Product Owner wants corrections; Claude may resume
accepted                  work accepted; no task in flight
blocked                   work cannot proceed; Product Owner must resolve
```

This is a small state file, deliberately. It is not a workflow engine.

---

## Claude turn rules

**Before any FrankensteinCentral work**, Claude must:

1. Fetch/pull the latest repo state.
2. Read `.frankenstein/STATE.json`.
3. Read `.frankenstein/PRODUCT_DIRECTIVE.md`.
4. Verify it is Claude's turn.

Claude may begin implementation **only** when:

```
turn == "claude"
AND status in ("ready_for_implementation", "changes_requested")
```

Otherwise:

| situation | required behavior |
|---|---|
| `turn != "claude"` | **Do not start product work.** Say whose turn it is and stop. |
| `status == "accepted"` | **Do not invent a new task.** Wait for a directive. |
| `status == "blocked"` | Report the blocker and stop. |
| `status == "awaiting_directive"` | No authorized scope exists. Stop. |

Reading the repo, answering questions, and explaining existing behavior are
always allowed. The restriction is on **changing the product**.

### Start of work

When beginning an authorized task, update `STATE.json`:

```
turn = "claude"
status = "implementing"
last_actor = "claude"
updated_at = <current UTC>
```

Do not modify the Product Owner's directive.

### End of work (handoff)

When the approved scope is complete:

1. Run the required tests (`bash scripts/test.sh`).
2. Run any verification the directive requires.
3. Commit the implementation work.
4. Capture the implementation commit SHA.
5. Write/update `IMPLEMENTATION_HANDOFF.md`.
6. Update `STATE.json`:
   ```
   turn = "product_owner"
   status = "awaiting_review"
   implementation_commit = "<SHA>"
   last_actor = "claude"
   updated_at = <current UTC>
   ```
7. Commit the handoff + state update.
8. **Push the task branch.** This is always allowed, at every Deployment
   Authorization level, because a task-branch push deploys nothing. The
   Product Owner cannot review what was never pushed.
9. **Stop.** Do not start another feature, and do not promote to production —
   promotion happens only after acceptance, and only when authorized.

---

## Product Owner review flow

Documented here for completeness; Claude does not execute this side.

Review: `PRODUCT_DIRECTIVE.md`, `IMPLEMENTATION_HANDOFF.md`, `STATE.json`, the
implementation diff, the tests, and relevant docs. Then choose:

**A. Accept**

```
turn = "none"          status = "accepted"
```

or, when immediately issuing the next task: bump `task_id`, write the new
directive, and set

```
turn = "claude"        status = "ready_for_implementation"
```

**B. Request changes**

Put the correction scope in `PRODUCT_DIRECTIVE.md` (a clearly marked Product
Owner correction section is fine), then

```
turn = "claude"        status = "changes_requested"
```

**C. Block**

```
turn = "product_owner" status = "blocked"
```

with an explanation in the directive or review notes.

---

## The control branch: the messaging channel

The Product Owner and Claude exchange directives and handoffs through the
orphan `control` branch on GitHub. No human relays routine messages between
them.

| | |
|---|---|
| bus | the `control` branch — orphan history, carries `.frankenstein/` only |
| transport | GitHub |
| what travels here | directives, corrections, handoffs, protocol state |
| what never travels here | code, product changes, anything deployable |

`control` shares no history with `production`, so it cannot be fast-forwarded
into it, and the poller watches `production` only. A commit on `control`
therefore deploys nothing, by construction rather than by promise.

**The human leaves the message path, not the decision path.** These remain
explicit human approvals regardless of what `STATE.json` says:

- production deployment
- credential creation, rotation or disclosure
- actions that spend money
- destructive deletion of meaningful data
- destructive migrations
- weakening containment
- widening autonomous egress
- high-risk host changes
- irreversible operations

`turn: claude` authorizes implementation. It has never authorized any of the
above, and direct messaging does not change that.

### Control write order

`control` is the authorization channel, so a half-written directive must not
be able to start work. When the Product Owner writes through sequential
single-file commits, the state flip goes **last**:

**New directive**

1. commit `PRODUCT_DIRECTIVE.md` while `STATE.json` still says
   `turn: product_owner` — this commit cannot wake Claude
2. note that commit's SHA
3. commit `STATE.json` with `directive_commit = <that SHA>`,
   `turn = claude`, `status = ready_for_implementation`

**Changes requested**

1. commit the correction while state remains
   `product_owner` / `awaiting_review`
2. commit `STATE.json` with `turn = claude`,
   `status = changes_requested`

The intermediate commit never authorizes execution. Atomic multi-file commits
to `control` are welcome if they become available, but they are not required
for safety as long as this ordering holds — and the ordering is what the
worker's strict state validation assumes. That validation is not to be
loosened to accommodate a partially written directive.

---

## Commit conventions

| actor / event | prefix |
|---|---|
| Product Owner directive | `[PO-DIRECTIVE] FC-### <short title>` |
| Claude implementation | `[CLAUDE] FC-### <short description>` |
| Claude final handoff | `[CLAUDE-HANDOFF] FC-### ready for review` |
| Product Owner changes requested | `[PO-CHANGES] FC-### <short description>` |
| Product Owner acceptance | `[PO-ACCEPT] FC-### accepted` |

Commit names are a convenience for scanning history. **`STATE.json` remains
authoritative** — never infer protocol state from commit messages alone.

## Branch naming

| purpose | pattern | example |
|---|---|---|
| task / review branch | `claude/FC-###-<slug>` | `claude/FC-002-paycheck-money` |
| production | `production` | — |

`main` is **not** the production branch: it holds only the initial commit and
is 83 commits behind the working history, so pointing the box at it would
deploy an empty repo. `production` was created at the exact commit already
running, so introducing the boundary changed nothing about what was live.

## Task IDs

Sequential: `FC-001`, `FC-002`, `FC-003`, … Never guessed from memory.

```
bash scripts/frankenstein-status.sh --next-id
```

scans git history and the current protocol files for the highest `FC-###` and
proposes the next one. There is no task database.

---

## Principles

### Actual code beats the report

The implementation handoff is **explanatory metadata**. The repository diff,
the tests, and the running behavior are the authoritative evidence that
something was implemented.

Claude must never claim a feature is complete merely because the handoff
describes it as complete.

### Product Owner decisions are not Claude's to edit

Claude must never silently change a Product Owner decision to make
implementation easier or cheaper.

If a requirement appears impossible, unsafe, self-contradictory, or
substantially more expensive than it looks:

- do **not** rewrite the requirement,
- describe the problem in `IMPLEMENTATION_HANDOFF.md`,
- set `status = "blocked"` if it genuinely cannot proceed,
- hand control back.

Minor implementation choices inside an authorized scope remain Claude's
responsibility and do not need escalation.

### Deviations are declared, never buried

Any departure from the directive goes in the handoff's
`## Deviations From Directive` section, and for each one states: what changed,
why, whether behavior differs from the acceptance criteria, and whether there
is user or product impact. When there are none, the section must say
**"No deviations"** explicitly — silence is not an answer.

### No self-review, no simulated Product Owner

After setting `turn = product_owner` / `status = awaiting_review`, Claude
**stops**. Claude must not re-review its own work as if it were the Product
Owner, and must not write, predict, or act on what the Product Owner "would
probably say." Only a real Product Owner directive or state change begins the
next turn.

### Conflicts stop work

If `STATE.json`, `PRODUCT_DIRECTIVE.md`, and commit history disagree — a task
id that doesn't match, a status implying a commit that doesn't exist, a
directive for a different task than the state names — Claude does **not**
guess. Set/report `status = "blocked"`, explain the inconsistency, and stop for
Product Owner resolution.

### History is preserved

Git preserves revisions, so the current files may reflect only the current
task. That is fine, but:

- do not destroy the previous handoff before the next directive commit exists,
- do not reset `STATE.json` ambiguously,
- `task_id` must change for a new task,
- old task history must stay reconstructable from commits.

---

## Code review vs production deployment

**Pushing code to GitHub is not deploying it.** These are two separate events
with two separate gates, and the whole review loop depends on the distinction:

```
Claude implements
  → pushes a TASK BRANCH            (always allowed; deploys nothing)
    → Product Owner reviews the real diff, tests, and handoff
      → Product Owner accepts
        → if deployment is authorized, the commit is PROMOTED
          → production branch moves
            → the OptiPlex deploys it
```

| | branch | who moves it | effect |
|---|---|---|---|
| review | `claude/FC-###-<slug>` | Claude, freely | nothing runs; code is visible on GitHub |
| production | `production` | `scripts/promote.sh` only | the OptiPlex deploys within ~60s |

The poller (`scripts/autopull.sh`) watches **only** the production branch. It
never falls back to whatever branch happens to be checked out — that fallback
is exactly what made every review push a production deploy. If the production
branch is missing it deploys **nothing** and says so.

`scripts/promote.sh` is the only promotion path. It is fast-forward only, and
refuses unless protocol status is `accepted` **and** Deployment Authorization
is `deploy-approved`.

## Deployment authorization

`PRODUCT_DIRECTIVE.md` carries an explicit field:

```
Deployment Authorization: none | test-only | deploy-approved
```

| | implement | run tests | **push task branch** | non-prod verification | promote to production |
|---|---|---|---|---|---|
| `none` | yes (if turn permits) | yes | **yes** | no | **no** |
| `test-only` | yes | yes | **yes** | yes | **no** |
| `deploy-approved` | yes | yes | **yes** | yes | yes, within directive scope |

**Pushing a task branch is allowed under all three**, because a task-branch
push cannot deploy. GitHub review is therefore always possible, which is the
point of the whole arrangement.

A missing or unrecognized value is treated as `none`, and the handoff must say
so. Never assume a directive permits deployment.

## Acceptance is not deployment

`status: accepted` means **the work is good**. It does not mean "ship it now."
Deployment Authorization is a separate decision the Product Owner makes
independently, and either can come first:

- accepted + `none` → the work is approved and sits on its branch, unpromoted.
- accepted + `deploy-approved` → promotion may proceed.
- not accepted + `deploy-approved` → **no promotion.** Authorization does not
  excuse failing the acceptance criteria, and Claude may not promote work the
  Product Owner has not accepted.

Deployment state deliberately does **not** live in `STATE.json`: only the box
knows what actually ran. `implementation_commit` means "this was pushed for
review", never "this is running".

## Rollback: production moves forward

Production history is **append-only**. Rolling back means adding a new commit
whose tree is the known-good one — not rewinding the branch:

```
production:  A --- B --- C(bad) --- D(tree of B, "rollback")
```

```
bash scripts/rollback.sh --dry-run <good-sha>
bash scripts/rollback.sh <good-sha>
```

A plain `git revert` is equally acceptable for a single bad commit.
`scripts/rollback.sh` exists for multi-commit rollbacks, where "restore this
exact known-good tree" is clearer and safer than a chain of reverts. Either
way production moves forward, the box deploys the rollback like any other
change, and the bad deploy remains in the audit trail.

Rewriting the production branch (`--force-with-lease`) is **emergency recovery
only** and a high-risk action under this protocol: it requires explicit
approval, because it erases the record that the bad deploy happened. It is
never the default operational rollback.

## What is actually running

`scripts/deploy.sh` writes a record outside the repo (`~/.frankenstein/
deployed.json`, since `git reset --hard` would erase anything tracked):

```json
{"production_branch": "production", "running_commit": "<sha>",
 "last_attempt_commit": "<sha>", "last_attempt_at": "<utc>",
 "last_result": "success|tests_failed", "last_success_at": "<utc>"}
```

**Deployment state model.** The poller compares
`DESIRED = origin/production` against `RUNNING = deployed.json.running_commit`
(the last *successful* deploy) and deploys when they differ. Local git HEAD is
never consulted: `deploy.sh` resets the repo to production *before* the test
gate, so a commit whose tests failed leaves HEAD at production while the
containers still run the previous build. A missing, unparseable or null record
means **unknown**, which is treated as "deploy required" — a running commit is
never inferred.

`bash scripts/frankenstein-status.sh` prints the desired commit and, on the
box, the running commit and last deploy result — including when the last
attempt failed and the box is therefore still on an older commit. A failed
deploy leaves the previous build running: the test gate runs before containers
are touched.

## High-risk actions

`turn: claude` is **not** unlimited authority. This protocol does not by itself
authorize sensitive or destructive work. Stop and get explicit Product Owner /
user approval before:

- deleting important user data
- destructive database migrations
- changing authentication or authorization boundaries
- exposing services publicly
- rotating credentials
- modifying financial accounts
- sending email
- account mutations of any kind
- destructive infrastructure changes
- reimaging machines
- rewriting the production branch (`--force-with-lease`) instead of rolling
  forward with `scripts/rollback.sh` or a revert
- any irreversible operation

When in doubt, treat the action as high-risk and ask.

---

## Helper tooling

```
bash scripts/frankenstein-status.sh            # current turn/status/commits
bash scripts/frankenstein-status.sh --check    # validate STATE.json + consistency
bash scripts/frankenstein-status.sh --next-id  # propose the next FC-### id
bash scripts/promote.sh --dry-run              # what promotion would do
bash scripts/promote.sh                        # promote accepted+authorized work
```

`--check` exits non-zero on an invalid or inconsistent state, so it can gate
automation. It prints no secrets. The protocol's own tests live in
`tests/test_protocol.py` and run as part of `bash scripts/test.sh`.
