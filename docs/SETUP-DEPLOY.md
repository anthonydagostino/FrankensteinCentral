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
