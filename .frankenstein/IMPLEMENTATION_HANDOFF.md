# Implementation Handoff — FC-002 (bootstrap reliability corrections)

**This is protocol infrastructure, not an FC product task.**

This file is the in-branch record. The canonical, reviewable handoff — with
the exact final SHA — is published on the `handoff` branch, because a commit
cannot contain its own SHA and fabricating a self-referential one would be a
lie in the bookkeeping.

| | |
|---|---|
| Task | FC-002 |
| Authorization epoch | `95000da5534bb0aed1c0d40e61588e2e11c20fa2` |
| directive_commit | `3c0b81d791cf41a67054b835941c7bceeeadff6e` |
| Task branch | `claude/po-handoff-release` |
| Prior reviewed implementation | `f1fa382b0823100a3bc9520f104b4e793052093d` |
| Integrated production | `0a5d24a13309f79386f69cfa575b48336df445eb` |
| Deployment Authorization | `test-only` — nothing is promoted or deployed |

## Scope implemented

Product Owner correction 2 of 2026-09-07, findings 1–6. Each is a case where
the system reported confidence it had not earned.

1. **Readiness could not distinguish the hub from an error page.** The check
   asked for `"<html"` and the substring `"app"`, which
   `<html><body>Application unavailable</body></html>` satisfies. Reproduced
   as a PASS at `f1fa382`; it fails now. The entry page must carry the
   dashboard's own structure markers, and the same-origin JS/CSS it declares
   must load with a plausible content type and a body that is not an HTML
   fallback. Only relative paths the page itself names are fetched.
2. **Missing verification silently cleared attention.** A successful deploy
   with no readiness record published `not_run` / `attention_required=false`.
   Anything other than a pass on the commit in question now holds attention,
   and `deploy.sh` moves a success through an explicit `pending` state so a
   new deployment cannot wear the previous commit's pass.
3. **A partial Compose failure kept the previous success as
   `running_commit`.** It is now `null` with
   `running_state: unknown_partial_start`; the previous success is kept
   separately as `last_success_commit`; the stale verification is
   invalidated. No automatic rollback was added.
4. **Readiness hardening.** Malformed health entries produce a structured
   fail (required) or degraded (optional) rather than an uncaught exception,
   and required-service readiness is retried inside one shared time budget.
5. **Full-separation activation plan**, planning only —
   `docs/ACTIVATION-PLAN.md`. Four actors, four credentials, ref-scoped
   rulesets and per-clone pre-push hooks. Installed, enabled and verified are
   reported as three separate states. No identity is inferred from commit
   authorship. The missing `frankenstein-deploy` unit template is added and
   `tests/test_unit_templates.py` holds every template to its own paths.
6. **Release-source SHA reported separately** from the candidate SHA.
   `release-service.sh` records the SHA of the checkout it is itself running
   from; the publisher reports `release_service.source_commit` and
   `matches_production`.

Current production `0a5d24a` is merged in, so this candidate integrates the
observed production ancestry rather than fast-forwarding over it.

## What is NOT claimed

- No end-to-end deployment has been observed. `verification` has never been
  exercised inside a real deploy, so **unattended readiness is not claimed**.
- The status publisher remains **not installed**; the `status` branch remains
  absent on the remote.
- Nothing in the activation plan has been executed: no Unix user, no
  credential, no ruleset, no unit installation, no spend.
- Deployment Authorization remains `test-only`. Nothing was promoted or
  deployed, and neither `control` nor `production` was written.
