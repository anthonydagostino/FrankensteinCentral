# Connecting the Plex app (a server shared with you)

The Plex sub-app is a **read-only dashboard** for a Plex server that someone
has shared with your Plex account — you don't need to run Plex yourself. It
shows continue-watching / on-deck, recently added, and the shared libraries,
and every title deep-links into app.plex.tv.

How it works: it signs requests with **your own Plex account token** and
auto-discovers the shared server through plex.tv, so it reaches the server
even though it lives on someone else's network. The token stays server-side
and is never sent to the browser.

Because you're a shared user (not the server owner), Plex does not let you see
who else is streaming — so there's no "streaming now" panel, by design.

## 1. Get your Plex token

1. Sign in at **https://app.plex.tv** with YOUR account (the one the server
   was shared with).
2. Open any movie/show **on the shared server**.
3. Click the **⋮ (three dots) → Get Info → View XML**.
4. A new tab opens; look at its URL — the end contains
   `X-Plex-Token=xxxxxxxxxxxxxxxxxxxx`. Copy that value.

(Tokens occasionally rotate if you sign out everywhere; just repeat these
steps if the app ever shows "not connected" again.)

## 2. Configure the hub

In the FrankensteinCentral `.env`:
```
PLEX_TOKEN=<the token from step 1>
# Only if several servers are shared with you and you want a specific one:
# PLEX_SERVER_NAME=TheirServerName
```
Then:
```
docker compose up -d --build plex assistant gateway
```

## 3. Check it

```
curl -s localhost:8092/summary
```
You want `"connected": true` with the server's name and library count. Then
open **▦ → Plex** on the hub.

## Notes

- **Privacy**: the only things sent to plex.tv / the Plex server are your
  token and standard Plex API reads. Nothing about your dashboard usage is
  shared, and the token is never exposed to the browser.
- The app is read-only — it can't delete or modify anything on their server.
- If discovery picks a slow route, you can pin a direct URL with `PLEX_URL=`
  (ask the server's owner for their remote address), but auto-discovery via
  plex.tv is the zero-config path.
