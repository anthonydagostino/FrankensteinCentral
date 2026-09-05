# Architecture

How FrankensteinCentral is put together, what each moving part is responsible
for, and how data actually flows between them. This describes the system as it
exists on the `production` branch — not a plan.

For what is *deployed right now* on the box, see
[DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md).

---

## 1. Shape of the system

One gateway, fifteen independent FastAPI services, one Postgres. Everything
runs as containers under a single `docker compose` project on a home OptiPlex.

```
                    browser (LAN)
                          │
                          ▼  :8080
        ┌─────────────────────────────────────────┐
        │  gateway  (FastAPI + static SPA)        │
        │   • GET /api/apps      service catalog  │
        │   • GET /api/health    fan-out probe    │
        │   • /api/<key>/<path>  reverse proxy    │
        │   • /  → gateway/static (SPA)           │
        └───────────────┬─────────────────────────┘
                        │ internal docker network, http://<service>:8000
   ┌────────────────────┼─────────────────────────────────────────┐
   │                    │                                         │
   ▼                    ▼                                         ▼
assistant ──reads──▶ every other service                      db (postgres:16)
(the orchestrator)                                            volume: db_data
   │
   └─ writes ──▶ schedule ──▶ real Google Calendar (token borrowed from gmail)
```

Two rules hold the design together:

1. **Every sub-app is independent.** Its own container, its own Dockerfile, its
   own `requirements.txt`, its own HTTP API on port 8000 inside the network. A
   service can be rebuilt, broken, or removed without touching the others.
2. **Only the gateway is browser-facing.** Sub-apps never receive a request
   from the browser directly in normal use; the browser talks to
   `/api/<key>/...` and the gateway proxies. The host ports 8081–8099 exist for
   debugging, not for the UI.

### Why a gateway at all

The gateway is deliberately thin — about 100 lines. It does three jobs:

- **Catalog** (`/api/apps`) — the dashboard renders itself from
  `gateway/app/registry.py`, so adding a sub-app is a registry entry plus a
  compose service, not a frontend rewrite.
- **Health fan-out** (`/api/health`) — probes every registered service's
  `/health` concurrently with a 3s timeout and reports `up`/`down` per key. One
  dead service degrades one card instead of the page.
- **Reverse proxy** (`/api/{app_key}/{path:path}`) — forwards method, query
  params, body and headers (minus `host`/`content-length`) with a 15s timeout.
  An unreachable upstream returns `502` with the sub-app's name, not a stack
  trace.

It also sets `Cache-Control: no-cache` on every non-`/api/` response. That
middleware exists because browsers were heuristically caching `app.js` across
deploys, leaving users running week-old JavaScript against a new page — buttons
silently did nothing. `no-cache` still permits `304`s, so it stays fast.

---

## 2. The service catalog

Fifteen sub-apps. "Source" is where the numbers actually come from — the single
most important column when judging whether a card can be trusted.

| Key | Name | Host port | Source of truth | State |
|---|---|---|---|---|
| `core` | Core | 8098 | user input + fitness + tasks | Postgres |
| `stocks` | Stocks | 8099 | Stooq (keyless quotes) | stateless, config in core |
| `assistant` | Assistant | 8085 | every other sub-app | Postgres |
| `powerbuy` | PowerBuy | 8081 | external PowerBuy API (Render) | stateless |
| `fitness` | Fitness | 8082 | user input | Postgres |
| `gmail` | Gmail Checker | 8083 | Google Gmail API (own OAuth) | volume `gmail_token` |
| `schedule` | Schedule | 8084 | user + assistant + Google Calendar | Postgres |
| `finance` | Finance | 8086 | user input | Postgres |
| `tasks` | Tasks | 8087 | user input | Postgres |
| `budget` | Budget | 8088 | firefly service + core settings | stateless |
| `deals` | Deals | 8089 | parsed from gmail | Postgres |
| `networth` | Net Worth | 8090 | firefly service, manual fallback | Postgres |
| `vault` | Vault | 8091 | Vaultwarden via `bw serve` | stateless, read-only |
| `plex` | Plex | 8092 | plex.tv / Plex server | stateless, read-only |
| `firefly` | Firefly | 8097 | self-hosted Firefly III | stateless, read-only |

