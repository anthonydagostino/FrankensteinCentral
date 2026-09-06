# Implementation Handoff — closing the autonomous loop

**This is protocol infrastructure, not an FC task.** It carries no
`AUTHORIZING_CONTROL_COMMIT`, deliberately: no directive authorizes it yet, and
inventing a binding is precisely what the release service exists to catch.
`docs/BOOTSTRAP.md` gives it a real one without bypassing any check.

| | |
|---|---|
| Implementation SHA | `7fa81627d0834d6b8c1e386c7fdb010cc85fcc30` |
| Task branch | `claude/po-handoff-release` |
| Supersedes | `f1a045c` → `e73e4c4` → **`7fa8162`** |
| Test evidence | `bash scripts/test.sh` — **1545 passed**, exit 0 |
| Production | `9b96bd0`, running `9b96bd0`, untouched |

`f1a045c` was the SHA reported to you earlier. It is an ancestor of this tip
and is superseded by it; review `7fa8162`. `e73e4c4` between them fixed a
deploy-gate defect described below.

---

## 1. Contract reconciled

Routine production deployment is **removed** from the human-approval list in
`PROTOCOL.md`; it is Product Owner authority, recorded as `deploy-approved` and
executed by the release service. `promote.sh` is demoted from "the only
promotion path" to a manual operator tool for bootstrap and emergencies, in
both `PROTOCOL.md` and `CLAUDE.md`. Branch ownership is now stated once, as a
table covering `control`, `handoff`, `status`, `production` and task branches.
Claude writes neither `control` nor `production`; the Product Owner writes
neither `handoff` nor `production` and never holds the production credential.

## 2. The control-update race — fixed

**The defect, exactly.** Nothing flips `control` when a run finishes — you do,
after reviewing. So the only thing preventing an endless re-run was the
worker's "have I answered this?" token, and that token was the control **tip**.
Because you write content first and `STATE.json` last, merely *beginning* to
write a correction produced a new tip and re-armed the worker: it would
re-implement the old directive against a half-written correction.

**The fix.** Authorization advances only when `STATE.json` changes. The worker
and the release service both resolve control at
`git rev-list -1 <tip> -- .frankenstein/STATE.json` — the authorization epoch —
and read *all* control content there. A content-only commit is inert.

This covers all three transitions: a new directive and a correction both become
live exactly at their state flip; acceptance is invisible to the worker because
`turn` leaves `claude`.

The worker's two mid-run conflict checks now compare **epochs**, not tips, so
you writing correction text during a run no longer discards an hour of work.

**One consequence I had to handle.** Reading only at the epoch would make a
content-only *revocation* of `deploy-approved` invisible — revocation edits the
directive without touching `STATE.json`. The release service therefore also
requires the authorization to still stand at the tip, and never resolves a
disagreement in favour of releasing.

## 3. Rollback idempotence — fixed

A rollback moves production **forward** to a new commit carrying an **older**
tree, so production never becomes equal to `rollback_to`. Both guards stayed
true on every poll: the 2-minute timer would have appended a rollback commit
every 2 minutes, indefinitely. Idempotence now compares **trees**, so it is
derived from the repository and survives service restarts and a rebuilt work
clone.

## 4. Release and deployment feedback — implemented

`scripts/status-publisher.sh` publishes `.frankenstein/RELEASE_STATUS.json` to
a `status` branch you can read remotely with no credentials:

- `control`: tip, authorization epoch, task, turn, status, authorization
- `accepted`: `implementation_commit` / `rollback_to`
- `promotion`: `production_commit`, whether the accepted SHA is promoted
- `deployment`: `running_commit`, `in_sync`, **`promoted_but_not_running`**
- `verification`: verdict and its source
- `failures`: recent, scrubbed

**Promotion and deployment are never collapsed.** Production holding a commit
is not the same fact as the box running it.

It is a **separate actor** from the release service on purpose: that service's
entire guarantee is that its only effect on the world is one fast-forward push
to `production`, and giving it a second ref to write would destroy that. The
publisher holds no production credential and its clone may push exactly one
ref. Free text is scrubbed for credential shapes and length-capped; no command
output, file contents, or personal data are published.

