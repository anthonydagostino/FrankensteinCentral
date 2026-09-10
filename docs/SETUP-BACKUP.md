# Backing up the OptiPlex to Backblaze B2

Restic, driven by Backrest, on a schedule, with the databases dumped first and
the whole thing refusing to snapshot if any dump fails. This is SCRUM-14; the
two `[CLAUDE]` halves (the dump script and the Backrest config) are in the
repo, the two `[YOU]` halves (the B2 bucket and the first run on the box) are
below as steps.

**A backup nobody has restored from is a belief.** Step 8 is not optional and
the ticket is not Done until it has been performed once and put on a calendar.

## What gets backed up, and how

`scripts/dump-all.sh` writes a complete, checksummed set to `/srv/dumps/current`:

| target | how | why that way |
|---|---|---|
| **dashboard** | delegates to `scripts/backup.sh`, which writes to `~/frankenstein-backups` as always | already shipped and restore-tested; `restore.sh --drill` and the **Data safety** card (SCRUM-67) read that directory and `data-safety.json`, so it is not redirected — Backrest snapshots it as a second path |
| **firefly** | engine read from the `firefly` container's own `DB_CONNECTION`; `mariadb-dump --single-transaction --quick --all-databases` or `pg_dump -Fc` inside the DB container | the engine is documented nowhere in this repo, so it is discovered, not assumed; the root password is read inside the container and never reaches the host |
| **vaultwarden** | `vaultwarden backup` (its own `VACUUM INTO`), then `/data` minus the live DB as a tarball | copying a WAL-mode SQLite file loses committed rows; keys/attachments/sends are not in the DB |
| **pihole** | `pihole-FTL --teleporter` | the sanctioned export; a zip of every setting |

Then Backrest snapshots `/srv/dumps/current` plus the compose files and `.env`
into an encrypted restic repository on B2.

Failures are loud on purpose: an empty dump is a *failed* dump, a container
that is not running is a *failure* (not a skip), any failure exits non-zero,
and the pre-snapshot hook is `ON_ERROR_FATAL` — so a failed dump **cancels the
snapshot** instead of archiving whatever was on disk. `current` is only ever
replaced by atomic rename of a fully verified set, so the last good dump
survives a bad night.

## 1. B2 bucket (SCRUM-35)

In Backblaze: create a **private** bucket, enable **Object Lock** (compliance
mode, retention e.g. 30 days) so a compromised host cannot delete or encrypt
history. Create an **application key scoped to that bucket** with read + write
(restic needs both; the master key is not required and should not be used).
Note the key ID, the application key, and the bucket's S3 endpoint region
(`s3.<region>.backblazeb2.com`).

Restic recommends B2's S3-compatible API over its native `b2:` backend, which
is why `config.json` uses an `s3:` URI with `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY`.

## 2. A healthchecks.io check

Create one check, period **1 day**, grace **2 hours**. Backrest's native
Healthchecks action pings `<url>/start` when a snapshot begins, `<url>` on
success and `<url>/fail` on error — one URL, all three states, no curl glue.
Prune/check/forget errors on the repo ping `/fail` as well.

## 3. Install Backrest as a native binary

Not in Docker. It must keep working when Docker is broken, and it runs
`docker exec` for the dumps as a member of the `docker` group.

```bash
# Pin a release deliberately; bump on purpose, not by accident.
BR_VERSION=v1.7.3
curl -fsSL "https://github.com/garethgeorge/backrest/releases/download/${BR_VERSION}/backrest_Linux_x86_64.tar.gz" \
  | sudo tar -xz -C /usr/local/bin backrest
sudo chmod 755 /usr/local/bin/backrest
backrest --help >/dev/null && echo "backrest ${BR_VERSION} installed"

sudo usermod -aG docker "$USER"        # log out and in again if this was new
sudo mkdir -p /srv/dumps && sudo chown "$USER:$USER" /srv/dumps
```

Backrest manages its own restic binary; nothing else to install.

## 4. The systemd unit

```bash
cd ~/FrankensteinCentral
sed "s/REPLACE_WITH_USER/$USER/g" scripts/backrest/backrest.service \
  | sudo tee /etc/systemd/system/backrest.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now backrest
systemctl status backrest --no-pager
```

The unit binds the UI to `127.0.0.1:9898` only. Reach it over SSH:
`ssh -L 9898:127.0.0.1:9898 optiplex` then open <http://127.0.0.1:9898>.

## 5. The restic password — read this before step 6

The restic repository is encrypted with a password you choose. **If it is
lost, every backup is unreadable, permanently.** And Vaultwarden is *inside*
this backup — so the password cannot live only in Vaultwarden.

