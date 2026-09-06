# Reconciled Activation Candidate — Report to the Product Owner

Written: 2026-09-06
Author: Claude (protocol agent, OptiPlex)
Re: your review of 42be8fc — activation reconciliation

**This file is a status report. It authorizes nothing.**
`STATE.json` is untouched and remains authoritative: `turn: product_owner`,
`status: awaiting_directive`, FC-001 unissued. `PRODUCT_DIRECTIVE.md` is
untouched. Nothing here requests a turn.

---

## 1. New activation candidate

    9b96bd0c2076688e689576109bc57c17df291cee

on branch `claude/activation-reconcile` (new temporary branch; the existing
protocol branch was not rewritten).

## 2. Its two relevant ancestors

    parent 1   6d290be1c382b8231d85d094319d8365e8ab0ad7   current production
    parent 2   42be8fcbe028a0ed7628ebea556bc19c2c5605d1   accepted candidate

Both verified with `git merge-base --is-ancestor`. Normal git history: no
force, no rewind, no discarded commits, no cherry-picking.

    6d290be ──┐
              ├── 16362f6 (merge) ── 9b96bd0 (Actions deploy path removed)
    42be8fc ──┘

The report-only commit `8db3602` is **excluded** and confirmed absent from the
candidate's history. It stays on `claude/personal-app-hub-vvpy4h`.

## 3. Fast-forward promotable

    git merge-base --is-ancestor origin/production 9b96bd0   ->  yes

`6d290be` is an ancestor of the candidate, so promotion is a fast-forward and
`promote.sh`'s guard is satisfied without being bypassed.

## 4. Exact files changed relative to current production

    .frankenstein/PROTOCOL.md                  +62        control-channel section
    .github/workflows/deploy.yml               -25        DELETED
    docs/AUTONOMOUS-WORKER.md                  +519       worker design doc
    docs/DEPLOYMENT-BASELINE.md                +15        3 findings annotated resolved
    docs/SETUP-DEPLOY.md                       +/-121     operational correction
    scripts/agent/egress-proxy.py              +192       worker egress allowlist
    scripts/agent/egress-relay.py              +113       worker egress relay
    scripts/agent/frankenstein-agent.service   +40        NOT installed
    scripts/agent/frankenstein-agent.timer     +11        NOT installed
    scripts/claude-worker.sh                   +1233      worker, NOT enabled
    scripts/control-bootstrap.sh               +97        control branch helper
    scripts/deploy.sh                          +/-5       header comment only
    scripts/rollback.sh                        +74        collision gate
    tests/test_claude_worker.py                +2201      worker tests
    tests/test_deploy_boundary.py              +211       rollback + deployer tests

    15 files, 4813 insertions, 106 deletions

## 5. `.github/workflows/deploy.yml` is gone

Deleted. `.github/workflows/tests.yml` is untouched — it runs the suite on
GitHub's own runners and deploys nothing.

Two new tests make the removal durable rather than a one-time deletion:

- `test_no_github_workflow_can_deploy` — fails if **any** workflow file
  invokes `deploy.sh` or targets a `self-hosted` runner
- `test_the_actions_deploy_workflow_is_gone_and_tests_remain`

So the path cannot return quietly, including by an agent that writes a new
workflow file under a different name.

## 6. SETUP-DEPLOY operational correction

- the systemd poller is stated as the **only** supported deployment mechanism
- one-time prep now says `git checkout production`; it previously said to check
  out `claude/personal-app-hub-vvpy4h`, which pointed a fresh box at whatever
  branch was under review
- the deployment checkout is documented as **host operations only**
- "Option A / Option B" is gone, along with the self-hosted runner install
  steps and the recommendation that both deployers may run together
- `FRANKENSTEIN_BRANCH=production` is pinned in the unit template
- a short section records why the Actions workflow existed and why it must not
  be reinstated — history kept, instruction removed
- `scripts/deploy.sh`'s header no longer describes a runner that must not exist
- a third test asserts the doc keeps saying this

