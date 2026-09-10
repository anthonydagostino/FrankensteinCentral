# Letting Bones text you

The manager (Bones) can text you a digest — either on demand (the **📱 Text me**
button on the hub) or automatically after each sync. Pick **one** channel below,
put the values in your `.env`, and set `NOTIFY_CHANNEL`.

> **About iMessage:** there's no way to send iMessage from a self-hosted app —
> Apple has no API and it needs a Mac running Messages. So the options are
> Telegram, WhatsApp, SMS, or a webhook. Telegram is the easiest by far.

Common settings (in `.env`):

```
NOTIFY_CHANNEL=telegram      # telegram | whatsapp | sms | webhook
NOTIFY_ON_SYNC=true          # also text automatically after each sync (deduped)
```

---

## Option A — Telegram (easiest, free, 2 minutes) ✅ recommended

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
   It gives you a **bot token** like `123456:ABC-DEF...`.
2. Message your new bot once (say "hi") so it's allowed to message you.
3. Get your **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and look
   for `"chat":{"id":123456789}`.
4. In `.env`:
   ```
   NOTIFY_CHANNEL=telegram
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=123456789
   ```

---

## Option B — WhatsApp (via Twilio)

1. Make a free account at twilio.com and open the **WhatsApp sandbox**
   (Messaging → Try it out → Send a WhatsApp message). Follow the "join …"
   step from your phone to opt in.
2. From the Twilio console grab your **Account SID** and **Auth Token**, and the
   sandbox **from** number (looks like `whatsapp:+14155238886`).
3. In `.env`:
   ```
   NOTIFY_CHANNEL=whatsapp
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   NOTIFY_TO=+1<your number>
   ```
   (For a permanent number instead of the sandbox, you register a WhatsApp
   sender in Twilio — the env stays the same.)

---

## Option C — SMS (via Twilio)

Same as WhatsApp but with a Twilio phone number that can send SMS:

```
NOTIFY_CHANNEL=sms
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_SMS_FROM=+1<your twilio number>
NOTIFY_TO=+1<your number>
```

---

## Option D — Webhook (Discord/Slack/anything)

Point it at any URL that accepts a JSON `{ "text": ... }` (also sends
`content` for Discord):

```
NOTIFY_CHANNEL=webhook
NOTIFY_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## Try it

1. `docker compose up --build`
2. On the hub, click **📱 Text me** → Bones sends the current digest.
3. If it says "Not set up", double-check `NOTIFY_CHANNEL` and that channel's
   values in `.env`.

A digest looks like:

> 🦴 Bones here — 3 emails to reply; 2 open tasks; over budget. Today is Pull
> day. Next up: Interview @ Acme.

With `NOTIFY_ON_SYNC=true` (and `AUTO_SYNC_SECONDS` set), Bones texts you on his
own whenever the digest changes — so you're not pinged with the same thing twice.
