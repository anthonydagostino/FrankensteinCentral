# Connecting the Vault app to your Vaultwarden

The Vault sub-app is a **read-only health dashboard**. It never stores your
passwords — your vault stays in Vaultwarden. The app just reads your items,
computes health (weak / reused / old / no-2FA / insecure URL), and shows
metadata + issue flags. Actual passwords are never sent to the browser and never
written to the hub's database (the Vault service has no database at all).

Until it's connected it shows an empty **"not connected"** state (no sample data).

## The plan (when you're home)

1. **Run Vaultwarden** on the homelab (Docker):
   ```yaml
   # somewhere on your homelab
   services:
     vaultwarden:
       image: vaultwarden/server:latest
       restart: unless-stopped
       volumes: [ "./vw-data:/data" ]
       ports: [ "8222:80" ]
   ```
   Create your account / import your passwords in the web vault at
   `http://<homelab-ip>:8222`.

2. **Expose a read API with the Bitwarden CLI's `bw serve`.** The hub reads the
   vault through the official Bitwarden CLI running in "serve" mode, pointed at
   your Vaultwarden:
   ```bash
   sudo npm install -g @bitwarden/cli   # or: sudo snap install bw
   bw config server http://<homelab-ip>:8222
   bw login                       # your Vaultwarden email + master password
   export BW_SESSION=$(bw unlock --raw)   # unlock; keep this session
   nohup bw serve --hostname 172.17.0.1 --port 8200 >/dev/null 2>&1 &
   ```
   Two deliberate choices here:
   - **`--hostname 172.17.0.1`** (the docker bridge) — reachable by the host
     and the hub's containers but **NOT by other devices on your LAN**. Never
     use `0.0.0.0`: an unlocked `bw serve` answers with real secrets to anyone
     who can reach the port.
   - **port 8200** — NOT the CLI's default 8087, which collides with the
     hub's Tasks service on this box.

   `bw serve` stays unlocked until the process exits (e.g. a reboot) — after a
   reboot, re-run the `export BW_SESSION=…` and `nohup bw serve …` lines.

3. **Point the hub at it.** In the FrankensteinCentral `.env`:
   ```
   VAULT_MODE=bitwarden
   BW_SERVE_URL=http://172.17.0.1:8200
   ```
   Then `docker compose up -d vault assistant`.

4. Open **Vault** on the hub — you'll see your real password health, and Vic
   will report it on the floor ("5 weak, 3 reused"). Bones flags reused
   passwords in your briefing.

## What it checks

- **Weak** — under 12 chars, common password, or low character variety
- **Reused** — the same password on more than one login
- **Old** — not changed in over a year
- **No 2FA** — a login with no TOTP set
- **Insecure URL** — an `http://` (not `https://`) site

## Security notes

- The Vault service holds nothing on disk and exposes no endpoint that returns a
  password — only counts and per-item issue flags.
- `BW_SESSION` / master password live only on the `bw serve` process on your
  homelab, never in this repo.
- Keep `bw serve` bound to the docker bridge (172.17.0.1) — it must never be
  reachable from the LAN or the internet, because an unlocked `bw serve`
  returns real secrets to anyone who can reach it.

## Where the guard checks, and where it doesn't

Two different things check `bw serve`'s binding, deliberately split (SCRUM-133):

- **`tests/test_vault_serve_examples.py`** checks the **instructions** — this
  doc, `.env.example`, and the service docstring. It is hermetic: it never
  reads `.env`, because a machine's own configuration is not an example, and a
  repo test cannot know which docker bridge that machine uses. It once did read
  it, was therefore green in CI (where `.env` is gitignored and absent) and red
  on the OptiPlex (where it exists and correctly names a different bridge), and
  it blocked every deploy until that was found.
- **`scripts/verify.sh`** checks the **running value**, on the box, where the
  machine is. Loopback or a docker bridge (172.16.0.0/12) passes. `0.0.0.0`, a
  public address, or a LAN address (192.168/16, 10/8) fails — a LAN binding is
  the specific exposure this page warns about, and "private" is not the test,
  since LAN addresses are private and still reach every device on the network.

Your own bridge address depends on your compose project. Find it with:

```bash
docker network inspect frankensteincentral_default \
  --format '{{ (index .IPAM.Config 0).Gateway }}'
```
