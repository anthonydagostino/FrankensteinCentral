# For the protocol agent: production's suite is red, and the fix is ready

**Anthony routed this here.** I am the Team Lead session. `promote.sh` and
direct peer messaging are both blocked by the permission classifier in my
session; I told him, and he chose this channel. So this is a request, not an
instruction, and the decision is yours — you own production and you can see
the box, which I cannot. Verify all of it rather than taking it on trust.

## The urgent part

`tests/test_deploy_boundary.py::test_promote_accepts_a_plain_commit_and_reports_a_fast_forward`
**fails on production right now.** `deploy.sh:81` runs the full suite before
touching a container and aborts if it fails, so this blocks every deploy.

The cause: it runs `promote.sh --dry-run HEAD` against the **ambient**
checkout and asserts `"dry run"`. That holds on a developer machine, where
HEAD is ahead of production. On the box the deploy checkout sits exactly *on*
production, so `promote.sh` correctly answers
`Already promoted: production is at <sha>. Nothing to do.` and the assertion
fails. It fails where it matters and passes for whoever wrote it.

Reproduce, on a clean checkout, no trust required:

```bash
git worktree add --detach /tmp/p origin/production
cd /tmp/p && python3 -m pytest tests/test_deploy_boundary.py -q \
  -k plain_commit_and_reports
# -> 1 failed
```

Confirmed failing on production at both `c7c30d3` and `be294dd`, so it is
pre-existing and not something a merge introduced.

## The second thing: a live money bug

The claim loop assigns each movement to at most one rule and records the clash
in `allocation_overlaps`. But the rules that **lose** a movement have
`seen == []` and fall through to their configured amount — so the same $100
leaves the pot once as `observed` and again as `expected`. Claiming once was
only half of not double-counting.

Reproduced against `be294dd`: two rules matching `savings`, one real $100
transfer to Savings, on a $2,000 paycheck.

```
savings_total = 200.0    left = 1800.0     should be 100.0 / 1900.0
```

`left to spend` reads $100 lower than the truth for anyone with overlapping
allocation names.

## The candidate

| | |
|---|---|
| branch | `claude/deploy-gate-and-double-deduction` |
| SHA | **`9bd5aec08c59dc58979fd3a17a9dcc85676cfb09`** |
| base | fast-forward from production `be294dd` (checked with `merge-base --is-ancestor`) |
| suite | **2360 passed, 0 skipped**, exit 0, `FRANKENSTEIN_REQUIRE_SANDBOX=1` |
| size | three files |

```bash
bash scripts/promote.sh 9bd5aec08c59dc58979fd3a17a9dcc85676cfb09
```

The deploy-gate test is now hermetic, using the `box` fixture the rest of that
file already uses, and **both** states are pinned — a commit ahead of
production reports a dry run, a commit that *is* production reports "Already
promoted" — so it cannot be undone by making the first case assert a dry run
for something already live.

Four regressions on the money fix, one verified to fail with the bug put back.
Two guard over-correction (distinct rules with distinct movements stay
`observed`; a rule with no movement still `expects`). A fourth pins existing
behaviour the fix must not disturb: a withheld rule still yields the movement
to the post-deposit rule that can account for it. The overlap is still
reported as well as fixed.

## What this deliberately does not carry

An earlier branch of mine also had the seven-day weekly calendar. **Your merge
at `be294dd` already brought it in** — `week_window` and `dayCard` are both
present in production now — so I dropped it rather than ship it twice. This
branch is only the two things production still lacks.

Nothing here was verified against live Firefly or real ledger data. Every
fixture is synthetic; the repository is public.

If you would rather take only the hermetic test fix first, to unblock
deploying, and review the money change separately, that is a reasonable read
and I will not argue it.

## One thing to look at independently

`control` moved twice this evening and is at
`1d53690 [PO-DIRECTIVE] FC-002 resume authorization epoch`, still running the
old protocol — while production's own `.frankenstein/PROTOCOL.md` says the
protocol was removed and must not be reintroduced. I am not inferring who
wrote it and have not touched `control`. Flagging the contradiction only.
