# Configuration Reference

Every environment variable the stack reads, which service consumes it, its
default, and what happens when it is missing.

Configuration lives in **two places**, and knowing which is which saves a lot
of confusion:

| Where | What lives there | How to change it |
|---|---|---|
| `.env` in the repo root | secrets and infrastructure URLs | edit the file on the box, then `docker compose up -d` |
| `core_settings.data` (Postgres, jsonb) | product configuration | `PUT /api/core/settings` |

`.env` is **untracked** — it is in `.gitignore`, so `git reset --hard` during a
deploy never touches it, and secrets never reach GitHub. `.env.example` is the
tracked template. **A variable added to `docker-compose.yml` must also be added
to `.env.example`,** or the next person to set the box up from scratch will
silently get the default.

Product configuration that is *not* in `.env`:

- **Stock holdings and watchlist** — `market.holdings` / `market.watchlist` in
  core settings, read by the `stocks` service.
- **Budget definitions** — core settings, read by the `budget` service.

---

## Missing-value semantics

Every variable in this system is optional, and the compose file supplies a
default for each with `${VAR:-default}`. Nothing crashes on a missing value.
Instead, three patterns apply:

1. **Empty credential = "not connected".** `firefly`, `plex`, `vault`,
   `powerbuy` and `gmail` all detect an empty token/password and either serve
   sample data or report themselves disconnected. They never fabricate a
   number and pass it off as real.
2. **Empty interval = "off".** `AUTO_SYNC_SECONDS=0` means manual sync only;
   an empty `NOTIFY_CHANNEL` means notifications are off.
3. **Empty URL = fall back to the in-network default.** Every inter-service
   URL defaults to `http://<service>:8000`, the docker-network name.

---

## Infrastructure

| Variable | Consumed by | Default | Notes |
|---|---|---|---|
| `POSTGRES_USER` | `db` + every persisting service | `frank` | |
| `POSTGRES_PASSWORD` | `db` + every persisting service | `frank` | **The default is `frank`.** Postgres publishes 5432 on the host, so on an untrusted network this must be set. |
| `POSTGRES_DB` | `db` + every persisting service | `frankensteincentral` | |
| `LOCAL_TZ` | `core`, `firefly`, `assistant` | `America/New_York` | The user's local day. The box runs UTC; this is what makes "today" mean the right thing. Getting it wrong shifts day boundaries and re-introduces the 2026-09-01 class of bug. |

`DATABASE_URL` is not set by hand — compose composes it from the three
`POSTGRES_*` values for each service that needs it.

## Inter-service URLs

Set in `docker-compose.yml`, not normally in `.env`. Each defaults to the
docker-network hostname.

| Variable | Set on | Points at |
|---|---|---|
| `POWERBUY_URL`, `FITNESS_URL`, `GMAIL_URL`, `SCHEDULE_URL`, `ASSISTANT_URL` | gateway | the matching service |
| `CORE_URL`, `STOCKS_URL`, `BUDGET_URL`, `DEALS_URL`, `NETWORTH_URL`, `VAULT_URL`, `FINANCE_URL`, `TASKS_URL` | assistant | the matching service |
| `PLEX_SVC_URL` | assistant, gateway | `http://plex:8000` |
| `FIREFLY_URL_SVC` | assistant, gateway | `http://firefly:8000` — the **sub-app**, not Firefly III itself |
| `FIREFLY_SVC_URL` | budget, networth | `http://firefly:8000` — same target, different name |
| `TASKS_URL`, `FITNESS_URL` | core | services core reads for the daily score |
| `GMAIL_URL` | schedule | where it fetches the Google token from |

> Three near-identical names point at the same firefly sub-app:
> `FIREFLY_URL_SVC` (assistant/gateway), `FIREFLY_SVC_URL` (budget/networth),
> and `FIREFLY_URL` (**different** — the real Firefly III instance). Read the
> suffix carefully before changing one.

## Google / Gmail

| Variable | Default | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | empty | From Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | empty | |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8083/auth/callback` | Must match the Google Cloud config exactly, including host and port. |
| `GOOGLE_REFRESH_TOKEN` | empty | Optional headless path — reuse an existing token instead of clicking through consent. |
| `GMAIL_QUERY` | empty | Override which inbox slice to triage. Empty = primary tab, last 7 days. |
| `GMAIL_TOKEN_FILE` | `/data/token.json` | On the `gmail_token` volume. |
| `GMAIL_STATE_FILE` | `/data/gmail_state.json` | Last-known-good inbox, so a restart doesn't blank the card. |
| `GMAIL_REFRESH_SECONDS` | `21600` (6h) | Background poll interval. Read endpoints serve the last poll, so page loads cost no API quota. |
| `GOOGLE_CALENDAR_ID` | `primary` | Which calendar `schedule` writes to. |

**The scope trap.** The OAuth scope set includes `calendar.events`, added after
the original `gmail.modify`-only version so `schedule` could write real events.
A refresh token minted before that change **403s on every Calendar call** while
Gmail keeps working perfectly. Symptom: the inbox card is fine and the calendar
silently stops updating. Fix: visit `/auth/login` once and re-consent. Changing
a token or scope is a credential operation — treat it as high-risk under
`.frankenstein/PROTOCOL.md`.

## Firefly III

| Variable | Default | Notes |
|---|---|---|
| `FIREFLY_URL` | empty | The **real Firefly III API**, e.g. `http://192.168.1.185:8093`. Server-side only. |
| `FIREFLY_TOKEN` | empty | Personal access token made in the Firefly UI. Empty = sample data. Never sent to the browser. |
| `FIREFLY_WEB_URL` | empty | Browser-facing URL for "Open in Firefly" deep links. Separate from `FIREFLY_URL` because the container and the browser may not reach Firefly at the same address. |
| `FIREFLY_IMPORTER_URL` | empty | Browser-facing URL for the data-importer deep link (8094 on this box). |

