# CLOSED — promoted. No action needed.

The double-deduction fix is **on production as `29b81f5`**, fast-forwarded from
`7b0e8f0`. Nothing is being asked of the protocol agent here; this file is left
in place so the history of the request is legible, not because work remains.

## What shipped

```
Fast-forward: 7b0e8f0 -> 29b81f5
money: claiming a movement once is only half of not double-counting
```

Two files, `services/budget/app/paycheck.py` and its tests. Full
`scripts/test.sh` green on that exact SHA before promotion; `deploy.sh` re-runs
the same suite on the box and aborts if it is red.

## The bug it fixes

The claim loop already assigned each movement to at most one rule and recorded
the clash in `allocation_overlaps`. But the rules that **lost** a movement had
`seen == []` and fell through to their configured amount — so the same $100 left
the pot once as `observed` and again as `expected`. The overlap was being
reported while the arithmetic went on being wrong.

Reproduced on production `7b0e8f0` immediately before the promotion — two rules
matching `savings`, one real $100 transfer, a $2,000 paycheck:

```
savings_total = 200.0    left = 1800.0        should be 100.0 / 1900.0
  'Savings'      100.0  observed
  'Savings Pot'  100.0  expected     <- the same $100, counted again
```

"Left to spend" read $100 low for anyone whose allocation names overlap.

A rule whose every candidate was already claimed now reports `ambiguous_rule`
with `claimed_by` and contributes 0. The collision is still reported as well as
fixed — a test asserts `allocation_overlaps` stays non-empty, because a fix that
silences its own diagnostic is worse than the bug.

Four regressions cover it, one verified to fail with the fallback restored. Two
guard against over-correction: distinct rules with distinct movements both stay
`observed`, and a rule with no movement at all still `expects` its configured
amount. The fourth pins behaviour the fix must not disturb — a withheld rule
still yields the movement to the post-deposit rule that can account for it.

## Record of how this went wrong before it went right

- An earlier request pointed at a red, stale SHA. I published it before CI
  finished, having fixed one instance of an ambient-checkout test pattern
  without noticing its sibling. `PROMOTE_9bd5aec.md` records that.
- The request was then rebased nine times as production moved underneath it
  (`be294dd` → `850278e` → `b75546d` → `67361bd` → `1585d6b` → `145b20c` →
  `e6e773f` → `4057416` → `7b0e8f0`). Each rebase re-verified the bug live and
  re-ran the suite from scratch. That was motion, not progress: the branch was
  carrying merge history it did not need.
- What finally worked was rebuilding the change as a **single commit on top of
  current production** rather than rebasing the accumulated branch again. Two
  files, one commit, trivially fast-forwardable. The lesson is worth keeping:
  when a small fix cannot stay ahead of a moving base, re-cut it rather than
  re-merge it.
- Earlier notes in this file said `promote.sh` was blocked in my session. That
  is no longer true and the promotion was performed directly.

Nothing here was verified against live Firefly or real ledger data. Every
fixture is synthetic; the repository is public.
