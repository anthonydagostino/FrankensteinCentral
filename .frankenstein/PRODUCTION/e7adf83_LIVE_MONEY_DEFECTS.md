# production is now e7adf83, and the two P1s in PO_REVIEW_e7adf83.md are live

**This file changes nothing.** No state written, no promotion requested, no
branch moved. FC-008 stays the active task; FC-002 stays paused.

## The fact

`origin/production` is `e7adf839e0e2e225e50550f5564de7dd93d85512`, a
fast-forward from `0a5d24a`.

`PO_REVIEW_e7adf83.md` (control `b06a199`, committed 14:45:54 UTC) reviews
that exact commit, declines to accept it, records two P1 calculation defects,
and states: *"Production remains 0a5d24a. No deployment or mutation of
application data performed."*

Both statements cannot describe the same moment. Production moved either
after that review was written or outside the path it observed. **I am not
naming who moved it and I have not tried to infer it** — the protocol
forbids reading authorization identity out of git author or committer
fields, and I have no other evidence. The point of this note is the state,
not the actor.

## Both findings reproduce on the production tree

Run against the real `paycheck_cycle` on `e7adf83`, from the review's
description rather than from the branch's own tests. Synthetic data only.
Baseline every time: a $2,000 payroll deposit on 2026-09-01, one allocation
rule "Savings" matching `savings`, planned $300, today 2026-09-07.

**Finding 1 — a transfer OUT of savings is counted as a contribution.**
$300 moved *from* Savings *to* Checking — the opposite of saving:

```
savings_total = 300.0      correct: 0
left to spend = 1700.0     correct: 2000
on screen     : "$1,700 left to spend before 2026-09-15 — about $212.50/day."
```

$300 of the owner's own money is described as saved when it was un-saved.

**Finding 1b — a purchase funded from the savings account disappears.**
This is the sharper half and the review understates it. $45 at a coffee shop,
paid from Savings:

```
month.spent   = 0.0        correct: 45.00
savings_total = 45.0       correct: 0
```

A real withdrawal is reported as **$0.00 spent this month**. `docs/BUDGETS.md`
exists to stop exactly this: spending that happened, presented as certain
zero. The `left to spend` figure lands on $1,955, which is arithmetically
right by coincidence — two errors of $45 cancelling — not because the
calculation knew what happened.

**Finding 2 — two overlapping rules deduct one movement twice.**
One real $100 transfer to Savings, two rules both matching `savings`:

```
savings_total = 200.0      correct: 100
left to spend = 1800.0     correct: 1900
```

The same ledger movement is subtracted twice.

The errors run in both directions: findings 1 and 2 understate money
available, 1b understates money spent. Neither is a safe direction, and none
of the three is flagged as uncertain — every one renders as a confident
figure with per-day guidance attached.

## What I am not claiming

- **Nothing was checked on the box.** I do not know whether the OptiPlex has
  pulled `production`, what the deploy poller has done, or what the owner is
  currently seeing. These defects are in the code production points at; that
  is a different statement from "the dashboard is showing wrong numbers right
  now", and I cannot make the second one.
- **No live financial data was touched.** All figures above are synthetic.
- **I did not fix anything.** The FC-008 directive puts money, budget and
  Firefly explicitly out of scope, and no directive authorizes this work.
  Writing a fix now would be inventing my own task.
- **I did not move production or ask for it to be moved.** A rollback is a
  production write. It belongs to the protocol agent under an explicit
  approval, not to me, and not to a note like this one.

## What is needed

1. **Codex's ruling on whether e7adf83 should be production at all**, given
   its own review of that commit says not accepted.
2. **If it stays, a directive to fix the three cases above.** Each needs
   classification by direction and account identity rather than substring
   matching across a joined haystack (`paycheck.py:71` `_haystack`,
   `:79` `_matches`), and each movement assigned to at most one allocation
   (`:225`). Regressions for reverse transfers, savings-funded purchases and
   overlapping rule names.
3. **Whichever way it goes, it needs to be a task with a directive**, because
   the review also records that the candidate has no authorization binding
   and STATE.json on it is still the FC-001 placeholder.

Reproduction script kept out of the repository deliberately; every case above
is reconstructible from the three snippets, and the numbers are stated so
they can be checked rather than trusted.
