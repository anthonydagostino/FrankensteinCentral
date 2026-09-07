# PO review — FC-002 at 5c5d64f

Reviewed implementation: 5c5d64f9e554c2494ac5b5785ec1a875cc6e4c73.
Handoff observed at 7e250ded329146abe73c0e48cb7f49dca9ad877c, binding epoch 95000da5534bb0aed1c0d40e61588e2e11c20fa2 and directive 3c0b81d791cf41a67054b835941c7bceeeadff6e.

Decision: NOT ACCEPTED. This review is metadata only. It creates no authorization epoch, changes no current scope, and authorizes no deployment or activation.

## Current task reconciliation

Control now names FC-008, epoch c60c29798510224111bb8339c9aacc4655e6972e, directive 71dba66c8814646a16b3d244e76a4a074e812c20, Deployment Authorization none. That directive records a direct owner request for a weekly calendar and pauses FC-002. This report does not independently authenticate that external conversation or infer a writer from commit attribution. Codex preserves the in-flight task and its no-deployment boundary rather than overwriting its state. The FC-002 handoff is reviewed against its historical authorization, not treated as a response to FC-008.

## Evidence and findings

1. P1 — full CI fails on the exact candidate. Run 34076167041, job 101602594586, finished with 6 failed and 1618 passed, exit 1. The six failures are protocol tests: the task snapshot still references implementation f1fa382, which is not available in the clean CI clone after the rebase. This is different from the earlier namespace failure; that CI prerequisite now passes. The reported local 1624 pass result does not establish clean-checkout reproducibility. Preserve accurate historical metadata and make referenced history resolvable, or define and test appropriate historical-reference semantics without weakening authorization validation. Do not mask this with cached objects or skipped protocol tests. Require a green full run from a fresh checkout on the final candidate.

2. P1 — required dashboard JavaScript can be absent while readiness passes. Independent reproduction using the committed readiness test server: remove its script tag, leave the stylesheet, dashboard markers and healthy APIs. readiness exits 0 with result pass. scripts/readiness.sh lines 129–148 require only some asset, not the essential script/style set. Require the essential entry assets explicitly, and fail if any is missing or omitted from checking. Test against the actual entry page as well as synthetic fixtures.

3. P1 — wrong asset types still pass. Independent reproduction: return application/json and body {"error":"unavailable"} for /home.js; return application/json and body "not css" for /styles.css. readiness exits 0 with result pass. Lines 149–168 reject HTML but accept other unsuitable MIME/content. Validate the expected JavaScript and stylesheet types, including URL query strings and normal MIME parameters. Keep genuine assets passing and wrong-type assets failing. These are remaining cases of the existing readiness blocker, not additional feature scope.

4. History deviation: current production 0a5d24a is an ancestor of this candidate, so the production divergence has been reconciled without removing its dashboard changes. However, 7fa8162 is no longer an ancestor after the rebase, contrary to the original baseline-history requirement. Preserve durable access to previously reviewed commits and explain the history strategy on resumption. Prefer integration that retains reviewed ancestry.

## Improvements verified by inspection

The previous generic Application-error-page false positive is addressed. Required-service retries and malformed-entry handling are implemented. Missing/pending verification now keeps successful deployment confirmation actionable. Partial Compose failure clears running_commit and retains last success separately. These improvements do not override the findings above or demonstrate a real deployment.

The existing required/optional service split and four dashboard markers are acceptable as a minimum core-readiness policy, provided the essential assets also work. Optional integration degradation must remain visible. This policy is not evidence that every personal integration or feature works.

## Activation and ownership

Activation remains unapproved and the end-to-end loop unproven. Status is still absent. Claude's handoff reports installed release code separately from production and the candidate; that distinction is necessary, and installed code must be verified before any release approval. No host state is independently confirmed by this review.

The activation plan is not yet an executable consent package. Replace blanket statements that Anthony must perform every step and own routine host installation with named agent/operator execution and verification. Anthony provides only the exceptional consent required by AGENTS.md; he must not become the installer, notifier or routine release operator. Validate the proposed platform capabilities and enforcement checks before requesting that consent. Define a reviewed, pinned release-source update procedure; following an unspecified mutable ref is not sufficient.

On FC-002 resumption, Codex will issue a content-first/state-last correction incorporating these findings, rebind the candidate, and require clean CI plus the remaining activation evidence. This note alone does not wake or redirect a worker. FC-008 remains the active task; no acceptance or promotion is granted here.
