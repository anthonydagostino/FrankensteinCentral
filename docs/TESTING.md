# Testing

Run everything: `bash scripts/test.sh` (~15s, no services required).

It runs on **every push** two ways:
- **CI** — `.github/workflows/tests.yml` on push and PR.
- **The box** — `scripts/deploy.sh` runs it against the freshly-pulled code
  *before* touching containers. A failing suite aborts the deploy and leaves
  the last good build running. `DEPLOY_SKIP_TESTS=1` overrides (emergencies).

## Why these tests look the way they do

On 2026-09-01 the money section showed "Firefly not connected", then "$0".
Nothing had been deployed to cause it. Two latent bugs fired because of the
calendar:

1. `_live()` asked Firefly for `first-of-month → today`. On the 1st those are
   the same date; Firefly rejects a zero-length range with **422**, and
   `raise_for_status()` on that first call aborted the other four reads. The
   homepage renders that failure as "not connected".
2. `_live()` used `date.today()` (container UTC) while the rest of the
   service used `LOCAL_TZ`. At 01:37 UTC it was September in the container
   and still August in New York.

Both had been latent since the code was written and would have fired on the
**1st of every month**. A test suite that exercises "today" would have
passed on Aug 31 and failed on Sep 1 — which is exactly the "it worked
yesterday" failure this suite exists to prevent.

**So date-dependent code is never tested against today's date.**
`services/firefly/tests/test_date_windows.py` sweeps a two-year calendar
(1,166 cases) and asserts the invariants on *every* day: the requested range
is never zero-length, never overshoots by more than a day, and always starts
at the month boundary — plus year rollovers and Feb 29.

## The rules that keep these tests honest

- **Test the real function, not a copy.** The date tests import
  `_month_window` from the service. `test_live_uses_the_shared_window`
  fails if `_live()` stops calling it, so the tested code can't drift away
  from the shipped code.
- **One clock.** All date logic goes through `_today()`. A test asserts no
  bare `date.today()` / `datetime.now()` call reappears elsewhere in the
  module, and tests pin the date by patching that one seam.
- **Assert the cause, not just the symptom.** Checking `/dashboard` returns
  200 is not enough: per-endpoint degradation keeps it at 200 while the
  range silently 422s. The test also asserts `degraded is None` against a
  healthy Firefly, on every 1st of the month.
- **Every regression test is verified to fail without its fix.** Each one
  above was confirmed by reintroducing the original bug and watching the
  suite go red. A regression test that never fails is decoration.

## What's covered

| suite | cases | what it protects |
|---|---|---|
| `services/firefly/tests/test_date_windows.py` | 1,923 | month boundaries, leap day, year rollover, UTC-vs-local, the clock seam, `$0`-vs-unknown flag, and the pay-cycle window (which must reach both the month start and a full lookback on every day) |
| `services/firefly/tests/test_endpoints.py` | 40 | real app + stub Firefly: 1st-of-month works, one bad endpoint degrades alone, total failure reports honestly, ingestion provenance (edits and account metadata are not imports), cache behaviour, `/cycle` (transfers and account names present, future dates dropped), `/history` (withdrawals only, truncation reported, bills degrade without failing the read) |
| `services/budget/tests/test_engine.py` | 28 | budget states, refunds, Budget Room, freshness signals, empty-month unknown |
| `services/budget/tests/test_recurring.py` | 2,324 | recurring charges swept across two years: a coffee habit is not a subscription, an annual renewal seen twice is not new, the oldest visible charge is not a new one, a stale resumption is not news, a penny of drift is not a price change, and a truncated read makes no absence claims |
| `services/assistant/tests/test_dashboard.py` | 47 | "next" is not "oldest", the seven-day week window across an 800-day sweep, ordinals incl. 11th/12th/13th, DST days swept hour by hour, leap day, month/year spans, pending+countered survival, conflict detection incl. across midnight, outage-vs-empty |
| `gateway/tests/donut.test.js` | 21 | the spending donut: percentages stay unrounded so near-equal slices don't collapse, the roll-up says how many categories it hides, every wedge is keyboard-reachable and announced, and the centre readout is composed beside its label rather than split back out of one — so changing the label's separator can't silently break the middle of the chart — with every caption trimmed to fit inside the ring |
| `gateway/tests/weekclock.test.js` | 5 | the browser's midnight rollover, swept over 800 days × 4 times of day, DST days hour by hour, month/year/leap boundaries |
| `services/budget/tests/test_paycheck.py` | 32 | pay cycle: savings never counted as spending (or subtracted twice), expected-vs-observed deductions, stale ledger pauses $/day but keeps totals, a missed paycheck reports unknown instead of an overspend, month-to-date across month/leap/year edges |
| `services/stocks/tests/test_session_label.py` | 1,480 | the portfolio card's session label: a completed past session is never called "Today", swept across two years and lags of 1-10 days; the oldest quote sets the headline, so one end-of-day bar can't ride under a live label |
| `services/budget/tests/test_service.py` | 15 | the wiring: firefly `/cycle` → paycheck engine and `/history` → recurrence engine → `/status`, `/paycheck`, `/recurring`, including the cross-service field contract and the rule that an outage is never cached as an answer |

## Browser-side date logic

Almost all date work happens server-side, where the Python sweeps can reach
it. The one exception is *when an open tab should re-fetch* — that has to run
in the browser. It lives in `gateway/static/weekclock.js` rather than inline
in `home.js`, takes `now` as an argument instead of calling `new Date()`
internally, and is swept by `node --test` under a pinned `TZ`, exactly as the
Python date logic is swept.

```bash
TZ=America/New_York node --test gateway/tests/*.test.js
```

`scripts/test.sh` runs this. Pass the file glob, not the directory: `node
--test <dir>` is not portable across the Node versions this runs on.

Assert against literals, not against the constant under test. A first draft of
the rollover test compared the fire time to the same `GRACE_MS` that produced
it, so setting the margin to zero still passed — a test that cannot fail is
decoration, the same trap the sweep rule exists to avoid.

## Adding a service's tests

Services all name their package `app`, which collides across one pytest run.
Load modules through the root `conftest.py` helper instead:

```python
from conftest import load_service_module
mod = load_service_module("unique_alias", "services/<svc>/app/main.py")
```