```bash
openssl rand -base64 32
```

Write it down and keep it somewhere that survives the OptiPlex being a brick:
a printed copy, and a second password manager or a sealed note. Then also
store it in Vaultwarden for daily convenience.

## 6. The Backrest config

```bash
mkdir -p ~/.config/backrest
cp scripts/backrest/config.json ~/.config/backrest/config.json
chmod 600 ~/.config/backrest/config.json
${EDITOR:-nano} ~/.config/backrest/config.json
```

Replace every `REPLACE_WITH_*` token:

| token | value |
|---|---|
| `REPLACE_WITH_USER` | your login on the box (`echo $USER`) |
| `REPLACE_WITH_B2_REGION` | from the bucket's S3 endpoint, e.g. `us-west-004` |
| `REPLACE_WITH_B2_BUCKET` | the bucket name |
| `REPLACE_WITH_B2_KEY_ID` / `REPLACE_WITH_B2_APPLICATION_KEY` | the scoped application key |
| `REPLACE_WITH_RESTIC_PASSWORD` | step 5 |
| `REPLACE_WITH_HEALTHCHECKS_UUID` | step 2 |

`/home/<you>/docker` is where the neighbour stacks' compose files are assumed
to live (Firefly, Vaultwarden, Pi-hole…). Adjust the `paths` list if yours are
elsewhere; anything listed there is backed up as files.

```bash
sudo systemctl restart backrest
journalctl -u backrest --since '-1 min' --no-pager
```

On first start Backrest initialises the repository (`autoInitialize`) and
fills in its `guid`. Do not add a `guid` by hand — the two are mutually
exclusive and the config is rejected if both are set.

## 7. First run — by hand, before trusting the schedule

```bash
cd ~/FrankensteinCentral
bash scripts/dump-all.sh
bash scripts/dump-all.sh --verify-only /srv/dumps/current
cat /srv/dumps/current/manifest.txt
```

Every target should read `ok`. If `firefly` says it cannot resolve `DB_HOST`
to exactly one container, it tells you the env var to set
(`FIREFLY_DB_CONTAINER`); put that in the unit's `Environment=` and restart.
Nothing should be guessed on your behalf.

Then in the Backrest UI run the `t1-optiplex` plan once and watch the hook
output: the dump runs first, then restic. Check healthchecks.io went green.

## 8. Restore drill (SCRUM-15 / SCRUM-30) — the ticket is not Done without this

Two proofs, because they answer different questions.

**Weekly, automated — does the dashboard database restore?** `restore.sh
--drill` restores the newest backup into a scratch database, compares row
counts against what `backup.sh` recorded at dump time, drops the scratch, and
writes the date to `data-safety.json`. That date is what the **Data safety**
card shows as "days since the last verified restore". Keep the weekly drill
cron from [OPERATIONS.md](OPERATIONS.md#backups); drop its *nightly*
`backup.sh` line once Backrest is running — `dump-all.sh` runs `backup.sh`
as part of every snapshot, and two nightly dumps of the same database is
just noise in the log.

**Quarterly, by hand — is the OFFSITE copy readable?** The drill proves the
local backup; it says nothing about whether the bytes on B2 come back. Same
day as the first snapshot, then quarterly: pick one file and prove it returns
byte-identical from B2:

```bash
# in the Backrest UI: Snapshots -> latest -> Restore, or from a shell:
export RESTIC_PASSWORD='<step 5>' AWS_ACCESS_KEY_ID='…' AWS_SECRET_ACCESS_KEY='…'
restic -r s3:https://s3.<region>.backblazeb2.com/<bucket>/frankenstein \
  restore latest --target /tmp/restore-drill --include /srv/dumps/current/vaultwarden/db.sqlite3
cmp /tmp/restore-drill/srv/dumps/current/vaultwarden/db.sqlite3 /srv/dumps/current/vaultwarden/db.sqlite3 \
  && echo "RESTORE DRILL PASSED"
rm -rf /tmp/restore-drill
```

Then a **monthly** calendar reminder to repeat it. The dashboard's infra card
(SCRUM-67) is where the date of the last verified restore belongs.

## Day to day

```bash
cat /srv/dumps/current/manifest.txt          # what the last dump did, per target
ls /srv/dumps/                                # failed-<stamp>/ dirs keep evidence of bad nights
journalctl -u backrest --since today          # Backrest's own log
```

A `failed-*` directory means a snapshot did **not** happen that night and
healthchecks.io was told. Read its `manifest.txt`: each target says exactly
why, and names the variable to fix where there is one.
