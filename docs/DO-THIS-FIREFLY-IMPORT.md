# Do this: make Firefly import by itself every morning

Everything in code is already shipped. This is the part only you can do,
because it needs a secret and your importer's own settings. Budget 20 minutes.
Do the steps in order. Each one ends with a way to check it worked.

Where things are:

- **The hub** = FrankensteinCentral, at `~/FrankensteinCentral` on the OptiPlex.
- **The importer** = the Firefly Data Importer, the thing at port 8094 you
  open in a browser to pull SimpleFIN into Firefly.

---

## Step 1 — Find the importer's settings

SSH into the OptiPlex. Run:

```bash
docker ps --format '{{.Names}}  {{.Image}}' | grep -i import
```

Note the container name (something like `firefly-importer`). Then run this with
that name:

```bash
docker inspect <container-name> --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

It prints a folder. That folder holds the importer's `docker-compose.yml` and
usually its `.env`. Go there:

```bash
cd <that folder>
ls
```

**Check:** you see a `docker-compose.yml` (or `compose.yaml`). If there is a
`.env`, that is the importer's settings file. If there is no `.env`, the
settings are inside the `environment:` block of the compose file. Either way,
the next step edits the same three lines.

---

## Step 2 — See what the importer already has

```bash
docker inspect <container-name> --format '{{json .Config.Env}}' | tr ',' '\n' | grep -E 'AUTO_IMPORT_SECRET|CAN_POST_FILES|CAN_POST_AUTOIMPORT|SIMPLEFIN_TOKEN' | sed 's/=.*/=<hidden>/'
```

This shows which of the four settings exist, with the values hidden.

- `SIMPLEFIN_TOKEN` should be there. If it is **not**, you have been pasting
  your SimpleFIN token into the browser each time. An unattended run cannot do
  that, so it has to go in the importer's settings in Step 3.
- `AUTO_IMPORT_SECRET` is probably **missing or empty**. That is expected. It is
  the one key that lets something other than a browser start an import, and
  the importer ships without it.

---

## Step 3 — Give the importer a secret and allow unattended runs

Make a secret:

```bash
openssl rand -hex 16
```

Copy the output. You will paste it twice (here, and in Step 5).

Open the importer's `.env` (or the compose file's `environment:` block):

```bash
nano .env
```

Add or change these lines. Use YOUR secret, not the placeholder:

```env
CAN_POST_FILES=true
AUTO_IMPORT_SECRET=paste-your-secret-here
```

If Step 2 showed no `SIMPLEFIN_TOKEN`, add it too:

```env
SIMPLEFIN_TOKEN=your-simplefin-token
```

Save (Ctrl+O, Enter, Ctrl+X). Restart the importer:

```bash
docker compose up -d
```

**Check:** run the Step 2 command again. `CAN_POST_FILES` and
`AUTO_IMPORT_SECRET` now show up.

---

## Step 4 — Save the import configuration you already use

You have been clicking through the same SimpleFIN import each time. The
importer can hand you that whole setup as one file, and the hub will replay it.

1. Open the importer in a browser: `http://192.168.1.185:8094`
2. Run the import exactly as you always do (SimpleFIN, your account mapping,
   through to the end).
3. On the final screen there is a link that says **download configuration
   file** (it also appears at the top of the mapping screens). Click it. You get
   a `.json` file.
4. Put that file on the OptiPlex. For example, from your laptop:

```bash
scp ~/Downloads/import_config.json anthony@192.168.1.185:~/firefly-import.json
```

**Check:** on the OptiPlex, `ls -l ~/firefly-import.json` shows the file.

Every future automatic run uses this file. If you ever change the account
mapping in the browser, download the file again and replace this one.

---

## Step 5 — Tell the hub about it

```bash
cd ~/FrankensteinCentral
nano .env
```

Add these two lines (the secret is the same one from Step 3):

```env
FIREFLY_IMPORTER_SECRET=paste-your-secret-here
FIREFLY_IMPORT_CONFIG=/home/anthony/firefly-import.json
```

Use the real full path to the file from Step 4 (`echo ~/firefly-import.json`
prints it). Save and exit. No container restart is needed for this: the import
script reads `.env` directly.

---

## Step 6 — Run it once by hand

```bash
cd ~/FrankensteinCentral
bash scripts/firefly-import.sh
```

It triggers the importer, waits up to two minutes, and reads Firefly before and
after. The last line is the verdict. What each one means:

| Last line says | What it means | What to do |
|---|---|---|
| `verdict: OK — the ledger moved (N new transaction(s) …)` | Working. | Go to Step 7. |
| `verdict: EMPTY — the importer ran, nothing entered the ledger …` | The trigger works; the bank had nothing new today. | Fine. Go to Step 7. If you KNOW there were new transactions, see Step 8. |
| `IMPORT FAILED (importer_http_403)` | The secret in the hub `.env` does not match the importer's. | Re-check Step 3 and Step 5 are the same string. |
| `IMPORT FAILED (importer_http_…)` any other number | `CAN_POST_FILES` is not `true`, or the importer was not restarted. | Redo the end of Step 3. |
| `IMPORT FAILED (importer_unreachable)` | Wrong importer address. | Check `FIREFLY_IMPORTER_URL` in the hub `.env` opens in a browser. |
| `IMPORT NOT CONFIGURED: set …` | A line from Step 5 is missing, or the file path is wrong. | It names the key. Re-open the hub `.env`. |
| `verdict: UNVERIFIED — …` | The hub's `firefly` container did not answer. | `docker compose up -d firefly` then rerun. |

**Check:** `bash scripts/firefly-import.sh --check` prints the record with
`last_import_result` showing `ok` or `empty`.

---

## Step 7 — Put it on a timer

```bash
crontab -e
```

Add this line at the bottom (it runs every day at 6:23 am):

```cron
23 6 * * *  cd ~/FrankensteinCentral && bash scripts/firefly-import.sh >> ~/.frankenstein/import.log 2>&1
```

Save and exit.

**Check:** `crontab -l` shows the line. The next morning, open the hub. The
**Data safety** card's *Import* line no longer says "never run". Or run:

```bash
bash scripts/verify.sh | grep "import"
```

and see `PASS  import cron`.

---

## Step 8 — Optional: the card that was losing rows

This is SCRUM-40. Not needed for the daily import to work, but it is why the
import mattered.

1. In the hub, open the **Firefly** tile. Scroll to **Import health**.
2. Any account marked **no credits imported** or **nothing landing** is the one
   to look at.
3. Open `docs/FIREFLY-IMPORT-DIAGNOSIS.md` and go down its list for that
   account. The most likely culprit is the importer's duplicate-detection
   setting for that account.
4. Fix it in the importer (browser), run the import once, and **download the
   configuration file again** so Step 4's file has the fix. Copy it over the old
   one on the OptiPlex.
5. Write which setting it was on SCRUM-40 and close it.

**Check:** after the next import the flag on that account is gone.

---

## If you get stuck

`bash scripts/verify.sh` prints a section called *Firefly import (scheduled)*.
It never prints the secret. Paste that section into a Claude session with
"the import isn't working" and it has enough to diagnose from.
