# Auto-deploy: production moves → the box updates itself

Your OptiPlex lives behind your home network, so GitHub's cloud cannot reach in
to it. A systemd timer on the box connects **out** to GitHub once a minute,
notices when the `production` branch has moved, and redeploys.

That poller is the **only** supported deployment mechanism. There is no second
deployer. `scripts/deploy.sh` does `git fetch` → `git reset --hard` → test gate
→ `docker compose up -d --build`; your `.env` and Docker volumes are untracked,
so they survive every deploy.

**Pushing code is not deploying it.** The poller watches `production` and
nothing else, so task branches can be pushed freely for review. Only
`scripts/promote.sh`, run after Product Owner acceptance, moves `production`.

---

## One-time prep

On the box, clone the repo once at `~/FrankensteinCentral` with your real
`.env` in it. **Check out `production`** — that is the branch the box deploys,
and pointing the deployment checkout at a task branch is how a review push
becomes a live deploy:

```bash
cd ~
git clone https://github.com/anthonydagostino/FrankensteinCentral.git
cd FrankensteinCentral
git checkout production
cp .env.example .env      # then edit .env (Firefly URL/token, etc.)
chmod +x scripts/*.sh
```

Make sure your user can run Docker without sudo:
```bash
sudo usermod -aG docker "$USER"   # log out/in once after this
```

> If you cloned somewhere else, set `FRANKENSTEIN_DIR=/your/path` in the timer
> environment below.

This checkout is **host operations only**. Nothing is developed in it: agents
and humans work in their own clones and push branches to GitHub.

---

## Install the poller

A systemd timer runs `scripts/autopull.sh` every minute; when
`origin/production` differs from the last successful deploy, it redeploys. No
runner, no inbound access, no token.

```bash
sudo tee /etc/systemd/system/frankenstein-deploy.service >/dev/null <<EOF
[Unit]
Description=FrankensteinCentral auto-deploy
After=docker.service
[Service]
Type=oneshot
User=$(whoami)
Environment=FRANKENSTEIN_DIR=$HOME/FrankensteinCentral
Environment=FRANKENSTEIN_BRANCH=production
ExecStart=/usr/bin/env bash $HOME/FrankensteinCentral/scripts/autopull.sh
EOF

sudo tee /etc/systemd/system/frankenstein-deploy.timer >/dev/null <<EOF
[Unit]
Description=Check for new FrankensteinCentral code every minute
[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now frankenstein-deploy.timer
```

Check it: `journalctl -u frankenstein-deploy.service -f`

`FRANKENSTEIN_BRANCH=production` is the default anyway; pinning it in the unit
removes all doubt about which branch the box follows.

---

## There is deliberately no GitHub Actions deploy

`.github/workflows/deploy.yml` used to deploy on every push to
`claude/personal-app-hub-vvpy4h` and `main`, passing `GITHUB_REF_NAME` straight
into `deploy.sh` — a review push became a production deploy, which is exactly
what the production boundary exists to prevent. `autopull.sh` was fixed; that
workflow was not, and it stayed dormant only because no self-hosted runner
happened to be installed.

**The workflow has been deleted.** Do not reinstate it, and do not install a
self-hosted deploy runner: a second deployer that can be reactivated by anyone
who registers a runner is not a boundary. This matters more now that an
autonomous worker pushes task branches on its own.

`.github/workflows/tests.yml` remains and is unaffected — it runs the suite on
every push and PR on GitHub's own runners, and deploys nothing.

---

# The review/production boundary (current design)

**Pushing code to GitHub does not deploy it.** The poller watches one branch —
`production` — and nothing else. Task branches can be pushed freely so the
Product Owner can review a real diff before anything reaches the box.

```
claude/FC-###-<slug>   pushed freely, deploys nothing, reviewable on GitHub
        │
        │  Product Owner accepts + directive says deploy-approved
        ▼
scripts/promote.sh     fast-forward only
        │
        ▼
production             the ONLY branch the OptiPlex deploys (~60s later)
```

## What changed, and why

`autopull.sh` used to default to `git rev-parse --abbrev-ref HEAD` — whatever
branch happened to be checked out on the box. The systemd unit never set
`FRANKENSTEIN_BRANCH`, so in practice the poller tracked the same branch
implementation was pushed to: **every review push went straight to
production.** It now defaults to `production` and, if that branch is missing,
deploys nothing and says so in the journal rather than falling back.

`production` was created at `bf6192e` — the exact commit already running — so
introducing the boundary deployed nothing and changed nothing about the live
stack. `main` was not used: it holds only the initial commit, 83 behind.

## Verifying the boundary on the box

```bash
# 1. a task-branch push must NOT deploy
git push origin HEAD:refs/heads/throwaway-boundary-test
sleep 90
journalctl -u frankenstein-deploy.service --since '-2 min' | grep -i redeploy   # expect nothing
git push origin --delete throwaway-boundary-test

# 2. what is running right now
cat ~/.frankenstein/deployed.json
bash scripts/frankenstein-status.sh
```

Use a harmless throwaway branch for this — never a product change.

## Rollback — production moves FORWARD

Production history is append-only. The normal rollback does **not** rewind the
branch; it adds a new commit whose tree is the known-good one, so the box
deploys it like any other change and the bad deploy stays in the audit trail:

```
production:  A --- B --- C(bad) --- D(tree of B, "rollback")
```

```bash
bash scripts/rollback.sh --dry-run <known-good-sha>   # show what would change
bash scripts/rollback.sh <known-good-sha>             # commit + push it
```

For a single bad commit a plain `git revert` is equally fine and reads more
clearly in history:

```bash
git revert --no-edit <bad-sha>
git push origin HEAD:refs/heads/production
```

Use `rollback.sh` when several commits need undoing at once, or when the
"restore this exact known-good tree" intent is clearer than a chain of reverts.

Other non-destructive options:

```bash
# restore the previous poller behavior
git checkout <commit-before-this-change> -- scripts/autopull.sh

# deploy a specific commit by hand on the box (bypasses the poller, changes
# nothing about branches)
bash scripts/deploy.sh <branch>
```

### Emergency only — rewriting production

```bash
git push origin <older-sha>:refs/heads/production --force-with-lease
```

This is **not** the operational rollback path. It erases the record that the
bad deploy ever happened and can desynchronize any other clone. It is a
high-risk action under the protocol and requires explicit approval; prefer
`rollback.sh` or `git revert` in every ordinary case.

## What is actually running

`deploy.sh` writes `~/.frankenstein/deployed.json` (outside the repo, because
`git reset --hard` would erase anything tracked):

```json
{"production_branch": "production", "running_commit": "…",
 "last_attempt_commit": "…", "last_attempt_at": "…",
 "last_result": "success", "last_success_at": "…"}
```

A failed deploy records `tests_failed` and leaves `running_commit` pointing at
the last good build — the test gate runs before containers are touched.

**What "already deployed" means.** The poller compares `origin/production`
(desired) against `running_commit` (last successful deploy), never local HEAD.
`deploy.sh` checks out and resets to production *before* running tests, so
after a failed gate HEAD equals production while the containers still run the
older build. The previous HEAD-based check read that as converged and stopped
retrying — it wedged the box live during the protocol bootstrap. A missing or
unreadable record counts as unknown and triggers a normal test-gated deploy.

Consequence worth knowing: a commit that fails the gate is retried on every
poll (~60s). The gate is cheap and containers are never touched on failure, so
the box keeps serving the last good build while the retries continue. Fix the
commit or roll production forward with `scripts/rollback.sh` to stop them.
