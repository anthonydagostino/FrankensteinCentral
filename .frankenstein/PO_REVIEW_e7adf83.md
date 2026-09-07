# PO review: e7adf83

Reviewed e7adf839e0e2e225e50550f5564de7dd93d85512 against current production 0a5d24a13309f79386f69cfa575b48336df445eb at Anthony's explicit request. Not accepted. Metadata-only review; no task switch, release approval or implementation authorization.

## Findings

1. P1: Savings classification ignores transfer direction (services/budget/app/paycheck.py:75, 130, 225). Matching searches source and destination indiscriminately. Synthetic reproduction with a 2000 paycheck and a 300 transfer FROM Savings TO Checking produces savings_total=300 and left=1700, claiming a savings contribution that went the opposite way. Purchases funded from a matching savings account can also be removed from month.spent. Classify actual outflows to savings using direction and account identity; handle reverse movements explicitly rather than adding their absolute amounts as contributions. Cover both directions and ordinary purchases from savings.

2. P1: Overlapping allocation rules double-count a transaction (paycheck.py:225–237). Each allocation independently scans every transaction. Synthetic reproduction: two rules matching "savings", one actual 100 transfer, 2000 paycheck -> savings_total=200, left=1800. The same ledger movement is deducted twice. Reject ambiguous configuration or assign each movement exactly once with an explained policy; preserve split transactions correctly. Add a regression for overlapping names/terms.

3. P2: The new cycle endpoint silently treats capped transaction history as complete (services/firefly/app/main.py:_cycle_payload and _fetch_txns). The 75-day window caps withdrawals at 10 pages and deposits/transfers at 4; the helper returns its partial list without a completeness signal. A longer ledger can therefore undercount spending or miss the actual paycheck while still generating available figures and daily guidance. Follow pagination to completion within explicit resource limits, or mark incomplete evidence and suppress unsupported totals/guidance. Exercise more-than-cap pages.

## Validation and release prerequisites

- Independently ran all 32 paycheck engine tests: pass. The two calculation reproductions above also ran against the exact candidate; existing tests do not cover them.
- GitHub Actions run 34069263187 / job 101583467204: 19 failed, 2237 passed, 37 skipped, exit 1. Logs show worker namespace failures and test_mismatched_running_commit_reports_pending. These are inherited CI/test-environment issues, not proof the live product is broken, but the final candidate needs green clean-checkout CI.
- No authorization binding exists in the candidate; STATE.json remains the FC-001 awaiting_directive placeholder. Assign and bind an explicit task and obtain a complete handoff before release review.
- Production remains 0a5d24a. No deployment or mutation of application data performed.
- Review covers code and targeted calculation tests; no live financial data or visual acceptance was exercised.
- Preserve the active FC-008 correction and paused FC-002 review. This note alone does not wake a worker or authorize queued money changes.
