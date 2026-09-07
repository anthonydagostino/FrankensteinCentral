# FC-009 correction 1 — answering PO_REVIEW_47c1257.md

| | |
|---|---|
| Implementation SHA | **`6439b1824d6e5500ffc3196d59c952dca96e2434`** |
| Branch | `claude/FC-009-savings-classification` |
| Answers | `PO_REVIEW_47c1257.md`, control `7efed7e` |
| CI on that exact SHA | **green** — run 34144360998 |
| Suite | **2311 passed, 0 skipped**, exit 0 |
| Deployment Authorization | none |

Both P1s were real, both were mine, and the second one was a correction I got
wrong while fixing something else.

## 1 — a withheld rule was claiming movements it has no claim on

Reproduced exactly as described, before changing anything: a pre-deposit rule
and a post-deposit rule both matching, one real $100 `Checking -> Savings`
transfer.

```
401k      amount=0.0   source=withheld_before_deposit   claimed_by=None
Savings   amount=0.0   source=ambiguous_rule            claimed_by=['401k']
savings_total=0.0   left=2000.0
```

A genuine transfer disappeared. The deduplication added in the previous commit
let the `already_withheld` rule take the row, contribute 0 as withheld, and
leave the real rule reporting the row as already claimed.

`already_withheld` describes money the **employer** removed before the deposit
landed, so a transfer sitting in the ledger after payday is by definition not
that money. Such a rule now scans nothing and consumes nothing — its amount
was never coming from the ledger. After:

```
401k      amount=0.0     source=withheld_before_deposit
Savings   amount=100.0   source=observed
savings_total=100.0   left=2300.0
```

`test_a_pre_deposit_rule_does_not_swallow_a_real_transfer` is the regression
you asked for; `test_a_withheld_rule_alone_consumes_nothing` covers the same
rule without a competitor.

## 2 — "a floor" was wrong, and wrong in the dangerous direction

You are right and I want to be precise about how I was wrong, because the
error was the exact kind this layer exists to prevent.

Missing **withdrawals** make `spent` too low, and `left = spendable − spent`,
so `left` comes out too **high** — a truncated window claiming *more* money
available than exists. Missing deposits push it the other way. Missing history
can put the cycle boundary somewhere else entirely. "Floor" asserted a
direction the data does not have, and asserted it in the direction that costs
the owner money.

What changed:

- `paycheck`, `spendable`, `left` and `per_day` are reported **only** when the
  window is known whole. Otherwise `null` — not shaded, not caveated.
- `spent` survives, because observed spending really did happen, carrying
  `spent_is_lower_bound`. The rendered text says *at least*.
- **`window_complete` is now three states.** A payload that does not carry it
  is `null` — unknown, not proof of completeness. The budget service passes
  that silence through rather than defaulting it to `True`; that default was
  the same false-confidence bug one layer up, and I had written it in.
- Propagated past `stale_reason` as instructed: the assistant carries
  `window_complete` and `spent_is_lower_bound` into the homepage slice, and
  **both** rendered money surfaces replace the arithmetic with the explanation
  when `left` cannot be stated, rather than printing a subtraction with holes
  in it. `app.js` labels the month figure "Spent (at least)".

```
window_complete=True    paycheck=2400  spendable=800  left=588  per_day=set
window_complete=False   paycheck=None  spendable=None left=None per_day=None
                        spent=212  lower_bound=True  state=unknown
window_complete absent  identical to False, and reported as null
```

## 3 — monthly-budget completeness stays open

Recorded as open, not closed. `/month` emits `window_complete`; the monthly
budget engine does not consume it. No claim is made that every truncation
finding is resolved, and nothing infers a complete budget from a partial
response.

## Evidence

Six new regressions, each verified to **fail with its own bug reintroduced**:
a withheld rule that claims rows, a partial window that only suppresses
`per_day` (the shape of my first attempt), and silence read as completeness.

One new cross-service test pins the fixture to `_cycle_payload` actually
emitting the key, so the stand-in cannot drift from the service it stands for.

The shared test fixture now states `window_complete` explicitly instead of
omitting it. Under the corrected rule omission means unknown, and a fixture
exercising the ordinary case has to say the window was whole exactly as the
real payload does. That change surfaced 15 tests that had been relying
silently on the old permissive default — worth noting as evidence of how far
the wrong default reached.

## Standing caveats, unchanged

- **Nothing verified live.** No OptiPlex, no real Firefly, no real ledger.
  Every fixture synthetic; the repository is public.
- **Still no control epoch.** Same declared deviation as the first handoff.
  Your note that this proposal needs explicit task authorization and binding
  before release is accepted, not argued with.
- **Not promoted; production untouched; no `.frankenstein/` writes on the
  task branch.**
- FC-008 remains in flight and separate. Both branches and production history
  preserved. No money fix is presumed to be in the calendar candidate.

The full-suite figure is a worker claim in the same sense as before, but the
CI run above is independent of this machine and green on this exact SHA.
