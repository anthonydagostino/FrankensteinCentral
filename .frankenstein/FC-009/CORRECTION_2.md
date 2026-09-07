# FC-009 correction 2 — the month headline, answering PO_REVIEW_6439b18.md

| | |
|---|---|
| Implementation SHA | **`e01268e5c65f4a1b11a10b18869e0170309d5c92`** |
| Branch | `claude/FC-009-savings-classification` |
| Answers | `PO_REVIEW_6439b18.md`, control `58b0896` |
| CI on that exact SHA | **green** — run 34145620336 |
| Suite | **2320 passed, 0 skipped**, exit 0 |
| Deployment Authorization | none |

The finding was correct, and the diagnosis in it was more precise than my own
reading of my change. The paycheck panel learned to say "at least" while the
headline directly above it went on printing an exact month-to-date total from
a window that had been truncated.

## Why attaching the flag to `paycheck` could never have fixed it

Two different services answer "spent this month" and they do not share a
window:

- the **pay-cycle engine**, when a paycheck is configured *and found*;
- **`/spending`**'s calendar month, otherwise.

You identified the branch that matters: a truncated window with **no matching
paycheck** takes the fallback, because `_paycheck_brief` returns
`available: False` and drops everything including the completeness
explanation. That is the case with the most need for the caveat and the one
guaranteed not to receive it.

## Three gaps, all closed

1. **`/spending` never reported completeness at all.** `_fetch_withdrawals`
   was discarding the flag I had added to `_fetch_txns` — `rows, _ = ...` —
   so the endpoint feeding the headline whenever the pay cycle is unconfigured
   could be silently truncated. It now returns `(rows, complete)` and
   `/spending` emits `window_complete`. This was a real hole I opened in the
   previous commit and did not notice.

2. **The number and its qualification are now chosen together**, in
   `month_spend_claim()`, so a caveat cannot attach to one source while the
   headline quotes the other. Absent completeness is unknown and therefore a
   lower bound — the same rule as the engine. `month_ingested: False` stays
   `None`: "at least $0" would be a claim, and nothing is imported yet.

3. **`home.js` renders it** — `≥ $480`, with "at least — part of the ledger
   couldn't be read" replacing "month to date".

`month_spend_claim` lives in `dashboard.py` rather than `main.py` for the
reason that module exists: `main.py` needs psycopg to import, so a decision
made there cannot be tested — and an untestable decision is exactly how the
first version of this shipped.

## Evidence

**Nine regressions**, verified to fail with each bug reintroduced:

| bug put back | tests that fail |
|---|---|
| month claim ignores `/spending` completeness | 3 |
| pay-cycle lower bound dropped | 1 |
| **flag emitted but renderer ignores it** | 1 |

The third matters most: it is the exact shape of the defect you just reported,
so a payload-only fix now fails rather than passing. Two further tests guard
the opposite error — a whole month must still read as an exact total, and an
unimported month is unknown rather than a lower bound of zero.

## Still open, unchanged

- **The monthly-budget engine completeness finding remains OPEN.** Not
  touched, not claimed closed, and no caveat was added to the paycheck panel
  and presented as resolving it.
- **No control epoch.** Same declared deviation. Your point that this needs
  explicit task authorization and binding before release stands.
- **Nothing verified live**; no visual acceptance. All fixtures synthetic.
- Production ancestry preserved, FC-008 untouched, nothing promoted. No money
  correction is assumed to travel with the calendar candidate.
