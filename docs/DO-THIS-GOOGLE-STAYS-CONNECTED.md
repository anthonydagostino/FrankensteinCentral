# Do this: stop Google from signing the hub out every week

Everything in code is shipped. This is the part only you can do, because it is
a setting on your Google Cloud project. Budget 5 minutes.

## Why it kept disconnecting

A Google Cloud OAuth app whose consent screen is in **Testing** status issues
logins that expire after **seven days**. The hub's Gmail and Google Calendar
share one such login, so once a week Google started refusing it, the calendar
on the week card went blank behind a *Connect Google Calendar* button, and
reconnecting bought one more week. Publishing the app removes the expiry.

## Step 1 — Publish the app

1. Open <https://console.cloud.google.com/apis/credentials/consent> and pick
   the project whose client ID is in the hub's `.env` (`GOOGLE_CLIENT_ID`).
2. Under **Publishing status** click **Publish app**, then **Confirm**.
3. If Google offers a verification form, skip it. A personal app with one
   user does not need verification; the only effect is an "unverified app"
   screen the next time you connect, which has an *Advanced → Go to …* link.

**Check:** the consent screen page now says *In production*.

## Step 2 — Reconnect once, under the new status

On a browser running on the OptiPlex (or from anywhere, using the paste box
the connect page offers), open:

```
http://192.168.1.185:8080/api/gmail/auth/login
```

Approve **both** Gmail and Calendar. The hub saves this login and prefers it
over anything in `.env` from now on.

**Check:** the week card shows *Google Calendar connected* (or *N from
Google*) with no caveat, and `bash scripts/verify.sh` shows `PASS  gmail conn`
and `PASS  google calendar`.

## Step 3 — Optional: remove the seed

`GOOGLE_REFRESH_TOKEN` in the hub's `.env` was PowerBuy's mail-only token,
used to get connected the first time. It is no longer read ahead of the saved
login, so it can go:

```bash
cd ~/FrankensteinCentral && nano .env     # delete the GOOGLE_REFRESH_TOKEN line
```

## If it disconnects again

The week card's caveat will say *Google signed this login out on <date>* and
why. `bash scripts/verify.sh` says the same under `gmail conn`. If the reason
says expired and it is about a week after you connected, Step 1 did not take:
check the publishing status again.
