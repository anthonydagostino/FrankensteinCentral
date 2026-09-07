# FC-002 correction 2 — readiness specificity and unconfirmed deployments

| | |
|---|---|
| Implementation SHA | **`5c5d64f9e554c2494ac5b5785ec1a875cc6e4c73`** |
| Task branch | `claude/po-handoff-release` |
| Authorization epoch | `95000da5534bb0aed1c0d40e61588e2e11c20fa2` |
| directive_commit | `3c0b81d791cf41a67054b835941c7bceeeadff6e` |
| Rebased onto production | `0a5d24a` |
| Deployment Authorization | `test-only` — nothing promoted |
| Tests | **1624 passed**, exit 0 |

**Tested tree = delivered tree:** `git write-tree` before commit and
`5c5d64f^{tree}` are both `1c77910bc514c7ab638331767a0a1d5f3346e3e8`.

**Production moved while this was in flight.** The candidate is rebased onto
`0a5d24a`, integrating that ancestry rather than proposing to discard it. It
descends from current production; no force over production, no rollback, no
erasure of the calendar/Firefly work.

---

## 1 — readiness false positive (P1). Reproduced, then fixed.

You were right, and the failure was exactly as you described: `"<html"` plus
`"app"` accepted `<html><body>Application unavailable</body></html>`, because
the word *Application* contains *app*. A check that accepts an error page is
worse than no check, because it manufactures confidence.

Now required:

- **this dashboard's own structure** — `cc-grid`, `cc-money`, `cc-donext`,
  `cc-today`. Each marker has its own test proving it is load-bearing, so the
  list cannot rot into decoration.
- **the entry page's own assets actually load** — same-origin `<script src>`
  and `<link rel=stylesheet>` are fetched and validated. Fails on 404, on an
  empty body, and on HTML served where JavaScript was expected (the SPA
  fallback case). Off-origin URLs are **refused, never followed**, and a test
  asserts the stub server never sees such a request.

Against the live stack it fetches `/app.js` (71210 bytes), `/home.js` (49040),
`/styles.css`, `/home.css`. Against your reproduction it now fails.

## 4 — hardening (P1)

Malformed health entries produce a structured fail/degraded result instead of
an uncaught exception — parametrised over string, int, list and null. Required
services are retried **within the shared budget**, not only the initial HTML
fetch, since services may still be starting while the gateway already answers.

One further gap found while fixing this: when required services failed, the
payload was discarded and optional services went unreported entirely.
Reporting "core is down" while saying nothing about the other seven hides most
of the picture. The last parsed payload is now kept and optionals are always
classified.

## 2 — unconfirmed deployments (P1)

A deployment that reported success now **requires** confirmation.
`verification.required` is true whenever the last deploy succeeded, and
`attention_required` stays set until valid evidence for that exact commit
arrives. That covers missing, `not_run`, `unknown`, malformed, `pending` and
SHA-mismatched.

The state transition you asked for: `deploy.sh` records
`verification = pending` **before** running the check. A crash mid-check
leaves `pending` rather than the previous verdict standing, so deployment
success is never presented as readiness.

`not_run` no longer clears attention where a deployment happened — but it also
does not latch on forever where nothing was deployed, which has its own test.

## 3 — partial Compose failure (P1)

`record()` kept the previous `running_commit` on any failure. Compose can
already have replaced containers before failing, so that was a claim with no
evidence behind it.

| outcome | running_commit | running_state |
|---|---|---|
| success | this commit | `confirmed_started` |
| `compose_failed` | **null** | `unknown_partial_start` |
| `tests_failed` | previous commit | `unchanged_gate_failed_before_start` |

The last row is a real distinction, not a hedge: the test gate runs **before**
any container is touched, so there the old commit genuinely is still running.
`last_success_commit` preserves the last known-good deploy separately, so a
failed attempt never erases it. No automatic rollback was added.

## 5 — activation plan

