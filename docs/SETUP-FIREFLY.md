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

## 4. Tell it about your paycheck

The Money card's **Left to spend** needs to know which deposit is your
paycheck and what comes out of it before the rest is yours to spend.
⚙ **Settings → Paycheck & savings**:

- **Paycheck deposit contains** — a word or two from how your bank describes
  the deposit, or the employer account name (`payroll`, `direct dep`). Open a
  paycheck in Firefly and copy a distinctive part of it.
- **Minimum deposit** — keeps a Venmo repayment from being read as payday.
- **Pay every (days)** — only a fallback; once two paychecks are in the
  ledger the real gap between them is used instead.
- **Savings deductions** — one row per thing that comes out of each paycheck
  (Fidelity, Marcus). If Firefly has the transfer, the real amount is used;
  if it hasn't been imported yet the amount you enter is used and shown as
  *expected*. Tick **pre-deposit** only when your employer takes it before
  the money lands — then it is displayed but not subtracted, because the
  deposit is already net of it.

Nothing here is written to Firefly; it is stored in the hub's own settings.
If the card says *no paycheck found*, the match terms are the thing to check.

## 5. Automate the import (SCRUM-142)

Everything above reads the ledger. This step is what keeps the ledger fed —
without it, "importing daily" is a hope, and the dashboard's money figures
are only as fresh as the last time someone clicked *Import data*.

`scripts/firefly-import.sh` triggers the official Data Importer and then
judges the run **by the ledger**, not by the importer's HTTP code: it reads
the transaction count and the newest `created_at` before, waits, and reads
them again. That distinction is the whole design. An importer can answer
`200`, run to completion, and land nothing — that is exactly what happened to
one card for five months ([FIREFLY-IMPORT-DIAGNOSIS.md](FIREFLY-IMPORT-DIAGNOSIS.md))
— so the record it writes has four results, not two:

| result | meaning |
|---|---|
| `ok` | rows entered (count rose, or the newest `created_at` advanced) |
| `empty` | the importer ran and nothing entered — a quiet day, or a broken import; the home card tells them apart using the ledger's own age |
| `failed` | the importer refused, or could not be reached, or nothing is configured |
| `unverified` | the importer answered but the `firefly` service could not check the ledger |

Only `ok` advances *last import*. A week of `empty` cannot make the card look
freshly fed.

### On the importer

In the importer's environment (its `.env`, or the compose service):

```env
CAN_POST_AUTOIMPORT=true          # for a directory of json+csv pairs
CAN_POST_FILES=true               # for posting one config file instead
AUTO_IMPORT_SECRET=<16+ random characters>
IMPORT_DIR_ALLOWLIST=/import      # the directory, as the importer sees it
```

Restart the importer. `openssl rand -hex 16` makes a fine secret.

### On the hub

In the hub's `.env`:

```env
FIREFLY_IMPORTER_SECRET=<the same secret>
FIREFLY_IMPORT_DIR=/import        # → POST /autoimport   (json + csv pairs)
# — or —
FIREFLY_IMPORT_CONFIG=/home/you/firefly/discover.json   # → POST /autoupload
FIREFLY_IMPORTER_INTERNAL_URL=    # only if the importer's URL differs from FIREFLY_IMPORTER_URL
```

A directory import needs each bank's `name.json` beside its `name.csv`; a
config-file import posts one importer configuration (a `nordigen`, `spectre`
or `simplefin` flow needs no csv at all). Run it once by hand and read the
verdict:

```bash
bash scripts/firefly-import.sh          # triggers, waits, judges by the ledger
bash scripts/firefly-import.sh --check  # prints the last record, triggers nothing
```

Then install the shipped timer, once:

```bash
sudo cp scripts/import/frankenstein-import.* /etc/systemd/system/
sudo sed -i "s/REPLACE_WITH_USER/$(whoami)/g" /etc/systemd/system/frankenstein-import.service
sudo systemctl daemon-reload
sudo systemctl enable --now frankenstein-import.timer
systemctl list-timers frankenstein-import.timer     # shows the next run
```

It fires at seven past every hour from 6am to 10pm, and the script's
`--daily` mode makes every run after the day's first landed one (`ok` or
`empty`) a no-op — so the bank is asked once a day, a failed attempt is
retried within the hour, and `Persistent=true` runs the missed tick as soon
as a box that was off at 6am comes back. A timer rather than a crontab line
because it is visible (`systemctl list-timers`) and survives a crontab edit.

The secret is never printed: not by the script, not by `verify.sh`, and the
importer's response body is saved to `~/.frankenstein/import-last-response.txt`
(mode 0600) rather than echoed.

### Where it shows

- **Data safety card** (home): an *Import* line — *never run* and *runs but
  nothing enters* are the two loud ones.
- **Firefly panel**: *Import health*, per asset account — withdrawals,
  deposits, newest row, and the flags `no credits imported` and `nothing
  landing`.
- `bash scripts/verify.sh` — section *Firefly import (scheduled)*.

## Notes

- Everything is LAN-only — keep Firefly and the hub behind your network / VPN.
- The hub is **read-only**; it never changes anything in Firefly.
- Ports: your Firefly `8093`, your Firefly importer `8094`, the hub tile
  service `8097`, the hub `8080`.
- The 30-day pie uses Firefly's own `insight/expense/category` data, so it
  matches what Firefly shows.
- Transfers between your own accounts are never spending. The pay-cycle view
  reads them only to recognise the savings that leave each paycheck — see
  [BUDGETS.md](BUDGETS.md#the-pay-cycle--what-i-spent-and-whats-left-to-spend).
