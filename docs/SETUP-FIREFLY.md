# Firefly III — running inside FrankensteinCentral

A **real Firefly III** now ships as part of the stack. You don't need to stand
one up separately. Bringing the stack up gives you:

- **`fireflyiii`** — the full Firefly III web app, at `http://<box-ip>:8095`
- **`firefly-db`** — its own Postgres (kept separate from the app's database)
- **`firefly-importer`** — the official data importer, at `http://<box-ip>:8096`
- **`firefly`** — the read-only tile on the hub that shows your net worth,
  monthly spend/income, accounts, and recent transactions. It reads the in-stack
  Firefly and never sends your token to the browser.

Until you make an access token, the hub tile shows **sample data**.

## Get it running (on the box)

1. In `.env`, set the browser URLs to your box's IP (so the dashboard links
   work) — replace `192.168.1.185` with your box if different:
   ```
   FIREFLY_WEB_URL=http://192.168.1.185:8095
   FIREFLY_IMPORTER_URL=http://192.168.1.185:8096
   # optional but recommended — change these from the shipped defaults:
   FIREFLY_APP_KEY=<any 32-character string>
   FIREFLY_DB_PASSWORD=<a password>
   ```

2. Bring the stack up:
   ```
   docker compose up -d --build firefly-db fireflyiii firefly-importer firefly gateway assistant
   ```
   First boot runs Firefly's database migrations automatically — give it a
   minute, then open `http://<box-ip>:8095`.

3. **Register** your account (the first account you create is the admin).

## Import your data

Open `http://<box-ip>:8096` (the importer), or click **Import data ↗** on the
Firefly tile. It walks you through:

- **CSV** — export from your bank and map the columns (most common).
- **camt.053** — European bank statements.
- **Bank sync** (Nordigen/GoCardless, Spectre) if you set those keys.

You can also import a previous Firefly export. The importer needs the same
access token as the step below — set `FIREFLY_TOKEN` first, then restart it:
`docker compose up -d firefly-importer`.

## Show your real numbers on the hub

1. In Firefly (`:8095`): **Options → Profile → OAuth → Personal Access Tokens →
   Create new token**, name it "FrankensteinCentral", copy it (shown once).

2. Put it in `.env`:
   ```
   FIREFLY_TOKEN=<the token>
   ```

3. Restart the pieces that use it:
   ```
   docker compose up -d firefly firefly-importer assistant
   ```

Open **Firefly** on the hub — you'll see your real net worth, this month's
earned/spent/left-to-spend, your accounts, and recent transactions, with
**Open in Firefly ↗** and **Import data ↗** buttons. Fitz reports your net
worth on the floor each sync.

## Notes

- Everything is LAN-only — keep it behind your network / VPN.
- The hub tile is **read-only**; it never changes anything in Firefly.
- Ports: Firefly III `8095`, importer `8096`, hub tile service `8094`.
- To use an **external** Firefly III instead of the in-stack one, set
  `FIREFLY_URL` to its base URL (leave blank to use the in-stack one).
