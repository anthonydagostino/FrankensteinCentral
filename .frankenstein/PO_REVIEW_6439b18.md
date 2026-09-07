# Review of FC-009 proposal at 6439b18

Exact SHA: 6439b1824d6e5500ffc3196d59c952dca96e2434. This is metadata only; no acceptance, deployment approval, task switch or new implementation epoch.

Verified independently:
- GitHub Actions 34144360998 / job 101813030829: 2311 passed, ALL TESTS PASSED.
- Local paycheck engine suite: 43 passed.
- Withheld allocations no longer claim post-deposit movements.
- Incomplete/unknown windows now suppress exact paycheck, spendable, left and per-day outputs. Both main arithmetic render paths handle a null left. These address the principal engine findings in PO_REVIEW_47c1257.md.

Remaining P1 from the existing end-to-end completeness requirement:
The homepage month headline still presents incomplete spending as an exact month-to-date total. services/assistant/app/main.py:604 copies pay_month.spent into month_spend, but the returned money object does not carry pay_month.spent_is_lower_bound. gateway/static/home.js:419 consequently always prints "Spent in [month]" / "month to date" without "at least". Passing the cycle lower-bound flag inside paycheck does not fix that separate month headline. A truncated cycle with no matching paycheck also takes the unavailable brief path, losing its completeness explanation entirely. Carry month completeness independently of paycheck availability and render a lower-bound label or unknown state at the headline itself; test both with and without a matching paycheck through the real assistant-to-renderer path.

The separate monthly-budget engine completeness finding remains open as declared. Do not treat it as resolved by adding a caveat only to the paycheck panel.

The full CI result is verified, not merely a worker claim. No live data or visual acceptance performed in this review. The proposal still needs explicit task authorization/binding before release. Preserve the active FC-008 correction and production ancestry. No money correction is assumed to travel with the calendar candidate.
