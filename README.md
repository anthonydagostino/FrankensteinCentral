# FrankensteinCentral

One hub app for all your apps. The **gateway** is the front door — it shows
every sub-app and hosts your **Assistant**, which reads across all of them,
surfaces what needs you, and routes information where it belongs (e.g. an
interview email → your calendar).

Every sub-app is its own independent service (its own container, its own API),
so they can be developed, deployed, and swapped out separately.

## Architecture

```
                         ┌────────────────────────────┐
   browser  ───────────► │  gateway (hub + dashboard)  │  :8080
                         │  - service registry          │
                         │  - health aggregation        │
                         │  - reverse proxy /api/<app>/ │
                         └──────────────┬───────────────┘
                                        │ (internal http)
      ┌───────────────┬─────────────────┼──────────────┬───────────────┐
      ▼               ▼                 ▼              ▼               ▼
  assistant       powerbuy          fitness         gmail          schedule
   :8085           :8081            :8082           :8083           :8084
  (the brain)   (your API)      (gym+food)      (own OAuth)      (calendar)
```

The **assistant** is the orchestrator: on each sync it reads the gmail sub-app,
detects scheduled interviews, and creates events in the schedule sub-app — the
cross-app flow you described.

## Run it

```bash
cp .env.example .env      # fill in secrets when you have them
docker compose up --build
```

Then open **http://localhost:8080**. Hit **Sync now** to run the assistant.

Each service is also directly reachable (8081–8085) and self-documents its
endpoints at `/` and `/health`.

## Sub-apps

| App        | Port | What it does                                              | Status |
|------------|------|-----------------------------------------------------------|--------|
| Assistant  | 8085 | Reads all sub-apps, briefing, agent lounge (Assistant HQ) | live (mock inputs) |
| PowerBuy   | 8081 | Your arbitrage tracker — purchases, profit, unpaid/expiring | live via login, mock fallback |
| Fitness    | 8082 | Gym visits, weekly plan, groceries & nutrition            | visits persisted (Postgres) |
| Gmail      | 8083 | Whole-inbox triage. Own Google OAuth                      | OAuth wired, token persisted |
| Schedule   | 8084 | Your calendar. Idempotent event creation                  | persisted (Postgres) |
| Finance    | 8086 | Bills & subscriptions — monthly spend, what's due soon    | persisted (Postgres) |

## Wiring in the real integrations

- **PowerBuy** — set `POWERBUY_EMAIL` / `POWERBUY_PASSWORD` in `.env` (same
  login you use at powerbuy.vercel.app). The service logs into
  `https://powerbuy.onrender.com/api`, pulls your purchases, and exposes
  `/purchases` and a rolled-up `/summary` (expected profit, unpaid, not
  delivered, expiring soon). Empty creds = mock data.
- **Gmail** — create OAuth credentials in Google Cloud, set `GOOGLE_CLIENT_ID`
  / `GOOGLE_CLIENT_SECRET`. Visit `http://localhost:8083/auth/login` to connect.
  Once connected, `/needs-reply` reads your real unread inbox.

## Adding a new sub-app

1. Create `services/<name>/` (copy any existing service as a template).
2. Add it to `docker-compose.yml`.
3. Register it in `gateway/app/registry.py`.

That's it — it shows up on the dashboard automatically. Clicking a card opens a
live detail view; add a renderer in `gateway/static/app.js` (`RENDERERS`) to
give the new app a rich panel (otherwise it falls back to a generic one).

## Persistence

Nothing resets on restart:

- **Calendar events** and **gym visits** live in the `db` Postgres service
  (tables auto-created on startup, backed by the `db_data` volume).
- **Gmail connection** (refresh token) is saved to the `gmail_token` volume.

To wipe everything and start clean: `docker compose down -v`.

## Auto-pilot

Set `AUTO_SYNC_SECONDS` in `.env` (e.g. `900` for every 15 min) and the
assistant syncs itself on that interval — the floor stays busy and your
briefing/deadlines stay current without opening the browser. `0` (default)
means manual only (the "Sync now" / "Dispatch team" buttons).
