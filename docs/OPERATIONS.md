# Operations Runbook

Day-to-day operation of the running stack: what to check, how to deploy, how to
roll back, and how to diagnose the failures this system actually has.

Companion documents: [DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md) for what
is currently deployed, [SETUP-DEPLOY.md](SETUP-DEPLOY.md) for the one-time
install, `.frankenstein/PROTOCOL.md` for the rules that govern changes.

---

## First five commands

When something is wrong, run these in order before forming a theory:

```bash
cd ~/FrankensteinCentral

bash scripts/frankenstein-status.sh                      # 1. protocol + desired vs running
curl -s localhost:8080/api/health | python3 -m json.tool # 2. which sub-apps are down
docker compose ps                                        # 3. which containers are up
journalctl -u frankenstein-deploy.service --since '-1h'  # 4. did a deploy fail?
bash scripts/verify.sh                                   # 5. deep live diagnostic
```

`verify.sh` is the thorough one. It never prints secrets, email bodies, or
tokens — keep it that way when editing it.

---

## Deploying

### The normal path

You do not deploy. You **promote**, and the box deploys itself within ~60s.

```bash
bash scripts/promote.sh --dry-run    # show exactly what would move
bash scripts/promote.sh              # promote STATE.json's implementation_commit
bash scripts/promote.sh <sha>        # promote a specific commit
```

`promote.sh` refuses unless **all three** hold:

1. `STATE.json` status is `accepted` — the Product Owner accepted the work.
2. `PRODUCT_DIRECTIVE.md` says `Deployment Authorization: deploy-approved`.
3. The move is a **fast-forward** — production history is never rewritten.

Acceptance and deployment authorization are **separate gates**. `accepted` means
the work is good; it does not mean ship it now. Authorization does not excuse
failing acceptance. Both, or no promotion.

`--force` / `--bootstrap` skip gates 1 and 2 but **never** gate 3. They exist
for the one-time bootstrap migration and explicitly approved overrides.

### What happens next

Within 60 seconds the poller notices `origin/production` differs from the
recorded running commit and runs `deploy.sh`:

```
git fetch → checkout production → reset --hard origin/production
bash scripts/test.sh            ← the gate
docker compose up -d --build --remove-orphans
docker image prune -f
record success + running_commit
```

`reset --hard` only touches tracked files. `.env` is untracked and Docker
volumes are outside the repo, so **secrets and data survive every deploy**.

Watch it happen:

```bash
journalctl -u frankenstein-deploy.service -f
```

### Deploying by hand

On the box, bypassing the poller and changing no branches:

```bash
bash scripts/deploy.sh production
```

### Pushing is not deploying

Task branches (`claude/FC-###-<slug>`) can be pushed freely at every
authorization level. A task-branch push deploys **nothing** — the poller
watches `production` and only `production`. This is what makes GitHub review
possible without touching the running stack.

---

## Rolling back

**Production history is append-only.** Rolling back means adding a commit whose
tree is the known-good one, not rewinding the branch:

```
production:  A --- B --- C(bad) --- D(tree of B, "rollback")
```

The box then deploys D like any other change, and the bad deploy stays in the
audit trail.

For a single bad commit, a plain revert reads more clearly:

```bash
git revert --no-edit <bad-sha>
git push origin HEAD:refs/heads/production
```

For several commits at once, where "restore this exact known-good tree" is the
clearer intent:

```bash
bash scripts/rollback.sh --dry-run <known-good-sha>
bash scripts/rollback.sh <known-good-sha>
```

### Never do this casually

```bash
git push origin <older-sha>:refs/heads/production --force-with-lease
```

Rewriting production is **emergency recovery only**. It erases the record that
the bad deploy happened and can desynchronize other clones. It is a high-risk
action under the protocol and requires explicit approval. It is never the
operational rollback.

---

## Diagnosing

### A deploy is not happening

Work down this list; each item is a real failure mode the poller handles
explicitly, and each says so in the journal:

| Symptom in the journal | Cause |
|---|---|
| `configured repo directory cannot be entered` | `FRANKENSTEIN_DIR` is wrong. Nothing deploys — the poller refuses to act from an arbitrary directory. |
| `could not fetch origin/<branch> — current production state is UNKNOWN` | Network or auth failure. Nothing deploys; it will **not** act on a stale remote-tracking ref. |
| `production branch could not be resolved` | The branch is missing or unreadable. Nothing deploys. |
| `TESTS FAILED — deploy aborted` | The gate caught a bad commit. Containers untouched; the previous build is still serving. |
| nothing at all, every minute | Desired == running. There is nothing to do. This is the healthy state. |

Confirm what the poller thinks:

```bash
cat ~/.frankenstein/deployed.json
git -C ~/FrankensteinCentral rev-parse origin/production
```

If `running_commit` equals `origin/production`, the deploy already happened.

### A deploy keeps retrying

A commit that fails the test gate is retried on **every** poll, once a minute.
That is intentional and cheap: containers are never touched on failure, so the
box keeps serving the last good build while the retries continue. It stops when
you fix the commit or roll production forward.

```bash
tail -50 /tmp/fc-test.log        # why the gate failed
```

### The whole dashboard is blank or buttons do nothing

