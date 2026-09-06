# Implementation Handoff — FC-002 (bootstrap reliability corrections)

**This is protocol infrastructure, not an FC product task.**

This file is the in-branch record. The canonical, reviewable handoff — with
the exact final SHA — is published on the `handoff` branch, because a commit
cannot contain its own SHA and fabricating a self-referential one would be a
lie in the bookkeeping.

| | |
|---|---|
| Task | FC-002 |
| Authorization epoch | `760f4c2694dfe6931c567cfb95142b1ccfa4df87` |
| directive_commit | `a4bb8c61c1819cfc5bd53a814258dce1ec027d9e` |
| Task branch | `claude/po-handoff-release` |
| Prior reviewed implementation | `093f73d6927f30bbfde36d388bc8c6c3be5521e8` |
| Deployment Authorization | `test-only` — nothing is promoted or deployed |

## Scope implemented

The Product Owner's correction to FC-002, which explicitly expanded scope
beyond the original binding-only limit:

1. `scripts/readiness.sh` — a bounded, read-only post-deploy readiness check
   of the core dashboard (gateway serves the page, the app catalog is valid,
   required local services are up). Optional third-party integrations are
   reported as **degraded**, never fatal.
2. `scripts/deploy.sh` runs it after a successful Compose start, with finite
   retries, and records the result against the exact deployed commit. No
   automatic rollback.
3. The test gate's disposition is recorded independently of the deploy
   result, so `DEPLOY_SKIP_TESTS=1` can no longer report a passing gate.
4. `scripts/status-publisher.sh` ties a verification verdict to the commit it
   was performed on; a verdict for another commit is reported as `stale`.
5. Canonical handoff files are published on `handoff`.
6. Rebound to the epoch and directive above.
7. `docs/ACTIVATION-PLAN.md` — the installation and validation plan.

No product features. No credential, account, host or security-boundary
changes. See the `handoff` branch for evidence and remaining blockers.