Notes on the port map:

- Every service listens on **8000 inside the container**. The host port is
  purely a published mapping.
- **`firefly` is on 8097, not 8094**, because the Firefly III *data importer*
  already occupies 8094 on this box. The hub reaches the service internally, so
  the host port only matters for direct debugging.
- 8093/8094 (Firefly III core + importer), 8096 (Jellyfin), 8282 (Wallos),
  3001 (Uptime Kuma), 8222 (Vaultwarden), 80/443/81 (nginx-proxy-manager) are
  **other containers on the same box**, not part of this compose project.

### Layering

Services fall into three tiers, and the tier tells you what a failure means:

**Tier 1 — leaf services.** `powerbuy`, `fitness`, `finance`, `tasks`, `deals`,
`vault`, `plex`, `firefly`, `gmail`. They own one data source and depend on
nothing else in the stack (except Postgres where they persist). A failure here
is contained to one card.

**Tier 2 — derived services.** `budget` (reads `firefly` + `core`), `networth`
(reads `firefly`, falls back to its own Postgres accounts), `stocks` (reads
`core` for holdings/watchlist), `core` (reads `fitness` + `tasks`), `schedule`
(reads `gmail` for a Google token). A failure here usually means a *dependency*
failed; check the tier-1 service first.

**Tier 3 — the orchestrator.** `assistant` reads all fourteen others and is the
only service that writes across app boundaries. It is the last thing to
suspect and the first thing to check when *everything* looks stale.

---

## 3. The Gmail → Assistant → Calendar pipeline

This is the one genuinely cross-app flow, and the reason the assistant exists.
It runs on every `POST /api/assistant/sync`.

```
 1. gmail (Posty)
      /needs-reply            triages the inbox
      /thread-availability    scans SENT mail for "I'm available X at Y"
              │
              ▼
 2. assistant (Bones)
      diffs each thread against thread_state (Postgres)
      unchanged thread ⇒ total no-op, nothing re-announces or re-books
              │
              ▼
 3. schedule (Cal)
      one pending event per proposed slot, then:
        they CONFIRM  → that slot flips confirmed; every other proposed slot
                        on the thread is auto-declined and removed
        they COUNTER  → pending slots update to the new offer, still pending
        they DECLINE  → all pending slots on that thread clear out
              │
              ▼
 4. Google Calendar (real)
      schedule borrows gmail's token via gmail's /internal/token
      🟡 proposed by you   🟠 they countered   🟢 confirmed
      declined slots are DELETED from Calendar; the Postgres row stays,
      marked `declined`, as the audit trail
```

Three design decisions worth understanding:

**The diff against `thread_state` is what makes sync idempotent.** Without it,
every sync would re-create the same calendar events and re-send the same
notifications. The table stores the last-seen state per thread; an unchanged
thread costs zero writes and zero API calls.

**`schedule` does not hold its own Google credentials.** It calls the `gmail`
service's internal-only `/internal/token` endpoint. One OAuth consent, one
refresh token, one place it can leak from. The cost is a hard dependency:
`schedule` cannot write to Calendar while `gmail` is down or disconnected.

**The Gmail OAuth scope includes `calendar.events`.** It was added so the
schedule service could write real calendar entries. A token minted before that
addition carries only `gmail.modify` and will **403 on every Calendar call** —
the fix is to revisit `/auth/login` once and re-consent. This is the single
most common "why did my calendar stop updating" cause.

---

## 4. Background work

Three things run without anyone opening a browser:

| What | Where | Interval | Controlled by |
|---|---|---|---|
| Gmail inbox poll | `gmail` service | 6h (21600s) | `GMAIL_REFRESH_SECONDS` |
| Assistant auto-sync | `assistant` service | off by default | `AUTO_SYNC_SECONDS` (`0` = manual) |
| Deploy poller | systemd on the host | 60s | `frankenstein-deploy.timer` |

