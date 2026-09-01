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

## Rollback

The change is two files and one branch; nothing destructive was done. No
containers, volumes or data were touched, and no git history was rewritten.

```bash
# restore the previous poller behavior (tracks the checked-out branch)
cd ~/FrankensteinCentral
git checkout <commit-before-this-change> -- scripts/autopull.sh

# or force a specific commit onto the box immediately, bypassing the boundary
bash scripts/deploy.sh <branch>

# if the unit was edited, drop the override
sudo systemctl edit --full frankenstein-deploy.service   # remove the Environment line
sudo systemctl daemon-reload
```

The production branch can also simply be moved back:

```bash
git push origin <older-good-sha>:refs/heads/production --force-with-lease
```

That is a deliberate rollback of what is running, and the box picks it up on
the next poll.

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
