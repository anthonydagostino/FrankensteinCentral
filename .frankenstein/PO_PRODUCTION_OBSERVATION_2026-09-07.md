# Production observation — 2026-09-07

Read-only observation by Codex, with this metadata-only record on control. No acceptance, deployment approval, rollback or new implementation epoch is created.

At the scheduled check around 01:53 UTC:
- production points to 0a5d24a13309f79386f69cfa575b48336df445eb, containing calendar and Firefly dashboard changes.
- Previous observed production was 9b96bd0c2076688e689576109bc57c17df291cee.
- control authorization epoch remains 95000da5534bb0aed1c0d40e61588e2e11c20fa2; FC-002 remains changes_requested, Deployment Authorization test-only.
- Latest handoff still answers older epoch 760f4c2 and reports implementation f1fa382b0823100a3bc9520f104b4e793052093d. No handoff for the current correction was observed.
- GitHub compare production...f1fa382 reports diverged: bootstrap is five commits ahead and one behind, common ancestor 9b96bd0.
- status branch remains absent; the actual running SHA cannot be confirmed through the reporting channel.
- GitHub Actions run 34068785765 for the new production SHA concluded failure in Run all tests. This does not by itself establish live application failure.
- No actor or separate owner authorization is inferred from git author/committer fields. The control history contains no accepted/deploy-approved transition explaining this production movement.

Implications for next review:
Preserve the newly observed production history. The existing bootstrap candidate cannot be fast-forward promoted over it. The next candidate must integrate the current production ancestry, retain its features, pass the applicable tests and be reviewed at its exact resulting SHA before acceptance. No force push, rollback or erasure of product work is authorized by this observation. Do not claim that promotion proves deployment or that a failing CI run proves the running application is broken.

This note leaves the active correction epoch unchanged so it does not interrupt an in-flight worker. Codex will reconcile the candidate with current production when reviewing the next handoff.
