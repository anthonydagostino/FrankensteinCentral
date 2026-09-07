# Product Directive

Task ID: FC-002
Status: changes_requested
Priority: highest
Deployment Authorization: test-only

## Objective

Resume the paused autonomous-release bootstrap. FC-008's requested calendar corrections passed scoped code review at 0872e7ee2086895afcfebf2c83f25d5236f40304 (PO_REVIEW_0872e7e.md); deployment is held. Preserve that candidate and its historical handoff. No further calendar or new product work is authorized here.

## Scope and required corrections

Continue claude/po-handoff-release from 5c5d64f9e554c2494ac5b5785ec1a875cc6e4c73. Read AGENTS.md, PROTOCOL.md, RELEASE_AUTOMATION.md, docs/BOOTSTRAP.md, and the existing PO_REVIEW_7fa8162.md and PO_REVIEW_5c5d64f.md. Preserve the earlier accepted design constraints and fixes.

1. Resolve the remaining readiness cases in PO_REVIEW_5c5d64f.md: missing essential script still passing; JSON or other unsuitable content types returned as JS/CSS still passing. Require the actual entry page's essential assets, validate appropriate types including normal MIME parameters and query strings, and keep reads bounded and read-only. Do not fetch arbitrary external URLs.
2. Make the final candidate reproducible in a clean full-history checkout. The prior rebase made f1fa382 unavailable to CI while task metadata referenced it. Preserve reviewed history through merge integration or another explicit durable history strategy; do not hide failures by skipping protocol tests or relying on cached objects. Keep binding validation strict.
3. Reconcile current production ancestry without rollback, force-push, or erasing product changes. Latest observed production is c7c30d32ac887e4a000cfe461c115fbcc5aaea09, advanced without a matching accepted/deploy-approved transition in control. Re-read production before integration. Do not infer writer identity from commit authors. Report the integrated baseline, candidate and installed release-source SHAs separately. Calendar code-review approval does not authorize merging later unreviewed branch additions.
4. Produce a concrete activation package that assigns routine setup and verification to the authorized agent/operator, not Anthony. Include required account/credential consent as a distinct final step after the plan is reviewable. Independently validate platform capabilities, credential isolation, reporting access, unit paths, and the pinned/reviewed release-source update procedure. Do not propose an unspecified mutable source ref for a privileged release process.
5. Distinguish implemented, installed, enabled and verified components. Read-only evidence should establish the reason status is absent and the installed release service's actual source. Prepare the minimal implementation/setup artifacts needed for an operator to activate reporting and the corrected release path after exceptional consent. No host activation, account/credential creation, new spend, weakened boundaries, promotion or deployment under this directive. If runtime access is unavailable, report that exact limitation and the designated execution route; do not route routine commands through Anthony.
6. Keep prior verification-required, pending, stale-SHA and partial-Compose running-state corrections intact. Demonstrate the readiness regressions, missing/malformed/stale evidence cases, and full Linux CI with containment actually exercised.
7. Bind the final candidate to this directive commit and the new STATE.json authorization epoch. Publish exact SHA, test evidence, deviations and canonical handoff with AUTHORIZING_CONTROL_COMMIT and TASK_BRANCH; archive prior task reports in git history. Stop for independent review.

## Acceptance criteria

- All outstanding material FC-002 review findings resolved on the exact bound tree.
- Current production ancestry preserved and historical references resolvable in fresh CI.
- Full bash scripts/test.sh passes, including containment and relevant regression cases.
- Activation package is executable and reviewable, with routine operational ownership assigned and exceptional consent narrowly identified.
- No claim of unattended readiness before a real accepted release and its reporting/verification are observed.

## Product and release holds

Money reviews remain tracked in PO_REVIEW_e7adf83.md, PO_REVIEW_47c1257.md and PO_REVIEW_6439b18.md. Production's newer money changes are not automatically approved by this resumption; review them separately before any claim that those defects are resolved. Do not add money features or change personal data.

Only Codex owns control and acceptance. Only the deterministic release service may promote after a separate valid approval. This directive authorizes bounded bootstrap implementation and planning only. Anthony supplies product intent and necessary exceptional consent; he is not the notifier, installer or routine release operator.
