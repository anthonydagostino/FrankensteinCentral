# FC-009 — the three money findings in PO_REVIEW_e7adf83.md, closed

| | |
|---|---|
| Implementation SHA | **`d2b77b6306ece71b51aa0bf52e1cd942d1fb6135`** |
| Task branch | `claude/FC-009-savings-classification` |
| Baseline | `e7adf83` (current production) — fast-forward |
| Answers | `PO_REVIEW_e7adf83.md`, control `b06a199` |
| Suite | **2306 passed, 0 skipped**, exit 0 |
| CI on that exact SHA | **green** — run 34143034262 |
| Deployment Authorization | **none** — nothing promoted |

**Authorization, stated plainly.** There is no control directive for this
task. Anthony authorized it directly as product intent, in session, after
being shown the reproductions. Claude must never write `control`, so the
authorization is recorded here for Codex to ratify or reject rather than
being asserted as an epoch. If Codex's answer is that this needed a directive
first, that is a fair finding and the work is reviewable either way — nothing
was deployed on the strength of it.

`FC-009` was taken from `frankenstein-status.sh --next-id`, not chosen.

## 1 — direction (P1)

Matching an account name anywhere in the row could not tell a contribution
from its exact opposite. Three rows all mention "Fidelity"; one is saving:

```
Checking -> Fidelity        a contribution
Fidelity -> Checking        money coming BACK OUT
Fidelity -> Coffee Shop     a purchase, funded from savings
```

Reproduced against the production tree first, from the review's text rather
than the branch's own tests:

```
$300 Savings -> Checking    savings_total 300.0   left 1700.0   (should be 0 / 2000)
$45 coffee from Savings     month.spent   0.0                   (should be 45.00)
```

The second is sharper than the review states. The purchase is removed from
spending *and* added to savings, so a real withdrawal renders as a confident
**"$0.00 spent this month"** — the exact claim `docs/BUDGETS.md` forbids. Its
"left to spend" was arithmetically right only because two $45 errors
cancelled.

The **source** now settles it: money leaving a savings account is never a
contribution. Destination, description and category still identify an inflow,
because a Firefly transfer is routinely described "Savings" while only the
destination account says "Fidelity" — narrowing to the description would have
broken that ordinary case, and
`test_the_destination_still_identifies_savings_the_description_does_not_name`
pins it.

## 2 — one movement, one rule (P1)

Every rule scanned every transaction independently, so two rules matching
"savings" each claimed the same $100 and the pot was debited $200.

Claimed by **position, not content**: two genuinely separate $50 transfers on
the same day are identical dicts, and a content-keyed dedupe would silently
halve them. `test_a_split_transfer_still_counts_every_part` fails against
that over-correction.

Deduplication alone was not enough. The losing rule fell back to its
configured amount, so the same $100 came out again labelled `expected`. A rule
whose only candidate was already claimed now reports `ambiguous_rule` with
`claimed_by` and contributes **0** — the "reject ambiguous configuration"
half of the review's instruction. `home.js` labels it, since a bare `$0` in a
line whose whole purpose is retraceability is its own defect.

## 3 — partial windows (P2)

`_fetch_txns` stopped at a page cap and returned a prefix with no signal, so a
long ledger produced a plausible "spent this month" and a plausible `$/day`
computed from part of the window.

It now returns `(rows, complete)`, using Firefly's own `meta.pagination`
where present and **refusing to assume a full last page is the last one**
when it is absent. Both payloads carry `window_complete`. The engine keeps the
totals — they are a floor, and a floor beats nothing — and suppresses
`per_day` with the reason named:

```
window_complete=True    spent 60.0   left 1840.0   per_day 230.0    reason None
window_complete=False   spent 60.0   left 1840.0   per_day None     reason "only part of
                        this window could be read from the ledger, so these totals are a
                        floor, not a full picture"
```

A firefly build that omits the flag is treated as complete, not as
permanently partial — crying wolf would train the caveat to be ignored.

## Evidence

**Eleven regressions, each verified to fail with its own bug reintroduced.**
Five bugs put back one at a time: direction-blind matching (2 fail),
per-rule rescanning (1), content-keyed dedupe (2), silent truncation (2),
and an engine ignoring `window_complete` (1).

Two of the eleven guard the *opposite* error — split transfers, and a full
last page with no pagination metadata — so over-correction fails too.

Full suite **2306 passed, 0 skipped, exit 0**, under
`FRANKENSTEIN_REQUIRE_SANDBOX=1` with the sandbox available, so containment
executed rather than skipped. Baseline `e7adf83` was 2293; the 13 new tests
account for the difference exactly.

`docs/BUDGETS.md` updated — the value table and three new refusals — because
it is the canonical spec a reviewer checks the code against, and the
semantics of "allocation-matched" changed.

## CI — one ported commit, declared

The first push (`47c1257`) was **red**: 19 failed, 2250 passed. None of them
were money. Eighteen were the worker containment suite failing because the
GitHub runner cannot create the namespaces, and the nineteenth was
`test_mismatched_running_commit_reports_pending` resolving `origin/production`
from an ambient checkout that a single-branch CI clone does not have.

Both are inherited from `e7adf83`, which is production and predates the fix.
That fix already exists, already reviewed, proven green: `691d70b` on
`claude/FC-008-ci-gate`. It was **cherry-picked here unchanged** rather than
re-solved — two files, `.github/workflows/tests.yml` and
`tests/test_deploy_boundary.py`, no product code — so this branch can show a
green run on its own SHA instead of asking the reviewer to take the failure on
trust. It no-ops the moment production carries it.

That is the one scope addition beyond the three findings, and it is declared
rather than folded in quietly. Nothing was skipped, disabled or quarantined:
the run is green with `FRANKENSTEIN_REQUIRE_SANDBOX=1`, which turns a skipped
containment test into a failure.

## Deviations and what is not claimed

- **No control epoch.** As above. This is the one deviation and it is
  deliberate rather than overlooked.
- **Nothing verified live.** No OptiPlex, no real Firefly, no real ledger.
  Every fixture is synthetic; the repository is public.
- **`window_complete` on `/month` is emitted but not yet consumed.** The
  monthly budget engine has its own freshness path and was out of scope here;
  the fact is published so that work does not have to re-derive it. Named
  rather than left to be discovered.
- **The `expected` fallback is unchanged** for a rule with no matching
  movement at all. Only the ambiguous-collision case was altered.
- **Not promoted, production untouched, no `.frankenstein/` writes on the
  task branch.**

## Interaction with what else is in flight

`e7adf83` is production and this branch fast-forwards from it. FC-008's
candidate `5f6b9bf` also descends from `e7adf83` and does **not** contain this
fix — the two are independent fast-forwards of the same base and will need
ordering, or one merge, before either lands. Flagging rather than choosing:
that ordering is the Product Owner's.
