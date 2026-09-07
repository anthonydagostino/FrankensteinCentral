# Connecting Gmail (one-time)

Goal: let the Gmail sub-app read your inbox so the assistant can triage it.
You do this **once**. It reuses the Google project your PowerBuy app already
has, so there's nothing new to create in Google.

Total time: ~5 minutes. You'll copy 2 values, add 1 web address, click "Allow".

---

## Step 1 — Get your Google client ID & secret from Render

1. Go to https://dashboard.render.com and open your **Powerbuy** service.
2. Click the **Environment** tab on the left.
3. Find these two variables and copy their values somewhere temporary:
   - `Google__ClientId`  (this is your **client ID**)
   - `Google__ClientSecret`  (this is your **client secret**)

*(If they're named slightly differently, look for anything with `Google` and
`Client` in the name.)*

---

## Step 2 — Add the hub's redirect address in Google

1. Go to https://console.cloud.google.com/apis/credentials
2. Make sure the project selected at the top is the same one PowerBuy uses.
3. Under **OAuth 2.0 Client IDs**, click the client whose ID matches the one
   you copied in Step 1.
4. Under **Authorized redirect URIs**, click **+ ADD URI** and paste exactly:

   ```
   http://localhost:8083/auth/callback
   ```

5. Click **Save**.

---

## Step 3 — Put the two values in your local .env

1. In the FrankensteinCentral folder, copy `.env.example` to `.env` if you
   haven't already.
2. Fill in these two lines (leave `GOOGLE_REFRESH_TOKEN` blank — the Allow
   step fills that in for you):

   ```
   GOOGLE_CLIENT_ID=<paste the client ID>
   GOOGLE_CLIENT_SECRET=<paste the client secret>
   ```

---

## Step 4 — Start the hub and click Allow

1. Start everything:

   ```
   docker compose up --build
   ```

2. In your browser, go to: **http://localhost:8083/auth/login**
3. Google will ask you to sign in and show a permissions screen. Click
   **Allow**.
4. You'll land on a "✅ Gmail connected" page. That's it.

From now on your real inbox shows up in the hub, and the assistant triages it.
You won't have to do this again — the connection is saved, even across
restarts.

---

## Google Calendar uses this same connection

The schedule service does not run its own OAuth flow — it borrows this one, so
connecting Gmail is also what connects Google Calendar. That means one consent
screen covers both, and it also means one specific way to end up with mail
working and the calendar dead:

**A Google login keeps the permissions it was granted, permanently.** If you
pasted a `GOOGLE_REFRESH_TOKEN` from PowerBuy (the fast path in `.env.example`),
that token was approved for mail alone. It will refresh happily forever, Gmail
will work perfectly, and every single Calendar call will be refused — not
because anything is broken, but because that login was never allowed near your
calendar. Waiting does not fix it and neither does restarting.

The fix is one click: open **http://localhost:8083/auth/login**, approve the
calendar permission, and a new login is minted and saved with both. The
dashboard also offers this as a **Connect Google Calendar** button on the week
card whenever it detects this state.

To check where you stand:

```
bash scripts/verify.sh        # the "google calendar" section says which state
curl -s localhost:8084/calendar-health
```

`state` is one of:

| state | what it means | what to do |
|---|---|---|
| `ok` | Calendar answered; the credential is in scope | nothing |
| `needs_consent` | a credential exists, Calendar refuses it | re-run `/auth/login` |
| `disconnected` | no Google credential at all | connect Gmail first |
| `unreachable` | Calendar could not be reached just now | wait; it is transient |

Events are imported into the schedule service every `GCAL_SYNC_SECONDS`
(15 minutes by default), independently of `AUTO_SYNC_SECONDS`.

---

## Troubleshooting

- **"Access blocked / app not verified":** Your Google project may be in
  "Testing" mode. That's fine for personal use — just make sure your own Gmail
  address is listed as a test user (Google Cloud Console → APIs & Services →
  OAuth consent screen → Test users). You already use PowerBuy, so you likely
  are.
- **"redirect_uri_mismatch":** The address in Step 2 must match exactly,
  including `http://` and no trailing slash: `http://localhost:8083/auth/callback`.
- **Want to disconnect:** delete the `gmail_token` docker volume
  (`docker compose down -v` removes it) and the connection is wiped.
