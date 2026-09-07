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
| `services/firefly/tests/test_date_windows.py` | 1,166 | month boundaries, leap day, year rollover, UTC-vs-local, the clock seam, `$0`-vs-unknown flag |
| `services/firefly/tests/test_endpoints.py` | 17 | real app + stub Firefly: 1st-of-month works, one bad endpoint degrades alone, total failure reports honestly, ingestion provenance (edits and account metadata are not imports), cache behaviour |
| `services/budget/tests/test_engine.py` | 28 | budget states, refunds, Budget Room, freshness signals, empty-month unknown |

## Adding a service's tests

Services all name their package `app`, which collides across one pytest run.
Load modules through the root `conftest.py` helper instead:

```python
from conftest import load_service_module
mod = load_service_module("unique_alias", "services/<svc>/app/main.py")
```
