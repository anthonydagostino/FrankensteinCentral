# FC-008 — what it takes to get this live

Written by Claude at Anthony's request, 2026-09-07. Every mechanical claim
below was read out of the scripts or executed, not recalled.

Candidate: `ce713da` on `claude/FC-008-weekly-calendar` (tree byte-identical
to the reviewed `add5b2b`).

## The gates, in order

`scripts/promote.sh` is the only path onto `production`. It refuses unless
**all three** hold:

| gate | source of truth | now | who clears it |
|---|---|---|---|
| protocol status `accepted` | `.frankenstein/STATE.json` | `ready_for_implementation` | Codex, or Anthony as PO |
| `Deployment Authorization: deploy-approved` | `.frankenstein/PRODUCT_DIRECTIVE.md` | `none` | Anthony / Codex on `control` |
| fast-forward onto production | git | **satisfied** | — |

Run today, for the record:

```
$ bash scripts/promote.sh --dry-run ce713da
  Protocol status:       awaiting_directive          (need: accepted)
  Deployment auth:       none                        (need: deploy-approved)
REFUSED: protocol status is 'awaiting_directive', not 'accepted'
```

Fast-forward is confirmed: `origin/production` (`0a5d24a`) is an ancestor,
4 commits behind.

## A trap in that output

`promote.sh` and `frankenstein-status.sh` read `.frankenstein/STATE.json`
**from the working tree**, not from `control`. On a task branch — and on
`production` — that file is the neutral placeholder the protocol tests
require, which is why the dry run above reports `FC-001 /
awaiting_directive` rather than FC-008.

So whoever promotes must run it from a checkout where control's state is
materialised, e.g.:

```
git fetch origin control
git checkout origin/control -- .frankenstein/
bash scripts/promote.sh --dry-run ce713da     # re-check the gates
```

Otherwise the gate is being evaluated against a placeholder, and a `--force`
used to "get past" it would be skipping a check that was never really
consulted.

## Once both gates are open

```
bash scripts/promote.sh --dry-run ce713da   # confirm the fast-forward
bash scripts/promote.sh ce713da             # pushes to production
```

Then, on the OptiPlex, `scripts/autopull.sh` (polling `production` only)
sees desired != running and runs `scripts/deploy.sh`, which:

1. runs `scripts/test.sh` **before touching any container** — a failing suite
   aborts and leaves the previous build running;
2. brings the stack up with docker compose.

Expect it within ~60s of the push.

## Verifying it actually went live

```
bash scripts/frankenstein-status.sh    # desired vs running commit
bash scripts/verify.sh                 # live diagnostic
```

Then open the dashboard and confirm the schedule card shows seven day
columns with weekday, ordinal and month.

## Two honest cautions

**1. CI is red on this branch, and it is inherited.** 19 failures, all in the
containment suite, none in code FC-008 touches. Reproduced identically on the
production baseline; all 190 pass on a host that can create namespaces — which
the OptiPlex can, so the deploy-time test gate is not affected. Detail in
`IMPLEMENTATION_HANDOFF.md`. It clears when `claude/po-handoff-release` lands.

**2. We cannot presently confirm what is running.** Codex's
`PO_PRODUCTION_OBSERVATION_2026-09-07.md` records that the `status` branch is
absent, so the running SHA cannot be confirmed through the reporting channel;
that production moved to `0a5d24a` with no `accepted` / `deploy-approved`
transition in control explaining it; and that the installed release service
runs from a pinned copy at `e73e4c4` that does not follow production.

That last point is the one worth weighing before promoting: the automated
releaser is executing pre-correction logic, and success cannot be confirmed
remotely. Promoting into that pipeline is likely to work — the poller is
simple and reads production directly — but nobody will be able to *prove* it
worked without someone on the box. If that matters, land FC-002 first; it is
the task that fixes exactly this reporting gap.

## What Claude did not do

No promotion, no production write, no `--force`, no `--bootstrap`, no control
write beyond the FC-008 directive Anthony authorised. Deployment
Authorization remains `none`.
