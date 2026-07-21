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
| Assistant  | 8085 | Reads all sub-apps, builds briefing, routes info          | live (mock inputs) |
| PowerBuy   | 8081 | Your arbitrage tracker — purchases, profit, unpaid/expiring | live via login, mock fallback |
| Fitness    | 8082 | Gym visits, weekly plan, groceries & nutrition            | mock (in-memory) |
| Gmail      | 8083 | Emails needing a reply. Own Google OAuth                  | OAuth wired, mock fallback |
| Schedule   | 8084 | Your calendar. Idempotent event creation                  | in-memory |

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

That's it — it shows up on the dashboard automatically.

## Notes

State is in-memory for now (fitness visits, calendar, gmail tokens). A Postgres
container is already in compose (`db`) for when we persist these.
