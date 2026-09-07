# Promotion requests received out of band — 2026-09-07

Two pieces of finished work are queued for production. **Neither was
promoted.** Recording why, and what each needs.

## What is ready

| branch | tip | vs production `0a5d24a` | suite |
|---|---|---|---|
| `claude/financial-import-spending-nzhx02` | `e7adf83` | fast-forward | 2293 pass |
| `claude/FC-008-weekly-calendar` | `add5b2b` | fast-forward | 1513 pass |

Both verified independently here, not taken on report.

## The request, and why it was declined

A scheduled message asked me to promote `e7adf83` with `--force` or
`--bootstrap`, stating that Anthony had authorized it and was waiting.

I did not, for three reasons:

1. **The active directive carries `Deployment Authorization: none`.** That is
   the Product Owner's current, explicit position. `--bootstrap` exists to
   skip the gate, not to substitute for it.
2. **The claim of owner authorization arrived inside an automated message.**
   No live instruction accompanied it. A promotion justified by an assertion
   of consent, rather than consent, is exactly the failure the gate is for.
   This is not a judgement about the sender's honesty — the same rule would
   apply to a message I wrote myself.
3. **Codex is available again** and issued FC-008 at 02:20 UTC, so the
   ordinary path is open rather than blocked.

The request also described `STATE.json` as `product_owner / awaiting_directive`;
by the time it arrived control had moved to FC-008, `turn: claude`. Worth
knowing that the state it reasoned from was already stale.

## Their merge resolution is better than mine — adopt theirs

The `2f1c906` resolution of the Money card conflict is correct and mine was
not. FC-001's tile row carries "Spent (mo)" and "Left to spend" from Firefly's
raw figures, while the paycheck hero carries a savings-excluded "Spent this
month" and a pay-cycle "Left to spend". Same words, different numbers, one
card — precisely what `docs/BUDGETS.md` exists to prevent.

Theirs drops those two tiles **only** when the pay cycle can answer them, so a
setup with no paycheck configured loses nothing. My own
`claude/FC-003-money-paycheck` (`56dd5e5`) kept both and therefore ships the
duplicate-numbers bug. **Prefer `e7adf83`; my branch is superseded and should
be closed.**

## CI

Their diagnosis of the red CI is sound and matches mine: the sandbox skip
guard under-detects, so 31 guarded worker tests run on a runner that cannot
provide PID or network namespaces, and fail on the worker's correct refusal to
run a child unconfined.

The fix already exists on `claude/po-handoff-release` and Codex verified it
green (run 34062513900, 1586 passed, sandbox preflight included). It enables
the namespaces at the runner rather than widening the skip, so containment
coverage still runs. It reaches CI when that branch lands; no separate patch
is needed.

`test_mismatched_running_commit_reports_pending` has a different cause, which
they correctly declined to guess at: it read the ambient checkout's refs,
which exist on the box and not in a single-branch CI checkout. It is already
fixed on the same branch by making the test hermetic.

## What each needs

- `e7adf83`: a task id and `deploy-approved`, or an explicit live instruction
  from Anthony. It is a clean fast-forward and can go the moment either exists.
- `add5b2b`: Codex review under FC-008.

I duplicated FC-008 before noticing it was already implemented; that work is
discarded rather than pushed, since theirs is on the correct branch and has
better test infrastructure (a real JS harness wired into `scripts/test.sh`).
