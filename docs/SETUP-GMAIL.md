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
4. Under **Authorized redirect URIs**, click **+ ADD URI** and paste the
   address you open the dashboard at, with `/api/gmail/auth/callback` on the
   end. On the OptiPlex that is:

   ```
   http://192.168.1.185.nip.io:8080/api/gmail/auth/callback
   ```

   Why the `nip.io` name: Google refuses plain `http://` redirects to a raw
   IP address, and `192.168.1.185.nip.io` is a public DNS name that resolves
   to the box's LAN address, so the console accepts it. Open the dashboard at
   that same name when you connect. If the box's LAN address ever changes,
   change the name here to match.

   The callback goes through the gateway on port 8080, the same port the
   dashboard is served on, so it works from any browser on the network. The
   gmail service's own port (8083) is bound to the box's loopback address and
   is never reachable from another device.

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

2. In your browser, open the dashboard at the address you registered in
   step 2 (**http://192.168.1.185.nip.io:8080**) and click **Connect Google
   Calendar** on the week card, or go straight to
   **http://192.168.1.185.nip.io:8080/api/gmail/auth/login**.
3. Click **Continue to Google**, sign in, and click **Allow**. Leave the
   calendar permission ticked.
4. You land on a "✅ Google connected" page. That's it.

**If the page after Google does not load**, nothing is lost: the connect page
has a paste box for exactly this. Copy the whole address of the failed page
and paste it there (see *Troubleshooting*).

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

The fix is one click: open **/api/gmail/auth/login** on the dashboard, approve
the calendar permission, and a new login is minted and saved with both. The
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

## Keep it connected

Two things made the connection die on the box, and both are fixed once.

**1. An OAuth app in "Testing" expires its logins after seven days.** That is
Google's rule for any Cloud project whose consent screen has publishing status
*Testing*: every refresh token it mints dies a week later, Gmail starts
answering `invalid_grant`, and the week card shows the reconnect button again.
Reconnecting buys exactly one more week. The fix is to publish the app:

1. Google Cloud Console → **APIs & Services** → **OAuth consent screen**.
2. Under *Publishing status*, click **Publish app** and confirm.
3. You do **not** need to submit it for verification. Google warns that
   unverified apps with sensitive scopes are limited to 100 users and show an
   "unverified app" screen on consent — click *Advanced → Go to … (unsafe)*
   the next time you connect. That screen is the only cost, and it is a
   personal app with one user.
4. Reconnect once at `/api/gmail/auth/login` so the saved login was minted
   under the published status. From then on it does not expire.

The hub tells you when this is what happened: the week card's caveat says
*Google signed this login out on <date>* with Google's reason, and
`bash scripts/verify.sh` reports `gmail conn` as `REVOKED` with the same
line. A revoked login is reported as **disconnected**, never as "checked 4
minutes ago" — a token Google refuses is not a connection.

**2. The seed token used to win over the real one.** `GOOGLE_REFRESH_TOKEN`
in `.env` is PowerBuy's token, minted for mail alone. Until 2026-10-06 the
gmail service loaded it *ahead of* the login you consented to through
`/auth/login` — so after every restart the calendar went back to
`needs_consent` until someone reconnected again. The saved consent now
outranks the environment seed. Once you have connected through the hub, you
can delete `GOOGLE_REFRESH_TOKEN` from `.env` entirely; it is only a seed for a
box that has never been connected.

The gmail service refreshes the token every six hours on its own, which also
keeps it from going stale through disuse. Nothing else needs tending.

## Troubleshooting

- **"Access blocked / app not verified":** the Google project is in
  "Testing" mode. Make sure your own Gmail address is listed as a test user
  (Google Cloud Console → APIs & Services → OAuth consent screen → Test users)
  to get connected now — and then publish the app (see *Keep it connected*
  above), because a Testing-mode login expires every seven days.
- **It keeps disconnecting about once a week:** that is the Testing-mode
  expiry. Publish the app; see *Keep it connected*.
- **The page after Google never loads (spinner, then a timeout):** the
  redirect URI points somewhere this browser cannot reach. Until 2026-11 the
  default pointed at the gmail service's own port, 8083, which is bound to
  the box's loopback address, so the callback only worked in a browser on the
  OptiPlex itself. Make sure `GOOGLE_REDIRECT_URI` in `.env` is empty (or set
  to the gateway address from step 2), restart the stack, and check that the
  connect page says Google sends the browser back to
  `http://192.168.1.185.nip.io:8080/api/gmail/auth/callback`.

  Whatever the cause, nothing is lost: the authorization code is sitting in
  the failed page's address bar. Copy the whole address, go back to the
  connect page, and paste it into the **"If the page after Google does not
  load"** box. That completes the connection server-side and saves the
  credential exactly as the redirect would have.
- **The callback lands on the sign-in page:** this device had no dashboard
  session. Sign in; the gateway carries the callback address through and
  finishes the connection right after.
- **"redirect_uri_mismatch":** the address in Step 2 must match exactly what
  the connect page shows, including `http://`, the port and no trailing slash:
  `http://192.168.1.185.nip.io:8080/api/gmail/auth/callback`.
- **Want to disconnect:** delete the `gmail_token` docker volume
  (`docker compose down -v` removes it) and the connection is wiped.
