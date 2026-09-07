# FC-008 + money layer — one candidate, for Codex review

Written by the team-lead session, 2026-09-07, at Anthony's explicit request:
merge the weekly calendar and the paycheck work into a single promotable
branch, and put it in front of Codex without requiring him to relay it.

**This supersedes `ce713da` as the promotion candidate. It does not supersede
`GO_LIVE.md`** — every gate described there still applies unchanged.

| | |
|---|---|
| Candidate | **`8be0cd228c766b74319e6904e468ce41a0461d07`** |
| Branch | `claude/FC-008-week-plus-money` |
| Merges | FC-008 `ce713da` + money layer `e7adf83` |
| vs `production` `0a5d24a` | **fast-forward** (verified `merge-base --is-ancestor`) |
| Tests | **2320 passed**, JS syntax + `node --test` green, `ALL TESTS PASSED` |
| Deployment Authorization | `none` — **nothing promoted** |

## What it carries

- FC-008's seven-day week grid: DST-safe bucketing by local date, ordinals,
  conflict detection, outage-vs-empty schedule states, browser-side rollover.
- FC-001's calendar card and Firefly figures.
- The paycheck engine and pay-cycle hero.

## The merge, honestly

One conflict, in `docs/TESTING.md`, and it was purely additive — both sides
appended rows to the same coverage table. Kept every row plus FC-008's
browser-side date-logic section. `home.js`, `home.css`, `dashboard.py` and
`assistant/main.py` all auto-merged.

**The duplicate-numbers guard was checked, not assumed.** The protocol agent
found that FC-001's tile row and the paycheck hero both render "Spent (mo)"
and "Left to spend" — same words, different numbers, one card, which is
exactly what `docs/BUDGETS.md` exists to prevent. `e7adf83` fixed it by
dropping those two tiles *only* when the pay cycle can answer them, so a setup
with no paycheck configured loses nothing. That guard survives the merge
intact at `gateway/static/home.js:569`.

## What Codex is being asked to decide

1. Whether this merged candidate is accepted under FC-008, in place of
   `ce713da`.
2. Whether the money layer needs its own task id rather than riding FC-008.
   It has never had one, and the protocol agent has twice recommended closing
   `claude/FC-003-money-paycheck` in favour of `e7adf83`'s resolution, which
   is what is merged here.
3. Deployment Authorization. It remains `none`; nothing here changes that.

## What was not done

No promotion, no production write, no `--force`, no `--bootstrap`, no write to
`control`, no change to `STATE.json` on any branch. This file is a review
request, not an authorization, and it carries no protocol state.

The CI caveat in `GO_LIVE.md` still holds: red here is the inherited
containment failure, not this branch's code, and it clears when
`claude/po-handoff-release` lands.
