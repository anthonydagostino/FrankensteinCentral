# Connecting the Firefly app to your Firefly III

The Firefly sub-app is a **read-only dashboard** — it shows net worth, this
month's income/spend/left-to-spend, your asset accounts, and recent
transactions. It never writes to Firefly and has no database. The access token
stays server-side and is never sent to the browser.

Until it's connected it shows **sample data**.

## Steps (when you're home)

1. In Firefly III, go to **Options → Profile → OAuth**, scroll to **Personal
   Access Tokens**, click **Create new token**, name it (e.g.
   "FrankensteinCentral"), and copy it — you only see it once.

2. In the FrankensteinCentral `.env`:
   ```
   FIREFLY_URL=http://<homelab-ip>:<port>      # your Firefly base URL
   FIREFLY_TOKEN=<the personal access token>
   # FIREFLY_WEB_URL=                          # optional: browser URL if different
   ```

3. Bring it up:
   ```
   docker compose up -d --build firefly assistant gateway
   ```

4. Open **Firefly** on the hub. You'll see your real numbers, Fitz reports your
   net worth on the floor, and there's an "Open in Firefly" button to jump into
   the full app.

## What it shows

- **Net worth**, and this month's **earned / spent / left-to-spend** (from
  Firefly's `summary/basic`)
- **Asset account** balances
- **Recent transactions** (color-coded: withdrawals red, deposits green,
  transfers grey)

## Notes

- Keep Firefly (and this app) on your LAN / behind your VPN.
- The app only reads; it never changes anything in Firefly.
- This overlaps your Finance/Budget/Net Worth apps on purpose — Firefly is the
  full ledger; those are lighter, hand-kept views. Use whichever you prefer, or
  point the others at Firefly later.
