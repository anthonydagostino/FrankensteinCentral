# Protocol bootstrap migration (one time)

Moving the OptiPlex from **branch-following** deployment to **production-only**
deployment. This is a protocol-infrastructure migration, not a product task: it
is deliberately *not* recorded as FC-001, and `STATE.json` stays neutral.

Run it once. Afterwards every change obeys the normal protocol.

## Why a bootstrap is needed

The corrected poller lives in commit `98aab13`, but the deployment mechanism
that would install it is the *old* branch-coupled one. The new rules cannot
install themselves under the old rules without a deliberate step.

## The hazard this ordering avoids

The old poller tracks whatever branch is checked out on the box — the same
branch task work is pushed to. So `98aab13` may **already** have auto-deployed.
If it did, the box is now running the new `autopull.sh`, which watches
`production` (still at `bf6192e`). Its next poll would see
`HEAD(98aab13) != origin/production(bf6192e)` and *roll the box backwards* to
`bf6192e`, whose `autopull.sh` is the old one again.

That is not dangerous — both commits are process-only and the test gate runs
either way — but it is confusing, and it leaves the boundary resting on
accident rather than configuration.

**Promoting `98aab13` to `production` resolves it in one move**: production and
the box converge on the commit that contains the correct poller, with the
systemd unit pinned to `production` explicitly.

## Order of operations

1. **Inspect** the real box state — do not assume (step 1 below prints it).
2. **Pin** `FRANKENSTEIN_BRANCH=production` in the systemd unit, and prove the
   unit environment actually carries it.
3. **Promote** `98aab13` to `production` (fast-forward from `bf6192e`).
4. **Verify** the box converges: running SHA, `deployed.json`, poller version.
5. **TEST A** — push a throwaway branch, wait a full poll cycle, prove nothing
   deployed.
6. **TEST B** — already demonstrated by step 3; if it had already happened,
   use a process-only marker commit instead.
7. **Delete** the throwaway branch.

Pinning before promoting means the box is already configured correctly when the
production change lands, so there is exactly one convergence rather than two.

## Rollback for the migration itself

Nothing here touches containers, volumes, data, `.env`, or git history.

```bash
# undo the systemd pin
sudo systemctl edit --full frankenstein-deploy.service   # remove the Environment line
sudo systemctl daemon-reload

# force a specific commit onto the box immediately, bypassing the poller
bash ~/FrankensteinCentral/scripts/deploy.sh <branch>
```

`production` only ever moves forward, so no rollback of the branch is needed;
if the deployed code is wrong, use `scripts/rollback.sh`.

## What "done" looks like

- systemd environment contains `FRANKENSTEIN_BRANCH=production`
- `origin/production` == `98aab13`
- `~/.frankenstein/deployed.json` shows `production_branch: production`,
  `running_commit: 98aab13`, `last_result: success`
- pushing a throwaway branch changes **neither** `running_commit` **nor**
  `last_success_at`
- containers keep running throughout
