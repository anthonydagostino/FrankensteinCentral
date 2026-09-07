# FC-008 `5f6b9bf` — verified independently, not taken on report

Team-lead check of the candidate published at `b624398`, run before Codex
reviews it so a bad claim costs a review cycle rather than a deploy.

| claim in the handoff | checked | result |
|---|---|---|
| suite 2325 passed, 0 skipped | re-ran `scripts/test.sh` on `5f6b9bf` | **2325 passed, exit 0, ALL TESTS PASSED** |
| containment actually exercised | `FRANKENSTEIN_REQUIRE_SANDBOX=1`, sandbox present | no skips; the guard would have failed the run |
| fast-forward from production | `git merge-base --is-ancestor e7adf83 5f6b9bf` | **YES** |
| both feature sets survive the merge | `week_window`, `paycheck_cycle`, `firefly_state` all present | present |

The worker's numbers are exact. Nothing here disputes the candidate.

## One thing Codex should know before accepting

`5f6b9bf` merges production in, so it **inherits the three money defects**
recorded in `.frankenstein/PRODUCTION/e7adf83_LIVE_MONEY_DEFECTS.md`. I re-ran
the same reproductions against this candidate's tree and all three still
reproduce, unchanged:

```
$300 moved FROM Savings TO Checking   savings_total 300.0   left 1700.0
$45 coffee paid from Savings          month.spent   0.0     (should be 45.00)
one $100 transfer, two rules          savings_total 200.0   left 1800.0
```

That is not a criticism of FC-008 — the directive puts money explicitly out of
scope and the worker was right not to touch it. It matters only because
accepting `5f6b9bf` promotes a tree that still contains them, so the money fix
should not be assumed to travel with it. It needs its own task.

No state written, no promotion requested, nothing moved.
