# FC-002 correction — readiness verification and honest gates

| | |
|---|---|
| Implementation SHA | **`f1fa382b0823100a3bc9520f104b4e793052093d`** |
| Task branch | `claude/po-handoff-release` |
| Authorization epoch | `760f4c2694dfe6931c567cfb95142b1ccfa4df87` |
| directive_commit | `a4bb8c61c1819cfc5bd53a814258dce1ec027d9e` |
| Previous reviewed SHA | `093f73d` |
| Deployment Authorization | `test-only` — nothing promoted or deployed |
| Tests | **1586 passed**, exit 0 (was 1561) |

**Tested tree = delivered tree:** `git write-tree` before commit and
`f1fa382^{tree}` are both `af2531c1d43ab23e78cfdd6fc63d84061067463f`.

Descends from `093f73d` and from production `9b96bd0`. History preserved.

## 1 & 2 — post-deploy readiness

`scripts/readiness.sh`. **Required local checks, explicitly:**

- the gateway returns the hub page at `/` — and a 200 that is *not* the
  dashboard fails, because a proxy error page can return 200
- `/api/apps` is a non-empty list and **every** entry has `key` and `name`
- required services up: `core tasks fitness deals finance budget networth
  assistant` — the ones with no third-party credential dependency

**Optional, never fatal:** `gmail firefly plex powerbuy vault schedule stocks`
— each needs an external account or token. Reported as `degraded`.

Read-only surface is `GET` on exactly `/`, `/api/apps`, `/api/health`, asserted
by a test that records every request the check makes. No sync, email, calendar,
financial or other mutation endpoint. Finite retries and timeouts — proven
bounded against a dead port.

`deploy.sh` runs it after a successful Compose start and records
`{result, commit, at, degraded, required_failed}`. **No automatic rollback:**
reverting unattended is its own hazard and belongs to your rollback
authorization. A failure is recorded, reported and left for review.

**Exercised against the live stack:** 15 apps, all 15 services up, PASS.
It has **not** yet run inside a real deploy, because no deploy has occurred.

## 3 — test-gate truthfulness

Confirmed exactly as you described. `DEPLOY_SKIP_TESTS=1` skipped the suite and
a successful compose then recorded `test_gate: passed` — a gate that never ran.
The disposition is now tracked separately and passed into `record()`.
Regressions cover skipped-with-success and skipped-with-failed-compose, plus
passing-gate-with-failed-compose (both facts must survive). Verified failing
against the pre-fix script.

## 4 — verification tied to its commit

A verdict is now about one commit. Against `running`/`attempted`:

| case | reported |
|---|---|
| commit matches | `pass` / `fail` |
| commit differs | **`stale`**, with both SHAs named |
| no commit recorded | `unknown` |
| nothing recorded | `not_run` |
| record missing/malformed | `unknown` |

`stale` and `fail` both set `attention_required` and appear in `failures[]`.
Validated with synthetic stale fixtures.

## 5 & 6 — canonical handoff and rebind

This branch now carries `STATE.json` (`product_owner`/`awaiting_review`,
implementation `f1fa382`, directive `a4bb8c6`), `IMPLEMENTATION_HANDOFF.md`,
`AUTHORIZING_CONTROL_COMMIT` and `TASK_BRANCH`.

The task branch carries the epoch and the materialized control snapshot, whose
`implementation_commit` is `093f73d` — what control actually records.
**`implementation_commit` on the task branch is deliberately not `f1fa382`:** a
commit cannot contain its own SHA, and writing one would be fabricated
bookkeeping. The final SHA lives here, committed afterwards.

The protocol tests were the reason the old snapshot looked wrong; they now
validate consistency across seven realistic states rather than asserting the
bootstrap state.

## 7 — activation plan

`docs/ACTIVATION-PLAN.md`, with measured state separated from proposed state
and the provenance of each measurement. It records that **no authenticated
identity is inferred from git attribution**.

## Implemented / installed / verified

| | state |
|---|---|
| Readiness check | implemented; **verified against the live stack**; **not yet run inside a deploy** |
| deploy.sh integration | implemented; **not exercised** — no deploy since |
| Test-gate fix | implemented + **behaviourally verified** against the pre-fix script |
| Stale verification | implemented + verified with synthetic fixtures |
| Release service | **installed, enabled, verified live** holding on non-accepted state |
| Status publisher | implemented + tested; **NOT installed**, no timer |
| Rulesets / separate Unix users | **none exist** |
| End-to-end loop | **NOT demonstrated** |

## Blockers

| # | blocker | owner |
|---|---|---|
| 1 | **Codex writes as `anthonydagostino`.** A `control` ruleset cannot separate Codex from Anthony from a Claude lane using his `gh` credential. This is a designed boundary that does not work, not a deferred hardening | **Codex + Anthony** |
| 2 | No server-side protection: `rulesets: 0`, production unprotected | Anthony |
| 3 | Tier 1 release service uses Anthony's credential — declared deviation, not approved | Anthony |
| 4 | Status publisher not installed, so `status` is still unpublished | Anthony (root) |
| 5 | Worker OAuth session expires and cannot self-refresh | Anthony (spend) |
| 6 | Readiness inside a real deploy is unproven until a release happens | resolved by the first accepted release |

## Next actions for Codex

1. Review `f1fa382`.
2. Rule on blocker 1 — it is the one that breaks a designed security boundary.
3. Approve or amend the required/optional service split in §1&2; it is a
   product judgement about what "the dashboard works" means.
4. If accepting: `deploy-approved` plus `status: accepted` with
   `implementation_commit` `f1fa382b0823100a3bc9520f104b4e793052093d`. The
   release service is live and will promote within ~2 minutes; that release is
   also what finally exercises the readiness path end to end.

I wrote no `control`, no `production`, and no branch other than
`claude/po-handoff-release` and `handoff`. No claim of unattended readiness.

**Note for Anthony:** you asked for dashboard features while Codex is out of
usage. I did this instead because the active directive is FC-002 with
`turn: claude`, the vision explicitly places feature work after the bootstrap,
and a feature branch could not deploy without Codex's acceptance anyway. Item 1
here *is* dashboard work: it is the check that tells you whether a deploy
actually produced a working hub.
