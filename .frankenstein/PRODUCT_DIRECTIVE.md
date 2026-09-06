# Product Directive

Task ID: FC-002
Status: ready_for_implementation
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
