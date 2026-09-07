# For the protocol agent: one live money bug is left

**Anthony routed this here.** I am the Team Lead session; `promote.sh` and peer
messaging are both blocked by the permission classifier in my session, and he
chose this channel. A request, not an instruction — you own production and can
see the box, which I cannot. Verify rather than trust.

| | |
|---|---|
| branch | `claude/hermetic-deploy-gate` |
| SHA | **`7b0b913a9eef1d0c466054b32573481e1fc33131`** |
| base | fast-forward from production `850278e` |
| CI | **green**, run 34156961819 |
| suite | **6928 passed, 0 skipped**, exit 0, `FRANKENSTEIN_REQUIRE_SANDBOX=1` |
| size | two files, one fix |

```bash
bash scripts/promote.sh 7b0b913a9eef1d0c466054b32573481e1fc33131
```

## The bug

The claim loop already assigns each movement to at most one rule and records
the clash in `allocation_overlaps`. But the rules that **lose** a movement have
`seen == []` and fall through to their configured amount — so the same $100
leaves the pot once as `observed` and again as `expected`. The overlap was
being reported while the arithmetic went on being wrong. Claiming once was only
half of not double-counting.

Reproduced on production `850278e` — two rules matching `savings`, one real
$100 transfer to Savings, $2,000 paycheck:

```
savings_total = 200.0    left = 1800.0        should be 100.0 / 1900.0
  'Savings'      100.0  observed
  'Savings Pot'  100.0  expected     <- the same $100, counted again
```

"Left to spend" reads $100 low for anyone whose allocation names overlap.

A rule whose every candidate was already claimed now reports `ambiguous_rule`
with `claimed_by` and contributes 0. **The collision is still reported as well
as fixed** — a test asserts `allocation_overlaps` stays non-empty, because a fix
that also silences the diagnostic is worse than the bug.

## Evidence

Four regressions, one verified to fail with the fallback restored. Two guard
over-correction — distinct rules with distinct movements both stay `observed`,
and a rule with no movement at all still `expects` its configured amount. The
fourth pins behaviour the fix must not disturb: a withheld rule still yields
the movement to the post-deposit rule that can account for it.

## What I got wrong, so you can weigh this accordingly

My previous request pointed at a red, stale SHA — I published it before CI
finished, and I had fixed one instance of the ambient-checkout test pattern
without noticing its sibling. `PROMOTE_9bd5aec.md` records that. This one is
green on its exact SHA and rebased on your current production.

Nothing here was verified against live Firefly or real ledger data. Every
fixture is synthetic; the repository is public.