The Gmail poll is why the dashboard is fast: endpoints serve the **last
completed sync** from `/data/gmail_state.json`, so a page load costs no Gmail
API calls. The state file lives on the `gmail_token` volume, so a restart does
not blank the card until the next poll.

---

## 5. Persistence

Nothing resets on restart. Two volumes carry everything:

- **`db_data`** — the Postgres 16 data directory. Every service that persists
  creates its own tables at startup with `CREATE TABLE IF NOT EXISTS`; there is
  no migration tool and no shared ORM. See [DATA-MODEL.md](DATA-MODEL.md).
- **`gmail_token`** — mounted at `/data` in the gmail service. Holds
  `token.json` (the Google refresh token, so the one-time "Allow" survives
  restarts) and `gmail_state.json` (the last-known-good inbox).

`docker compose down -v` destroys both. `git reset --hard` — which every deploy
runs — destroys neither, because both are Docker volumes and `.env` is
untracked.

### Schema ownership

There is one Postgres database shared by eight services, but **no table is
shared between two services**. Each service owns its tables outright and
reaches other services' data over HTTP, never over SQL. That keeps the "each
sub-app is independent" property honest: the database is shared infrastructure,
not a shared model.

---

## 6. Secrets and trust boundaries

The security posture is *home LAN, secrets stay server-side*:

- Every credential lives in `.env` on the box, is injected as a container
  environment variable, and is **never sent to the browser**. The firefly
  token, the Gmail OAuth secret, the Plex token, and the PowerBuy password all
  stay inside their service.
- `vault` returns **metadata only** — counts of weak, reused, old and no-2FA
  entries. It never returns a password, and it stores nothing.
- `plex` and `firefly` are read-only clients.
- `scripts/verify.sh` is the live diagnostic and is written to never print
  secrets, email bodies, or tokens. Keep it that way when editing it.

**Known and accepted gaps** (documented, not fixed):

- The dashboard has **no authentication**. Anyone on the LAN who can reach
  :8080 sees everything. This is a deliberate home-network tradeoff.
- The gateway will proxy **any** `/api/<key>/<path>` to a registered service,
  including endpoints intended as internal (`gmail`'s `/internal/token` is
  reachable as `/api/gmail/internal/token`). Acceptable only because the LAN is
  the trust boundary; it would not be acceptable if :8080 were ever exposed.
- Postgres publishes **5432 on the host**, with credentials that default to
  `frank`/`frank` if `.env` does not override them.

Anything that would change these boundaries — exposing a service publicly,
adding auth, rotating credentials — is a **high-risk action** under
`.frankenstein/PROTOCOL.md` and requires explicit approval.

---

## 7. Frontend

A static SPA served by the gateway from `gateway/static/`:

| File | Role |
|---|---|
| `index.html` + `home.js` + `home.css` | the homepage / dashboard |
| `app.js` + `styles.css` | sub-app detail views and the `RENDERERS` map |
| `lounge.html` | the canvas "agent lounge" view |
| `jobs.html` | job/activity view |

There is no build step, no bundler, and no framework — the files are served as
written. `scripts/test.sh` runs `node --check` on each `.js` file, so a syntax
error fails the deploy gate rather than shipping a blank page.

Adding a rich panel for a new sub-app means adding a renderer to `RENDERERS` in
`app.js`; without one, the app still appears and falls back to a generic panel.

---

## 8. Adding a sub-app

1. `services/<name>/` — copy any existing service as a template (Dockerfile,
   `requirements.txt`, `app/main.py` with `/` and `/health`).
2. Add the service to `docker-compose.yml`, publishing the next free host port
   and mapping to container `8000`.
3. Register it in `gateway/app/registry.py` with a key, name, description,
   icon, and `url` read from an environment variable.
4. If the assistant should read it, add its URL to the assistant's environment
   and its `depends_on` list.
5. Optionally add a renderer in `gateway/static/app.js`.

The card then appears on the dashboard automatically. Two conventions are
load-bearing: **every service must serve `/health`** (the gateway's fan-out
probe depends on it) and **every service must serve `/`** with a JSON list of
its endpoints (that is the self-documentation the API reference is built from).
