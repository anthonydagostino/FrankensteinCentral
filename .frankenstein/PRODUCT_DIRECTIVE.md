# Product Directive

Task ID: FC-002
Status: changes_requested
Priority: highest
Deployment Authorization: test-only

## Objective

Bootstrap the existing autonomous Product Owner / Claude / release-service loop by giving the reviewed infrastructure a genuine authorization binding, following docs/BOOTSTRAP.md at 7fa81627d0834d6b8c1e386c7fdb010cc85fcc30.

## Product Context

Anthony authorizes this bootstrap. Codex has successfully written control (probe 8a3c47597491d9d1f4f78f17e61a94983586b626), and a recurring Codex Product Owner wakeup is configured every 15 minutes. Anthony must not relay routine messages or perform routine release mechanics.

## Requirements

1. Use existing task branch claude/po-handoff-release and baseline 7fa81627d0834d6b8c1e386c7fdb010cc85fcc30. Preserve its history. No new product features.
2. Resolve E as the control commit that changes STATE.json to authorize this directive. Read scope and state at E. Add .frankenstein/AUTHORIZING_CONTROL_COMMIT containing the full SHA E. Do not invent or copy a previous authorization.
3. Ensure the task branch's protocol metadata identifies this task and the exact directive_commit supplied in control. Record the implementation and handoff consistently so the protocol test gate passes.
4. Run bash scripts/test.sh at the resulting bound implementation. Publish the exact resulting SHA, baseline-to-result diff summary, test output summary, deviations and remaining activation blockers to handoff, with AUTHORIZING_CONTROL_COMMIT and TASK_BRANCH. Never write control or production.
5. Stop after the handoff for independent Codex review. Codex is separately reviewing baseline 7fa8162 against handoff df4933c and may issue corrections. No acceptance or release is implied by this directive.
6. Report release-service and status-publisher installation/enabled state honestly and separately from code readiness. Do not activate a shared-credential release path, alter credentials/accounts or security boundaries, incur new spend, or promote/deploy under this directive.

## Acceptance Criteria

- Bound implementation descends from the specified baseline and current production, with no unrelated product changes.
- Binding names the actual authorization epoch; task and directive identities match control.
- Full suite passes on the bound implementation, and all material Codex review findings are resolved before acceptance.
- Handoff provides the exact SHA and enough evidence to independently verify it.
- Release remains gated by subsequent Codex acceptance plus deploy-approved. Only the deterministic release service may promote.
- No claim of unattended readiness before an observed end-to-end deployment and verification.

## Explicitly Out of Scope

New product work; direct production pushes; bypass flags; manual promotion by Anthony; changes to credentials, accounts, spend or security boundaries; host activation under this test-only authorization.

## Verification Required

Full suite exit status and counts, exact implementation SHA, authorization epoch and directive SHA, read-back of published handoff binding, and explicit installed/enabled/verified distinctions.

## Product Owner Notes

This authorizes binding and verification of existing infrastructure, not retroactive acceptance. Keep Deployment Authorization unchanged; Codex alone decides acceptance. Publish through handoff, never control. Existing bootstrap Tier 1 credential-sharing is a declared deviation requiring review, not automatically approved by referencing BOOTSTRAP.md.


## Product Owner correction — review of 093f73d (2026-09-06)

The bound implementation and handoff at 11ba8d0 answer epoch 0696ac0. Codex independently verified GitHub Actions run 34061422396 on exact SHA 093f73d6927f30bbfde36d388bc8c6c3be5521e8: 1561 passed and ALL TESTS PASSED, including the sandbox preflight. Findings 1 and 2 are addressed in code; finding 3 now has passing CI evidence. The corrective changes within the existing bootstrap reliability objective are recognized. Finding 4 remains incomplete. Acceptance is withheld.

This correction explicitly expands implementation scope as follows, superseding the earlier binding-only limit where necessary. Continue the existing claude/po-handoff-release branch from 093f73d; do not begin a feature task or replace the work with production's old implementation.

1. Add a bounded, read-only post-deploy smoke check of the core dashboard: gateway serves the expected dashboard/static assets and a valid app catalog; the required local services/database are ready. Define required local checks explicitly. Optional third-party integrations that are unconfigured/disconnected must be reported as degraded or unavailable, not cause core deployment failure. Do not gate deployment on the broad live-integration verify.sh as a whole. Do not invoke sync, email, calendar writes, financial actions or other mutation endpoints.
2. Run the check after successful Compose startup with finite retries/timeouts. Record result, exact checked commit and timestamp. Distinguish successfully started containers from verified application readiness. A failed readiness check must be actionable, with truthful running/last-successful/attempted semantics; do not claim that a previous version is still running after a partial Compose replacement without evidence. Do not add automatic destructive rollback.
3. Fix test-gate truthfulness: DEPLOY_SKIP_TESTS=1 currently still leads record(success/compose_failed) to label test_gate as passed, because no call supplies tests_skipped. Preserve and report the actual test disposition independently from deployment disposition; add regressions for skipped tests with successful and failed Compose.
4. Tie verification to the running/attempted SHA. The publisher currently accepts a pass/fail field without checking that verification.commit matches the running commit. An old pass must not confirm a new deployment. Missing, not_run, malformed or mismatched verification must be clearly distinguishable and actionable when a released deployment requires confirmation. Validate this with synthetic stale-verification fixtures.
5. Publish the canonical handoff files as well as any human-readable status: .frankenstein/STATE.json (product_owner/awaiting_review, exact implementation SHA, directive identity), IMPLEMENTATION_HANDOFF.md, AUTHORIZING_CONTROL_COMMIT and TASK_BRANCH. The current handoff has no STATE.json or IMPLEMENTATION_HANDOFF.md; the task branch still carries an implementing authorization snapshot. Make the task/report distinction consistent with protocol tests. Keep implementation SHA bookkeeping truthful; do not fabricate a self-referential commit.
6. Rebind to the NEW authorization epoch created by this correction's STATE.json flip and the NEW directive_commit in that state. Preserve history and publish the tested final tree. Run the full Linux suite and CI on that exact tree, with containment coverage required.
7. Prepare a concrete minimal installation/validation plan for the status publisher and release-verification path. Do not activate hosts, grant credentials, change accounts or security boundaries, or spend money under this correction. Report actual installed/enabled state and its provenance separately from proposed state. Do not infer authenticated authorization identity from git authors/committers.

Deployment Authorization remains test-only. Do not promote or deploy. Codex will assess the corrected handoff and operational prerequisites before a separate acceptance/deployment decision. Product vision work follows a completed bootstrap; it is not part of this correction.