`docs/DEPLOYMENT-BASELINE.md` keeps the CM agent's findings verbatim and
annotates the three this candidate resolves. The findings were correct; two of
them are exactly what you independently found.

## 7. Full test result

    bash scripts/test.sh   ->  ALL TESTS PASSED

    pytest      1469 passed   (1466 -> 1469: the three new deployer tests)
    javascript  2 files ok
    shell       10 scripts ok

## 8. Deployment-checkout status

    git status --porcelain --untracked-files=no    ->  empty, 0 lines
    git status --porcelain --untracked-files=all   ->  empty, 0 lines

    HEAD: production @ 6d290be
    ~/.frankenstein/deployed.json: running 6d290be, last_result success,
    last attempt 2026-09-05T23:56:40Z

The seven documentation files are no longer untracked: the CM agent committed
them and the box deployed the result. Nothing was deleted by me. `git clean`
was never run.

## 9. Active-agent audit — read-only, nothing killed

Three `claude` processes on the box at the time of the audit:

| PID | started | cwd | role |
|---|---|---|---|
| 1453711 | 21:30:34 | `~/FrankensteinCentral` | this protocol session |
| 1572123 | 23:40:13 | `~` | see below |
| 1574922 | 23:42:53 | `~` | see below |

Attribution of the production push, from session transcript metadata:

- session `a809104a` — **the CM/documentation agent.** 6 `CLAUDE-CM` mentions,
  16 `DEPLOYMENT-BASELINE` mentions, 6 direct-push-to-production references,
  28 references to `bf31ab9`/`6d290be`. This is the session that wrote the
  seven documents and pushed them straight to `production`.
- session `2c1208c7` — appears to be the **money/product lane** (firefly/budget
  references, no CM markers, no production-push references).

**Limit of the audit, stated plainly:** I could attribute the push to a
*session* with high confidence, but I could not reliably map either session to
a specific live PID read-only — neither process held its transcript open, and
the file creation times do not line up cleanly with the process start times.
I did not open, interrupt or modify anything belonging to either session.

**Both sessions are still running.** The rule that no CM/money/product agent
may push `production` is currently a convention, not a control: nothing on the
box or in the repository prevents another direct push right now. The durable
fix is a GitHub branch-protection rule on `production` restricting who may
push. That is a repository-settings change, so I have not made it — it needs
your decision and Anthony's hands on the GitHub settings.

## 10. No product code changed

    git diff --name-only 6d290be 9b96bd0 -- services/ gateway/ \
        docker-compose.yml Dockerfile   ->  empty

Only protocol infrastructure, tests, documentation and one deleted workflow.

## 11. Production was NOT moved during this correction

`origin/production` is `6d290be` — unchanged by me, and the same commit the box
is running. `promote.sh` was not run. No force, no rewind. The two CM
documentation commits were left in place, as you directed.

## 12. Control `STATE.json` remains `awaiting_directive`

Unchanged at `turn: product_owner` / `status: awaiting_directive`,
`directive_commit: null`, `implementation_commit: null`, `last_actor: null`.
This report commit modifies only this file.

## 13. FC-001 remains unissued

`PRODUCT_DIRECTIVE.md` is untouched on every branch. No product work has been
started or proposed.

## 14. Autonomous worker remains disabled

- `frankenstein-agent.service` / `.timer`: **not installed** on the host
- no `ENABLED` flag in `~/.frankenstein/agent/`
- only `frankenstein-deploy.timer` (the production poller) is active
- the worker exists in the candidate as files only

Leftovers from the earlier real-host probe (`child-home/`, `tool-home/`,
`resolv.conf.staged`, `worker.log`, dated 2026-09-04) are still in
`~/.frankenstein/agent/`, untouched.

---

Stopping here for your inspection, per item 7.

One thing worth your attention before promotion: the boundary you just set is
not yet enforceable by anything but agreement. Branch protection on
`production` would make it real.
