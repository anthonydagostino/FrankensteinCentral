# Unshipped work queue — surveyed 2026-09-06

Anthony asked for a check that other agents' work is pushed and can actually
be deployed while the Product Owner is unavailable.

**Everything is pushed. Nothing is stranded locally** — both checkouts on the
box are clean and fully synced, and no worktree holds uncommitted work.

**Nothing here can deploy yet**, and not for a mechanical reason: none of it
carries an authorization binding, because no directive authorizes it. Claude
cannot create one — that is the boundary. Each item below is verified and
waiting on a Product Owner decision.

## Ready and verified

| branch | tip | vs production | suite |
|---|---|---|---|
| `claude/po-handoff-release` (FC-002) | `f1fa382` | +5, **fast-forward** | **1586 pass** |
| `claude/FC-001-dashboard-firefly-calendar` | `0a5d24a` | +1, **fast-forward** | **1486 pass** |
| money/paycheck, `d1af4e7` on `claude/financial-import-spending-nzhx02` | — | branch is 12 behind; the **commit cherry-picks cleanly onto production** | **2276 pass** |

### FC-001 — Firefly figures and a real calendar
Author's own summary: there was no calendar card, and the "Next:" line showed
the **oldest** event ever stored, because `next_event` was `events[0]` from an
unbounded ascending query. The same list fed the "starts in 30 min" rules, so
Do-Next could never fire for a calendar event. `build_home` also filtered to
`status == confirmed`, dropping the pending and countered interview holds.
Both fixed in the same commit. 6 files, +406/-16, with 157 lines of new tests.

### Money / paycheck — `d1af4e7`
"What I spent this month, and what's left of this paycheck." 17 files,
+1524/-23, including `services/budget/app/paycheck.py` and ~670 lines of tests
across budget and firefly. **This is real product work that has been sitting
unshipped since 2026-09-05** on a branch that fell 12 commits behind.

## The sequencing problem, measured

- `f1fa382` + `0a5d24a` rebase cleanly and pass together: **1603**.
- `d1af4e7` applies cleanly to production alone: **2276**.
- **All three together CONFLICT** — one hunk, ~30 lines, in
  `gateway/static/home.js`. FC-001 adds a calendar card to the dashboard home;
  the money work rewrites the same region for spending and paycheck figures.

That conflict is a **product decision** — what the dashboard home shows and in
what order — so it is left for the Product Owner. Claude has not resolved it
and has not rewritten any other agent's branch.

Both FC-001 and FC-002 are fast-forward from production *right now*. Whichever
is promoted first, the other needs rebasing; that rebase is verified clean.

## Not deployable, and must not be promoted

`claude/dashboard-improvement-ideas-s6a49q`, `claude/personal-app-hub-vvpy4h`,
`claude/dev-team-management-33paeh`, and the tip of
`claude/financial-import-spending-nzhx02` are all **12 or more commits behind
production**. Promoting any of them would delete thousands of lines, including
the entire worker test suite. The release service's fast-forward rule refuses
them, which is the boundary doing its job — but nobody should route around it.

Their unique content is documentation and status reports, **except** the money
commit called out above, which is the only product work worth recovering from
that set.

## What the Product Owner needs to decide

1. Order: FC-002 then FC-001, or FC-001 first? Either is a clean rebase.
2. The `home.js` conflict — which dashboard layout wins.
3. Whether the money feature gets its own task id and directive so it can be
   bound and released; it is the largest piece of finished, tested, unshipped
   product work in the repository.
4. Whether the four stale branches should be closed to stop them being
   mistaken for live work.

Nothing here was promoted, rebased in place, force-pushed, or deployed.
Production is untouched at `9b96bd0`.
