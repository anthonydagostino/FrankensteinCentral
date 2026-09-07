# Review of proposed FC-009 candidate 47c1257

Candidate: 47c12571a1da661035dffdc8f5085f2cf5932f49. Received via handoff/.frankenstein/FC-009/HANDOFF.md at 8a75082a67962eb243a9da513993814efe54c492. No acceptance or deployment authorization. This metadata-only review does not interrupt the active FC-008 correction or create an FC-009 epoch.

The direction-aware classification and ordinary rule-collision changes address the original reproductions. Independently ran 40 paycheck tests successfully. Remaining findings:

1. P1: A pre-deposit rule claims ledger movements before its withheld check, then deducts zero; a later post-deposit rule matching that movement sees it as already claimed and also deducts zero. Reproduced with a 2000 paycheck, a real 100 Checking-to-Savings transfer, and matching pre/post-deposit rules: savings_total=0, left=2000. An already-withheld allocation must not consume a real post-deposit transfer. Reject ambiguous configuration or assign the actual movement once to an appropriate rule. Add the mixed withheld/post-deposit regression.

2. P1: Partial-window handling suppresses per_day but still returns an exact available left/spendable/paycheck and describes all totals as a floor. Missing withdrawals make left an OVERestimate, while missing paycheck deposits can make it an underestimate: it is not a floor. Missing history may also establish the wrong cycle. Distinguish lower-bound observed spending from unknown remaining money; suppress unsupported exact left/paycheck/cycle claims or provide correctly justified bounds. Propagate completeness through assistant and both rendered money surfaces, not only a generic stale_reason. Missing completeness evidence is unknown, not positive proof of completeness.

3. Monthly-budget completeness is explicitly not consumed despite the changed fetch helper serving /month. Keep this documented as open; do not declare all truncation findings closed or infer a complete budget from a partial response.

The reported full suite remains a worker claim in this review; local validation was the targeted engine suite and the mixed-rule reproduction. No live data used. Before release, this proposal needs an explicit task authorization/binding, corrected final tree and independently checked clean CI. Keep FC-008 in flight; preserve both branches and production history. No money fix is presumed to be included in the calendar candidate.
