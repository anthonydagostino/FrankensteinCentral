# One branch, both features, green — `claude/ship-calendar-and-money`

| | |
|---|---|
| SHA | **`cfe8d84830f926768dbba6b46546e828509b373e`** |
| Baseline | `e7adf83` — **fast-forward**, no history rewritten |
| CI | **green**, run 34151909808 |
| Suite | **2368 passed, 0 skipped**, exit 0, `FRANKENSTEIN_REQUIRE_SANDBOX=1` |

## Why this branch exists

Three finished branches were queued behind an acceptance step. Anthony has
said Codex is no longer reviewing, and that the priority is getting features
out. Leaving the work stranded in three separate branches serves nobody, so
this is one promotable candidate instead.

It is a merge of FC-008 `0872e7e` into FC-009 `c251aea`. Both were independent
fast-forwards of production and neither contained the other.

## What it carries

- the seven-day weekly calendar on the main dashboard, DST-safe bucketing,
  honest schedule states, month-themed decoration with a persisted toggle;
- the Firefly figures and calendar card from FC-001;
- the paycheck engine and pay-cycle hero;
- direction-aware savings classification — a transfer out of savings, and a
  purchase funded from savings, are no longer counted as saving;
- one movement, one allocation rule, with `ambiguous_rule` where two rules
  describe the same money;
- ledger-window completeness carried from both Firefly payloads through the
  assistant to both rendered money surfaces.

## Conflicts, and how each was settled

Four, none resolved by preference:

| file | resolution |
|---|---|
| `.github/workflows/tests.yml` | comment only. Took FC-008's, because it is the one that is **true** on the merged tree — FC-008 landed the `FRANKENSTEIN_REQUIRE_SANDBOX` guard (3 references vs 0), so my wording calling the variable inert would have been false. Guard confirmed present after merging. |
| `dashboard.py` | additive both ways. Asserted neither side contained the other's symbols before keeping both. |
| `assistant/main.py` | an import list. Union. |
| `test_dashboard.py` | 11 money tests, 32 calendar tests, asserted no name collisions before keeping all. |

Presence of `week_window`, `month_spend_claim`, `is_savings_inflow`,
`ambiguous_rule`, `window_complete` and `month_is_lower_bound` was **checked
in the merged tree**, not assumed to have survived.

## What this does not do

**It is not promoted, and I did not promote it.** `CLAUDE.md` is explicit that
a product agent never runs `promote.sh` and never moves `production` by any
other means. That rule protects real financial data and does not lapse because
the reviewer did. Production remains `e7adf83`.

Moving it is for Anthony, or for the protocol agent under his explicit
approval. Everything up to that point is done and green.

Nothing here was verified against the live box: no OptiPlex, no real Firefly,
no real ledger. All fixtures are synthetic; the repository is public.

## Open, and stated rather than buried

- The monthly-budget engine still does not consume `window_complete`. `/month`
  emits it. Recorded as open, not closed.
- FC-002 (`claude/codex-prioritization-6oekr9` at `22b0aff`) is untouched and
  still the branch that answers `PO_REVIEW_5c5d64f.md`. It is what would remove
  the manual promote step entirely, and it needs a decision about activation
  that no longer has a Product Owner to make it.
- The three source branches are left intact. Nothing was deleted or rewritten.
