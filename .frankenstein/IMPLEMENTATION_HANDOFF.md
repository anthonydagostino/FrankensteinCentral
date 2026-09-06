# Implementation Handoff — Product Owner model change

**This is protocol infrastructure, not an FC task.** `FC-001` remains
unissued and `STATE.json` is untouched. Nothing here changes product code,
and nothing has been activated.

Directive: Anthony's "Codex replaces ChatGPT as Product Owner / orchestrator",
2026-09-06. Authorized scope was explicitly the changes requiring **no new
account credentials**.

## What changed, and why

Codex becomes Product Owner. That is a role change, but it lands as one
technical requirement: **the implementation worker must no longer be able to
write `control`.**

The old worker published its handoff by pushing `control` — the same branch
that carries `status: accepted`. Under a design where accepted state
authorizes an unattended release, that is self-authorization waiting to
happen. An agent that can write control can accept its own work.

So control became read-only to Claude, and a new `handoff` branch carries
implementation output. Everything downstream follows from that one split.

## 1. Worker: `control` → `handoff`

`scripts/claude-worker.sh`

- The publisher's control clone is **gone**. In its place a fresh handoff clone
  publishes to the orphan `handoff` branch, built on the previous handoff tip
  so the branch is append-only.
- The handoff carries four files: `STATE.json` (with `implementation_commit`
  stamped by the publisher), `IMPLEMENTATION_HANDOFF.md`,
  `AUTHORIZING_CONTROL_COMMIT` and `TASK_BRANCH`. The last two are the binding
  the release service re-derives.
- The `pre-push` hook now refuses `control` as well as
  `production`/`main`/`master`, in every trusted clone.
- The stage-2 concurrency token check survives, but as a **read-only**
  `ls-remote` comparison: there is no control clone to reset and no control
  push to race. Stage 1 is unchanged.
- The prompt now forbids writing control, marking work accepted or
  deploy-approved, and running `release-service.sh`.
- Everything else is untouched: sandboxing, the four invocations, egress
  allowlisting, the export bundle, the publisher's ancestry and state
  validation, and every fail-closed path.

### The gap this opened, and how it is closed

Control used to flip to `awaiting_review` when a run finished. It no longer
does — the Product Owner does that, after reviewing. **Nothing else would have
stopped the next poll re-running the same directive forever.**

The worker now reads `handoff`'s `AUTHORIZING_CONTROL_COMMIT` before starting
and no-ops if it already equals the control commit authorizing this run. The
token is server-side, so it survives losing host state, and the Product Owner
can read it. A new directive or a `changes_requested` is a new control commit,
which is what makes the next run legitimate.

This was not in the directive. It is a direct consequence of removing the
control write, and without it the change would have produced a loop.

## 2. `scripts/release-service.sh` (new, 323 lines)

Deterministic promotion. No model, no prompt, no product logic; it never
invokes Claude or Codex, and never runs `deploy.sh`, `promote.sh` or
`rollback.sh`. Its entire effect is one fast-forward push of one ref, or
nothing.

Ten conditions must all hold, evaluated against the control tip; any failure
changes nothing. Full list in `docs/RELEASE-SERVICE.md`. The two that carry
the design:

- **`last_actor == product_owner`** — the implementation worker cannot be the
  accepting actor even if it somehow reached control.
- **the implementation's `AUTHORIZING_CONTROL_COMMIT` must be an ancestor of
  the accepting control commit, with matching `task_id` and `directive_commit`**
  — this binds *directive → implementation → acceptance* into one chain, so an
  accepted SHA cannot be swapped for another commit that merely descends from
  production.

Rollback uses the same gate and stays append-only: the target must already be
an ancestor of production, and production moves *forward* to the older tree.
Force pushes and deletions are refused by a local hook and are never
expressible by the script.

Two exit dispositions, deliberately different: **NO-OP exit 0** when the state
legitimately does not authorize a release (the quiet common case), and
**REFUSED exit 1** when control says accepted but cannot be validated — loud,
because that state needs a human to look at it.

## 3. `AGENTS.md` (new)

The Product Owner's operating contract: roles, the branch map, the loop, the
exact `control` write format the release service will accept, and the four
things that still require Anthony. It states plainly that normal feature work
and normal releases require none of them.

## 4. Documentation

- `docs/RELEASE-SERVICE.md` — new; architecture, the ten conditions, the
  credential model, activation, and the ruleset dependency it does not hide.
- `docs/AUTONOMOUS-WORKER.md` — four branches, the handoff zone, the
  one-authorization-one-run token, corrected failure results.
- `.frankenstein/PROTOCOL.md` — the actor model, the two-way channel, handoff
  publication, acceptance/release separation, Product Owner rollback.
- `CLAUDE.md` — Claude never writes `control`, never pushes `production`.

## Tests

`bash scripts/test.sh` → **1526 passed** (1469 before; +57).

New regression coverage for exactly what the directive asked:

| # | property | where |
|---|---|---|
| 1 | worker cannot push `control` | hook refuses it behaviorally; no `git push` in source names it |
| 2 | worker cannot push `production` | hook refuses it; production unchanged after every run |
| 3 | worker can publish `handoff` | end-to-end run, branch present and readable |
| 4 | handoff names the exact implementation commit | `STATE.json` and the task branch tip agree |
| 5 | handoff binds to the exact control snapshot | `AUTHORIZING_CONTROL_COMMIT` equals the authorizing commit, on both branches |
| 6 | worker cannot accept its own work | eight invalid-state cases, none published, control unmoved |
| 7 | containment unchanged | the whole existing suite, green |

Plus: handoff is an orphan branch; the handoff branch appends rather than being
rewritten; an answered authorization does not re-run; a new control commit does
authorize a new run; and 48 release-service tests covering every refusal path,
both rollback directions, dry-run inertness and the structural guarantees.

Two failures during development were real, not test noise: the release service
exited 1 instead of no-op when `control` did not exist yet, and it signed
rollback commits with an Anthropic address. Both fixed.

## Deviations From Directive

1. **Added the handoff completion token** (§1 above). Not requested; without it
   removing the control write creates an infinite re-run loop.
2. **Release decisions are logged locally**, to
   `~/.frankenstein/release/releases.jsonl`, rather than to the `releases`
   branch sketched in `RELEASE_AUTOMATION.md` §1. Fewer moving parts and no
   second push target; the release itself is already visible in production
   history. Say the word if the branch is wanted.
3. **`ENABLED`/`DISABLED` kill switches added to the release service**, not
   requested, mirroring the worker. It makes accidental activation impossible.
4. **`rollback.sh` is kept**, now documented as host-side operation, since
   routine rollback moves to the release service.

## Not done, because it was explicitly excluded

No Unix users, machine accounts, tokens, deploy keys or rulesets were created.
Nothing was installed or enabled. `gh` is still logged in. `FC-001` is still
unissued. Production is untouched at `9b96bd0` and healthy.
