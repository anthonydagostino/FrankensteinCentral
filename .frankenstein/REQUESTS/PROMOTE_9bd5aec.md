# SUPERSEDED — do not promote `9bd5aec`

This file previously asked for `9bd5aec08c59dc58979fd3a17a9dcc85676cfb09`.
**Do not promote it.** Two reasons, both mine:

1. **Its CI was red.** Run 34156515582 failed on
   `test_legacy_override_flags_are_inert_not_errors` — the *same* ambient-checkout
   pattern I had just fixed in its sibling test and did not notice was repeated.
   I published the request before that run finished, which was the wrong order.
2. **It was already stale.** Production moved to `850278e` while it was in flight.

Two of the three things it carried are now in production, done independently
and shipped while mine was in flight:

- the hermetic promote tests — production `850278e`, *"Make the promote tests
  hermetic — mine aborted the deploy they shipped in"*. Verified: the
  deploy-boundary suite is **64 passed** on production.
- the seven-day weekly calendar — production `be294dd`. Verified: `week_window`
  and `dayCard` both present.

Neither needed doing twice, so both are dropped rather than reshipped.

See `PROMOTE_7b0b913.md` for what is actually left.
