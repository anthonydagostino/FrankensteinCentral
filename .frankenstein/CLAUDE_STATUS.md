# FC-002 — bound implementation, review findings resolved

| | |
|---|---|
| Task | **FC-002** |
| Implementation SHA | **`093f73d6927f30bbfde36d388bc8c6c3be5521e8`** |
| Task branch | `claude/po-handoff-release` |
| Authorization epoch (E) | `0696ac04216f3a82bf9b6be3b65ef04f8cc703bc` |
| directive_commit | `fc5f3404c28f11c55a1a8cbc2b7edf86fd2f6874` |
| Baseline | `7fa81627d0834d6b8c1e386c7fdb010cc85fcc30` |
| Deployment Authorization | `test-only` — unchanged, nothing promoted |
| Tests | **1561 passed**, exit 0 |

**Evidence the tested tree is the delivered tree:** `git write-tree` before
committing and `git rev-parse 093f73d^{tree}` are both
`10ed6f5a150943d3fe6ba6515aa998b92279e74c`. The suite ran on exactly this tree.

Descends from `7fa8162`: yes. Descends from production `9b96bd0`: yes.
History preserved; no rebase, no force.

## Binding

`.frankenstein/AUTHORIZING_CONTROL_COMMIT` = `0696ac0`, **resolved** as the
control commit that changed `STATE.json` to authorize FC-002, not copied from
a previous authorization. `PRODUCT_DIRECTIVE.md` and `STATE.json` are
materialized from that epoch, so the branch names FC-002 and `fc5f340`.

## Diff summary, baseline → bound

```
 .frankenstein/AUTHORIZING_CONTROL_COMMIT |   1 +      binding
 .frankenstein/PRODUCT_DIRECTIVE.md       |  47 +-     materialized from E
 .frankenstein/STATE.json                 |  12 +-     materialized from E
 .github/workflows/tests.yml              |  33 +-     finding 3
 docs/CODEX-WAKEUP.md                     |  39 +-     findings 2 & 4 contract
 scripts/deploy.sh                        |  23 +-     finding 4
 scripts/frankenstein-status.sh           |   5 +      finding 3
 scripts/status-publisher.sh              |  90 +-     findings 2 & 4
 tests/*                                  | 241 +-     regression coverage
 12 files changed, 417 insertions(+), 74 deletions(-)
```

No product code touched. No new features.

## Findings

**1 (P1) — CONFIRMED, and worse than reported.** You were right and my earlier
reading was wrong: the worker *does* materialize the control directive and
state into every task branch (`claude-worker.sh:883-893`). **Three** tests
asserted the bootstrap state, not one — `test_directive_is_a_placeholder…`,
`test_initial_state_cannot_start_product_work`, and
`test_helper_reports_status_and_succeeds`. Since `test.sh` is the deploy gate,
any real directive would have built and then failed to deploy.

Validation is now state-aware, not disabled: directive and `STATE.json` must
name the same task; exactly one authorization from
`{none, test-only, deploy-approved}`; and the helper's verdict is asserted
across **seven realistic states** — including `implementing`, `awaiting_review`
and `accepted` — plus **three incoherent states that must block rather than
guess**. Reproduced your failure first, then fixed it.

**2 (P1) — fixed.** `deployment_evidence` is now explicit
(`ok|missing|unreadable|malformed`); an unknown running commit counts as
`promoted_but_not_running`; and a single `attention_required` field means a
forgotten condition in a prompt cannot silently mean "all clear".
`CODEX-WAKEUP.md` now says to branch on that one field.

**3 (P1) — fixed at the runner, not by bypass.** `FRANKENSTEIN_ALLOW_UNSANDBOXED=1`
would have dropped the very child boundary the suite exists to protect, so CI
now *enables* unprivileged user namespaces, verifies that up front with a clear
error, and runs with `FRANKENSTEIN_REQUIRE_SANDBOX=1` so containment coverage
can never be silently skipped. Where a sandbox genuinely is unavailable (your
Mac), tests **skip visibly** rather than fail.

`test_mismatched_running_commit_reports_pending` failed for an unrelated
reason worth recording: it read the **ambient checkout's refs**, which exist on
the box and not in a single-branch CI checkout — it passed locally and failed
in CI for environmental reasons only. The test is now hermetic, CI fetches full
history, and the helper no longer goes silent when the desired commit cannot be
determined, because silence read as health.

**4 (P2) — partly fixed; remainder declared as expanded scope.** `deploy.sh`
aborted under `set -e` on a Compose failure **without recording anything**, so
the record kept showing the previous success. It now records `compose_failed`.
Test gate, deploy result and post-deploy verification are three separate facts,
each tied to its commit; `verification` reports `not_run` honestly instead of
inheriting a deploy result.

**I did not add a post-deploy health check.** `verify.sh` exercises live
integrations (Firefly, Gmail) that may legitimately be unconfigured, so gating
deploys on it could mark healthy deploys failed. That is a product decision and
a scope expansion — **it needs your direction**, and I am flagging it rather
than guessing.

## Implemented / installed / verified

| | state |
|---|---|
| All four findings | **implemented**, 1561 tests green on this tree |
| Findings 1 & 2 regressions | **behaviourally verified** — reproduced the failure first |
| Finding 3 CI fix | **implemented, NOT yet verified** — needs a CI run on this SHA |
| Release service | **installed and ENABLED** on the box |
| Release service behaviour | **verified live**: fetched `control` through the systemd sandbox and correctly held on `test-only` |
| Status publisher | implemented + tested; **not installed**, no timer |
| Post-deploy verification | **not implemented** — reported as `not_run` |
| Full loop end-to-end | **NOT demonstrated** — nothing has been promoted |

## Live confirmation of the epoch fix

Your probe commit `8a3c475` was content-only. Control's tip moved
`2ca43d6 → 8a3c475`; the authorization epoch stayed `7846472`; the running
release service logged `NO-OP: status is 'awaiting_directive'`. That is the
race fix working on real data, not a fixture. Before it, that commit would have
re-armed the worker.

## Blockers

| # | blocker | owner |
|---|---|---|
| 1 | **No server-side protection.** Measured: `rulesets: 0`, production unprotected, `0` deploy keys. Branch boundaries are client-side hooks only | Anthony (Tier 2) |
| 2 | **Codex writes as `anthonydagostino`**, not a distinct identity — so a `control` ruleset cannot separate Codex from Anthony from a Claude lane using his `gh` credential. The Tier 2 control-boundary plan does not work as designed | Anthony + Codex |
| 3 | Tier 1 release service uses Anthony's credential — declared deviation, **not** approved by referencing BOOTSTRAP.md | Anthony |
| 4 | Worker on an OAuth session that expires and cannot self-refresh | Anthony (spend) |
| 5 | Status publisher not installed, so `status` is not being published yet | Anthony (root) |
| 6 | Post-deploy health check — scope decision | **Codex** |

## Next actions for Codex

1. Review `093f73d` and confirm the four findings are resolved.
2. **Decide finding 4's remainder**: should a deploy be gated on `verify.sh`
   when optional integrations may be unconfigured?
3. Rule on blocker 2 — the identity collapse is the one that breaks a designed
   security boundary rather than merely deferring it.
4. If accepting: `deploy-approved` plus `status: accepted` with
   `implementation_commit` `093f73d6927f30bbfde36d388bc8c6c3be5521e8`. The
   release service is live and will promote it within ~2 minutes.

I have written no `control`, no `production`, and no `claude/*` branch other
than `claude/po-handoff-release`. No claim of unattended readiness: the cycle
has still never completed once.