Almost always stale JavaScript. The gateway sets `Cache-Control: no-cache` on
non-`/api/` responses precisely to prevent this, but a hard reload
(Ctrl-Shift-R) rules it out in one step. If the page renders but a panel is
empty, check `/api/health` — one down service degrades one card.

### One sub-app card is empty or says "not connected"

Health `up` means the **process** is healthy, not that it has data. A service
with an empty credential reports itself disconnected and stays `up`. Check the
credential before suspecting the service:

```bash
docker compose logs --tail=50 <service>
curl -s localhost:<port>/summary | python3 -m json.tool
```

See [CONFIGURATION.md](CONFIGURATION.md) for which variable each sub-app needs.

### The Gmail card is stale

```bash
curl -s localhost:8083/sync-status | python3 -m json.tool   # when did the poll last run?
curl -s -X POST localhost:8083/refresh                      # force one now
```

The background poll is every 6 hours; endpoints serve the last completed sync,
so the card can legitimately be up to 6 hours old.

### Gmail works but the calendar silently stopped updating

**This is the OAuth scope trap, and it is the single most common cause.** The
scope set includes `calendar.events`, added after the original
`gmail.modify`-only version. A refresh token minted before that change **403s
on every Calendar call** while Gmail keeps working perfectly.

Fix: open `http://<box>:8083/auth/login` once and re-consent.

Also confirm the dependency chain — `schedule` has no Google credentials of its
own and borrows the token from `gmail`:

```bash
docker compose logs --tail=50 schedule
curl -s localhost:8084/events | python3 -m json.tool
```

If `gmail` is down or disconnected, `schedule` cannot write to Calendar.

### Money numbers look wrong on the 1st of a month

Read [TESTING.md](TESTING.md) first — this exact failure has happened. Firefly
returns **422** for a zero-length date range, which is what `first-of-month →
today` becomes on the 1st, and a container running UTC disagrees with
`LOCAL_TZ` about which month it is around midnight.

```bash
curl -s localhost:8097/audit | python3 -m json.tool
```

Honesty rules that apply here: zero and unknown are different states,
suppressed values are `null` and never `0`, and a partial window is never
presented as complete. See [BUDGETS.md](BUDGETS.md).

### Database inspection

```bash
docker exec -it frankensteincentral-db-1 psql -U frank -d frankensteincentral

\dt                                    # tables
SELECT COUNT(*) FROM events;
SELECT * FROM events ORDER BY starts_at DESC LIMIT 10;
SELECT * FROM events WHERE status='declined';   # the audit trail
```

Note that several tables store timestamps as `text`, and that day boundaries in
the product mean `LOCAL_TZ` days, not UTC. A query written in UTC will disagree
with the dashboard around midnight. See [DATA-MODEL.md](DATA-MODEL.md).

---

## Routine maintenance

### Reclaiming disk

`deploy.sh` runs `docker image prune -f`, which removes **dangling** images
only. Tagged orphans — images from a renamed compose project or a removed
service — survive it and must be removed deliberately:

```bash
docker images | grep frankensteincentral       # look before deleting
docker image rm <image>:<tag>                  # one at a time, verified
```

At baseline this box carries ~2.6 GB of such orphans. See
[DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md#observations).

### Backups

`~/docker/backup.sh` runs nightly at 03:00 via crontab. What actually protects
this stack:

- **`.env`** — untracked and in exactly one place. Losing it means re-entering
  every credential by hand.
- **`db_data`** — all persisted product data.
- **`gmail_token`** — losing it means re-consenting to Google.

The repository is not a backup of any of these. It is a backup of the code
only.

### Restarting

```bash
docker compose restart <service>     # one service
docker compose up -d                 # reconcile everything to the compose file
docker compose up -d --build <svc>   # rebuild one service from source
```

**Never** `docker compose down -v`. The `-v` destroys `db_data` and
`gmail_token` — every event, task, bill, and the Gmail consent. That is a
destructive data operation and a high-risk action under the protocol.

---

## Testing

```bash
bash scripts/test.sh            # everything, ~15s, no running services needed
bash scripts/test.sh -k budget  # extra args pass through to pytest
```

Three suites: Python (`services/` + `tests/`), `node --check` on every
`gateway/static/*.js`, and `bash -n` on every `scripts/*.sh`. It runs in CI on
every push and on the box before containers are touched.

`DEPLOY_SKIP_TESTS=1` forces a deploy past the gate. It removes the only thing
standing between a broken commit and the running stack — emergencies only.

---

## Before you change anything

Read `.frankenstein/PROTOCOL.md`. Two rules govern everything above:

**Check whose turn it is.** `bash scripts/frankenstein-status.sh`. Product work
requires `turn: claude` with status `ready_for_implementation` or
`changes_requested`. Reading the repo, answering questions, and explaining
existing behavior are always allowed — the restriction is on changing the
product.

**Stop and ask on high-risk actions.** Deleting user data, destructive
migrations, changing auth boundaries, exposing services publicly, rotating
credentials, modifying financial accounts, sending email, rewriting the
production branch, or anything irreversible. `turn: claude` is not unlimited
authority. When in doubt, treat it as high-risk and ask.