## Other integrations

| Variable | Service | Default | Notes |
|---|---|---|---|
| `POWERBUY_API_URL` | powerbuy | `https://powerbuy.onrender.com/api` | The Render backend, not the Vercel frontend. |
| `POWERBUY_EMAIL` / `POWERBUY_PASSWORD` | powerbuy | empty | Empty = mock data. |
| `PLEX_TOKEN` | plex | empty | Your plex.tv account token; the server is auto-discovered from it. Never sent to the browser. Empty = "not connected". |
| `PLEX_SERVER_NAME` | plex | empty | Disambiguates when several servers are shared with you. |
| `PLEX_URL` | plex | empty | Skip discovery and talk to a server directly. |
| `VAULT_MODE` | vault | `off` | `off` or `bitwarden`. |
| `BW_SERVE_URL` | vault | empty | A running `bw serve` endpoint. Read-only; no secret is ever returned or stored. |

## Assistant behavior

| Variable | Default | Notes |
|---|---|---|
| `AUTO_SYNC_SECONDS` | `0` | Seconds between automatic syncs. `0` = manual only. `900` = every 15 minutes. |
| `NOTIFY_CHANNEL` | empty | `telegram` \| `whatsapp` \| `sms` \| `webhook`. Empty = notifications off. |
| `NOTIFY_ON_SYNC` | `false` | Send a digest after every sync. |
| `NOTIFY_TO` | empty | Destination for sms/whatsapp. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | empty | For `telegram`. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | empty | For `whatsapp` and `sms`. |
| `TWILIO_WHATSAPP_FROM` / `TWILIO_SMS_FROM` | empty | Sending number. |
| `NOTIFY_WEBHOOK_URL` | empty | For `webhook`. |

iMessage is not supported and cannot be — there is no Apple API for it from a
self-hosted app.

---

## Deployment variables

Read by `scripts/autopull.sh`, `scripts/deploy.sh` and `scripts/promote.sh`,
**not by any container**. Set in the systemd unit, not in `.env`.

| Variable | Default | Notes |
|---|---|---|
| `FRANKENSTEIN_DIR` | `$HOME/FrankensteinCentral` | The repo clone the poller acts on. If this directory cannot be entered, the poller deploys **nothing** and says so — it never falls back to its own working directory. |
| `FRANKENSTEIN_BRANCH` | `production` | The one branch the poller watches. Changing this changes what "production" means on this box. |
| `FRANKENSTEIN_STATE_DIR` | `$HOME/.frankenstein` | Where `deployed.json` lives — deliberately outside the repo, because `git reset --hard` would erase anything tracked. |
| `DEPLOY_SKIP_TESTS` | `0` | `1` forces a deploy past the test gate. **Emergencies only** — it removes the only thing standing between a broken commit and the running stack. |

---

## Auditing configuration drift

Two commands worth running whenever something behaves unexpectedly. They print
variable **names** only, never values:

```bash
cd ~/FrankensteinCentral

# compose expects these, but .env does not set them → the default is in effect
comm -23 <(grep -oE '\$\{[A-Z_][A-Z0-9_]*' docker-compose.yml | sed 's/${//' | sort -u) \
         <(grep -E '^[A-Z_][A-Z0-9_]*=.+' .env | cut -d= -f1 | sort -u)

# .env sets these, but nothing in compose reads them → dead config
comm -23 <(grep -E '^[A-Z_][A-Z0-9_]*=' .env | cut -d= -f1 | sort -u) \
         <(grep -oE '\$\{[A-Z_][A-Z0-9_]*' docker-compose.yml | sed 's/${//' | sort -u)
```

Neither list being empty is normal — plenty of variables are intentionally
unset. The point is to notice when something you *expected* to be configured
is not. The current results, and what they mean, are recorded in
[DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md#configuration-drift).

## Rules for handling secrets

- Secrets live in `.env` on the box and are injected as container environment
  variables. They are **never** sent to the browser and never committed.
- `scripts/verify.sh` is the live diagnostic and is written to never print
  secrets, email bodies, or tokens. Keep it that way.
- `scripts/frankenstein-status.sh --check` prints no secrets either, which is
  why it is safe to use as an automation gate.
- **Rotating any credential is a high-risk action** under
  `.frankenstein/PROTOCOL.md` and requires explicit approval before it is done.
