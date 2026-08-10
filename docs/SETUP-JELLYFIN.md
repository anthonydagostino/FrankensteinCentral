# Connecting the Jellyfin app

The Jellyfin sub-app is a **read-only dashboard** — it shows what's worth seeing
at a glance (continue watching, next up, recently added, library counts, and
who's streaming right now). It never proxies or plays media, and it has no
database. Your API key stays server-side and is never sent to the browser.

Until it's connected it shows an empty **"not connected"** state (no sample data).

## Steps (when you're home)

1. In Jellyfin, go to **Dashboard → Advanced → API Keys → +** and create a key
   (name it e.g. "FrankensteinCentral"). Copy it.

2. In the FrankensteinCentral `.env`:
   ```
   JELLYFIN_URL=http://<homelab-ip>:8096
   JELLYFIN_API_KEY=<the key you just made>
   # JELLYFIN_USER_ID=   # optional — leave blank to use the first user
   ```

3. Bring it up:
   ```
   docker compose up -d --build jellyfin assistant gateway
   ```

4. Open **Jellyfin** on the hub. You'll see your real library, and Milo will
   report media status on the floor (e.g. "1 streaming now"). Bones mentions
   active streams in your briefing.

## What it shows

- **Streaming now** — active sessions (who, what, which device)
- **Continue watching** — in-progress items with a progress bar
- **Next up** — the next episodes in shows you're watching
- **Recently added** — new stuff in the library
- **Counts** — movies / series / episodes

## Finding your user id (optional)

Only needed if you have multiple users and want a specific one:
`http://<homelab-ip>:8096/Users?api_key=<your key>` → copy the `Id` you want
into `JELLYFIN_USER_ID`.

## Notes

- Keep Jellyfin (and this app) on your LAN / behind your VPN.
- The app only reads; it never changes anything in Jellyfin.
