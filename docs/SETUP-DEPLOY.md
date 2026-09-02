# Auto-deploy: push code → the box updates itself

Your OptiPlex lives behind your home network, so GitHub's cloud can't reach in
to it. The trick is to run a small agent **on the box** that connects out to
GitHub and does the deploy. Two ways to do it — pick one. **Option A** is the
real CI pipeline (deploys show up in the repo's Actions tab). **Option B** needs
no GitHub setup at all.

Both reuse `scripts/deploy.sh`, which does: `git fetch` → `git reset --hard` →
`docker compose up -d --build`. Your `.env` and Docker volumes are untracked, so
they survive every deploy.

---

## One-time prep (both options)

On the box, have the repo cloned once at `~/FrankensteinCentral` with your real
`.env` in it:

```bash
cd ~
git clone https://github.com/anthonydagostino/FrankensteinCentral.git
cd FrankensteinCentral
git checkout claude/personal-app-hub-vvpy4h
cp .env.example .env      # then edit .env (Firefly URL/token, etc.)
chmod +x scripts/*.sh
```

Make sure your user can run Docker without sudo:
```bash
sudo usermod -aG docker "$USER"   # log out/in once after this
```

> If you cloned somewhere else, set `FRANKENSTEIN_DIR=/your/path` in the runner
> or timer environment below.

---

## Option A — GitHub Actions self-hosted runner (the pipeline)

This registers the box as a runner. Every push then runs `.github/workflows/
deploy.yml` on it automatically, with full logs in **GitHub → Actions**.

1. In the repo on GitHub: **Settings → Actions → Runners → New self-hosted
   runner → Linux / x64**. GitHub shows you download + `config` commands with a
   one-time token. Run them on the box, but add the **`frankenstein` label**:

   ```bash
   mkdir -p ~/actions-runner && cd ~/actions-runner
   # (use the download line GitHub shows you — version changes over time)
   curl -o runner.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
   tar xzf runner.tar.gz
   ./config.sh \
     --url https://github.com/anthonydagostino/FrankensteinCentral \
     --token <TOKEN_FROM_GITHUB> \
     --labels frankenstein \
     --name optiplex \
     --unattended
   ```

2. Install it as a service so it runs on boot and survives reboots:
   ```bash
   sudo ./svc.sh install
   sudo ./svc.sh start
   ```

3. (If your clone isn't at `~/FrankensteinCentral`) tell the runner where it is:
   ```bash
   echo "FRANKENSTEIN_DIR=/your/path/FrankensteinCentral" | sudo tee -a ~/actions-runner/.env
   sudo ./svc.sh stop && sudo ./svc.sh start
   ```

Done. Push anything to `claude/personal-app-hub-vvpy4h` (or `main`) and the box
redeploys within seconds. You can also trigger it by hand from the Actions tab
(**Run workflow**).

---

## Option B — no GitHub setup, just poll (simplest)

A systemd timer runs `scripts/autopull.sh` every minute; if the branch has new
commits, it redeploys. No runner, no token, nothing inbound.

Create the service and timer (run as your user, `<USER>` = `whoami`):

```bash
sudo tee /etc/systemd/system/frankenstein-deploy.service >/dev/null <<EOF
[Unit]
Description=FrankensteinCentral auto-deploy
After=docker.service
[Service]
Type=oneshot
User=$(whoami)
Environment=FRANKENSTEIN_DIR=$HOME/FrankensteinCentral
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

---

## Which should I use?

- **Want it to feel like a real pipeline** with a history and green checkmarks in
  GitHub? → **Option A**.
- **Just want it to work with zero GitHub fuss?** → **Option B**.

You can even run both; they both just call `deploy.sh`, and the `concurrency`
guard + git checks keep them from stepping on each other.

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

## Recommended: make the branch explicit on the box

The default already gives the correct behavior, but pinning it in the unit
removes any doubt:

```bash
sudo systemctl edit --full frankenstein-deploy.service
# add under [Service]:
#   Environment=FRANKENSTEIN_BRANCH=production
sudo systemctl daemon-reload
```

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