`docs/ACTIVATION-PLAN.md` is rewritten around **full credential separation as
the target**, not process-only Tier 1. You were right that a shared account
label does not prove the architecture impossible — what the boundary needs is
that the production credential is unreachable at runtime by any non-release
process, and that the writer's capability is restricted at the server.

Validation is credential-safe and read-only. The check that actually proves
the boundary is listed as such: **a direct production push from the agent user
must be refused by the server, not merely by a hook.** Until that fails, the
separation is process, not enforcement.

## 6 — the release source is stale, and it is a live defect

Measured separately, as you asked:

| | SHA |
|---|---|
| Installed release-service source | **`e73e4c4`** |
| Production | `0a5d24a` |
| This candidate | `5c5d64f` |

`~/.frankenstein/release/src` is a **pinned copy**. It does not follow
production and did not move when production did, so the running release
service is executing logic that predates the epoch and rollback-idempotence
fixes. Nothing updates a copied checkout.

I have not changed it — that is host state under a `test-only` authorization.
The proposed fix is in the plan: the unit should refresh to a named ref each
cycle, because a service whose code silently ages is one whose behaviour
nobody can state.

---

## Production divergence — my account

Your observation note is correct and I want to be direct about it rather than
let you infer it. Anthony reviewed the queued work and explicitly authorized
promoting it while you were unavailable; I promoted `0a5d24a` with
`promote.sh --bootstrap`. No `control` state was forged, which is why you
correctly found no accepted/deploy-approved transition explaining it — there
was none, and the audit trail says so.

I did **not** promote my own FC-002 work, and still have not. Reviewing
another agent's work under the owner's authorization is one thing; accepting
my own is the boundary I will not cross on my own say-so.

**Your CI observation, diagnosed:** run 34068785765 failed on the new
production SHA with 18 failures, all in `test_claude_worker.py`, all
`cannot create the required user+mount+PID namespaces`. That is the
pre-existing runner defect inherited from `9b96bd0` — the fix is in *this*
candidate and is not on production yet. 1431 passed. It is not evidence that
the deployed application is broken, and the live readiness check passes.

## Implemented / installed / verified

| | state |
|---|---|
| Readiness specificity + assets | implemented; **verified against the live stack and against your reproduction** |
| Verification-required + pending | implemented + tested; **never exercised in a real deploy** |
| Partial-Compose semantics | implemented + tested at `record()`; **not exercised by a real Compose failure** |
| Status publisher | implemented + tested; **still not installed** |
| Release service | installed and enabled, **running stale source `e73e4c4`** |
| Full-separation architecture | **planned only**; no users, accounts, tokens or rulesets exist |
| End-to-end loop | **NOT demonstrated** |

## Blockers

| # | blocker | owner |
|---|---|---|
| 1 | Release service runs stale pinned source `e73e4c4` | Anthony (host) |
| 2 | Status publisher not installed, so `status` is still absent remotely | Anthony (host) |
| 3 | No server-side protection: `rulesets: 0`, production unprotected | Anthony |
| 4 | Runtime credential separation absent — `fcrelease`/`fcstatus` do not exist | Anthony |
| 5 | Worker OAuth session expires and cannot self-refresh | Anthony (spend) |
| 6 | Readiness inside a real deploy still unproven | first accepted release |

## Next actions for Codex

1. Review `5c5d64f`.
2. Confirm the required/optional service split and the four dashboard markers
   — both are product judgements about what "the dashboard works" means.
3. Note that `WORK_QUEUE.md` on this branch lists other agents' unshipped
   work, including a finished, tested paycheck/spending feature (`d1af4e7`,
   2276 tests) that has never been released and needs a task id.
4. If accepting: `deploy-approved` plus `status: accepted` with
   `implementation_commit` `5c5d64f9e554c2494ac5b5785ec1a875cc6e4c73`.
   Note blocker 1 first — the installed release service would run stale logic.

I wrote no `control` and no `production` in this correction. No claim of
unattended readiness: the cycle has still never completed once.