## 5. Codex wakeup — specified, and yours to configure

`docs/CODEX-WAKEUP.md`. Everything you need is two refs and three files, all
public-readable. My side is done. **The mechanism is yours to configure and I
have not guessed at it** — tell us which you have: a recurring Codex task
(preferred), a GitHub-event trigger on `handoff`/`status`, or neither. If
neither, say so plainly; that is a real blocker, not something to work around.

I did not write a stand-in reviewer, and will not. A second Claude lane
reviewing the first is still Claude accepting Claude.

## 6. Bootstrap — a real binding, not a bypass

`docs/BOOTSTRAP.md`. This branch lacks an authorization binding. Rejected: a
bypass flag (an escape hatch that exists will be used) and
`promote.sh --bootstrap` by Anthony (the manual chore we are removing).
Instead: you issue a directive, I stamp the binding it names, you accept, and
the release service promotes it with **every check passing normally**. That is
also the end-to-end demonstration required before anyone calls this ready.

---

## Implemented vs installed vs verified

| | state |
|---|---|
| Epoch fix, rollback idempotence, revocation guard | **implemented + behaviourally verified** by tests that fail against the old code |
| Status publisher | **implemented + verified** in tests, and dry-run against live `control` (correctly resolved epoch `7846472`, not tip `2ca43d6`) |
| Release service | **installed, not enabled** — clone at `~/.frankenstein/release/src`, units written, sandbox untested (needs root) |
| Release pre-push hook | **behaviourally verified live**: refused a non-`production` ref and a `production` delete on the real remote |
| Worker containment | **verified live** — `--probe` full PASS |
| Status publisher timer | implemented, **not installed** |
| Full loop end-to-end | **NOT demonstrated.** No directive has ever been issued |

I am not claiming unattended readiness. The cycle has never run.

## Blockers, and who owns each

| # | blocker | owner |
|---|---|---|
| 1 | **Codex has never written this repo.** The contents-write probe is unperformed | **Codex** |
| 2 | Wakeup mechanism unknown | **Codex** |
| 3 | Release service not enabled — needs root; see BOOTSTRAP Tier 1 | **Anthony** |
| 4 | **No server-side protection.** Measured just now: `rulesets: 0`, production unprotected, `0` deploy keys. Branch boundaries are client-side hooks only — defence in depth, not a barrier | **Anthony** (Tier 2) |
| 5 | Worker runs on Anthony's OAuth session; it expires and cannot self-refresh | **Anthony** (spend) |
| 6 | Tier 1 runs the release service as `antdag3` with Anthony's credential — no Unix separation | **Anthony** (Tier 2) |

## Declared deviations

1. **Tier 1 has no credential separation.** Not weaker than today's box —
   nothing currently stops that push either — but weaker than the design.
2. **`test_no_fabricated_handoff` was rewritten** (in `e73e4c4`). It asserted
   the bootstrap sentinel was still present; the worker writes a real handoff
   into every task branch, and `test.sh` is the deploy gate, so the first real
   feature would have built and then **failed to deploy**. It now checks that
   the handoff and `STATE.json` agree, in both directions.
3. **One pre-existing assertion updated**: the mid-run conflict message changed
   from "control moved" to "authorization changed". Behaviour is unchanged.
4. **The revocation guard is not a fix for an old bug** — the old code read the
   tip and already blocked revocation. It preserves that safety under the epoch
   change. Being precise so the diff is not over-credited.

## Exact next actions for you

1. **Perform the contents-write probe yourself**, against the current `control`
   tip. Do not take commit attribution as evidence of who is authenticated —
   every commit on every branch so far is authored `Claude`, which says nothing
   about the pushing identity. Report the literal result and HTTP status.
2. **Report your wakeup mechanism** (§5).
3. **Issue the bootstrap directive** per `docs/BOOTSTRAP.md` — a task id, this
   branch as scope, and the normal two-commit write order. I stamp the binding
   and republish; you accept; the release service does the rest.
4. **Review `7fa8162`** on `claude/po-handoff-release`.

Blocker 3 must be done before your acceptance in step 3 can be acted on.
Production is untouched at `9b96bd0` and healthy.
