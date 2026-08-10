# Connect the hub to your Firefly III

You already run Firefly III at **http://192.168.1.185:8093**. The hub connects to
it (read-only) and surfaces:

- the **Firefly** tile — net worth, this month's earned/spent/left-to-spend,
  your accounts, recent transactions, and a **30-day spending-by-category pie**
- the **Net Worth** tile — sourced live from Firefly (your accounts + net worth),
  instead of hand-keyed balances

Both go through the `firefly` service, which holds the access token. The token is
**never** sent to the browser. Until you set the token, both tiles show sample
data.

## 1. Point the hub at your Firefly

In `.env` on the box:
```
FIREFLY_URL=http://192.168.1.185:8093
FIREFLY_WEB_URL=http://192.168.1.185:8093
```
(`FIREFLY_URL` is what the containers read; `FIREFLY_WEB_URL` is what the
"Open in Firefly" button opens. Usually the same. Change the IP/port if yours
differs.)

## 2. Make a Personal Access Token

1. Open **http://192.168.1.185:8093** and log in.
2. **Options → Profile → OAuth** tab.
3. Under **Personal Access Tokens**, click **Create new token**, name it
   `FrankensteinCentral`, **Create**.
4. Copy the long token — Firefly shows it **once**. (Lost it? Just delete and
   make a new one.)

Put it in `.env`:
```
FIREFLY_TOKEN=<the long token>
```

## 3. Apply it

A plain `.env` edit doesn't trigger auto-deploy, so restart the pieces that use
the token:
```
docker compose up -d firefly networth assistant
```
(or `docker-compose ...` if you're on Compose v1)

Open the hub (**:8080**) → the **Firefly** tile shows your real numbers and the
spending pie fills in, and **Net Worth** shows your Firefly accounts. Fitz
reports your net worth on the floor each sync.

## Notes

- Everything is LAN-only — keep Firefly and the hub behind your network / VPN.
- The hub is **read-only**; it never changes anything in Firefly.
- Ports: your Firefly `8093`, the hub tile service `8094`, the hub `8080`.
- The 30-day pie uses Firefly's own `insight/expense/category` data, so it
  matches what Firefly shows.
