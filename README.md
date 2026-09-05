# FrankensteinCentral

One hub app for all your apps. The **gateway** is the front door — it shows
every sub-app and hosts your **Assistant**, which reads across all of them,
surfaces what needs you, and routes information where it belongs (e.g. an
interview email → your calendar).

Every sub-app is its own independent service (its own container, its own API),
so they can be developed, deployed, and swapped out separately.

## Documentation

Full reference lives in [`docs/`](docs/README.md):

- [Architecture](docs/ARCHITECTURE.md) — how the pieces fit and where the trust boundaries are
- [Deployment baseline](docs/DEPLOYMENT-BASELINE.md) — what is actually running on the box right now
- [Operations runbook](docs/OPERATIONS.md) — deploy, roll back, diagnose
- [API reference](docs/API-REFERENCE.md) · [Data model](docs/DATA-MODEL.md) · [Configuration](docs/CONFIGURATION.md)

**Pushing is not deploying.** Only the `production` branch is deployed, and only
`scripts/promote.sh` moves it. See [`.frankenstein/PROTOCOL.md`](.frankenstein/PROTOCOL.md).

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

Each service is also directly reachable (8081–8099) and self-documents its
endpoints at `/` and `/health`. Full port map in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Sub-apps

| App        | Port | What it does                                              |
|------------|------|-----------------------------------------------------------|
| Assistant  | 8085 | The orchestrator. Reads every sub-app, briefing, agent lounge |
| Core       | 8098 | Personal state & daily score — study, water, nutrition, Big 3, captures |
| Gmail      | 8083 | Whole-inbox triage + sent-mail availability detection. Own Google OAuth |
| Schedule   | 8084 | Your calendar. Idempotent, color-coded, pushes to real Google Calendar |
| Firefly    | 8097 | Read-only view of your Firefly III — net worth, spend, accounts |
| Budget     | 8088 | Time-aware budgets over Firefly. Definitions live in core settings |
| Net Worth  | 8090 | Balances from Firefly, with manual accounts as fallback     |
| Finance    | 8086 | Bills & subscriptions — monthly spend, what's due soon      |
| Tasks      | 8087 | Your to-do list — quick capture, check things off           |
| Fitness    | 8082 | Gym visits, weekly plan, groceries & nutrition              |
| Stocks     | 8099 | Portfolio & watchlist. Keyless quotes via Stooq             |
| Deals      | 8089 | Real discounts spotted in your inbox                        |
| PowerBuy   | 8081 | Your arbitrage tracker — purchases, profit, unpaid/expiring |
| Vault      | 8091 | Password health from Vaultwarden. Metadata only, no secrets |
| Plex       | 8092 | A Plex server shared with you — continue watching, libraries |

Every sub-app persists to the shared Postgres or is stateless over an upstream;
which is which, and what each one holds, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/DATA-MODEL.md](docs/DATA-MODEL.md). Endpoints:
[docs/API-REFERENCE.md](docs/API-REFERENCE.md).

## Wiring in the real integrations

- **PowerBuy** — set `POWERBUY_EMAIL` / `POWERBUY_PASSWORD` in `.env` (same
  login you use at powerbuy.vercel.app). The service logs into
  `https://powerbuy.onrender.com/api`, pulls your purchases, and exposes
  `/purchases` and a rolled-up `/summary` (expected profit, unpaid, not
  delivered, expiring soon). Empty creds = mock data.
- **Gmail** — create OAuth credentials in Google Cloud, set `GOOGLE_CLIENT_ID`
  / `GOOGLE_CLIENT_SECRET`. Visit `http://localhost:8083/auth/login` to connect.
  Once connected, `/needs-reply` reads your real unread inbox, and
  `/thread-availability` scans your **sent** mail for "I'm available X at Y"
  proposals and tracks whether the other side confirmed, countered, or
  declined. This scope also now includes `calendar.events` (added so Bones
  can write to your real Google Calendar) — if you connected before this
  was added, **revisit `/auth/login` once** to re-consent; a token minted
  with only the old `gmail.modify` scope will 403 on Calendar calls.

## Gmail → Bones → Calendar pipeline

1. **Posty** (gmail service) triages the inbox and separately scans sent
   mail for your own availability proposals (`/thread-availability`).
2. **Bones** (assistant) diffs each thread's state against what it saw last
   sync (`thread_state` table) — unchanged threads are a total no-op, so
   nothing re-announces or re-books itself.
3. **Cal** (schedule service) gets a pending event per proposed slot, then:
   - if they **confirm** one → that slot flips to confirmed and every other
     proposed slot on the thread is auto-declined and removed (no leftover
     tentative holds for a meeting that's already locked in).
   - if they **counter** with a different time → the pending slot(s) update
     to the new offer, still marked pending, waiting on you.
   - if they **decline** → all pending slots on that thread clear out.
4. Every create/update is pushed to your **real Google Calendar**
   (`schedule` borrows Gmail's token via its internal `/internal/token`),
   color-coded so pending vs. confirmed is visually obvious both on the hub
   and in Google Calendar itself: 🟡 proposed by you, 🟠 they countered,
   🟢 confirmed. Declined slots are removed from Calendar (the Postgres row
   stays, marked `declined`, as your own audit trail).

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

## Bones texts you

The manager can text you a digest — on demand (**📱 Text me** on the hub) or
automatically after each sync. Supports Telegram, WhatsApp (Twilio), SMS
(Twilio), or a generic webhook. Pick a channel and add credentials per
[docs/SETUP-NOTIFICATIONS.md](docs/SETUP-NOTIFICATIONS.md). (iMessage isn't
possible from a self-hosted app — no Apple API.)

## Auto-pilot

Set `AUTO_SYNC_SECONDS` in `.env` (e.g. `900` for every 15 min) and the
assistant syncs itself on that interval — the floor stays busy and your
briefing/deadlines stay current without opening the browser. `0` (default)
means manual only (the "Sync now" / "Dispatch team" buttons).
