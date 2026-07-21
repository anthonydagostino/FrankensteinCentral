"""Outbound notifications so the manager (Bones) can text the user.

One channel is chosen via NOTIFY_CHANNEL. Credentials come from env only — never
committed. When nothing is configured, send() is a harmless no-op.

Supported channels:
  telegram  — TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  whatsapp  — Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
              TWILIO_WHATSAPP_FROM (e.g. whatsapp:+14155238886), NOTIFY_TO
  sms       — Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
              TWILIO_SMS_FROM (e.g. +1415...), NOTIFY_TO
  webhook   — NOTIFY_WEBHOOK_URL (Discord/Slack/any {text|content} POST target)
"""
import os

import httpx

CHANNEL = os.environ.get("NOTIFY_CHANNEL", "").strip().lower()


def configured() -> bool:
    return bool(CHANNEL)


async def _telegram(text: str) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    return {"sent": r.status_code < 300, "status": r.status_code}


async def _twilio(text: str, from_key: str, whatsapp: bool) -> dict:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    src = os.environ[from_key]
    to = os.environ["NOTIFY_TO"]
    if whatsapp:
        if not src.startswith("whatsapp:"):
            src = f"whatsapp:{src}"
        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={"From": src, "To": to, "Body": text},
            auth=(sid, token),
            timeout=15,
        )
    return {"sent": r.status_code < 300, "status": r.status_code}


async def _webhook(text: str) -> dict:
    url = os.environ["NOTIFY_WEBHOOK_URL"]
    async with httpx.AsyncClient() as client:
        # "content" works for Discord, "text" for Slack/most others — send both.
        r = await client.post(url, json={"text": text, "content": text}, timeout=15)
    return {"sent": r.status_code < 300, "status": r.status_code}


async def send(text: str) -> dict:
    """Send a message on the configured channel. Never raises."""
    if not CHANNEL:
        return {"sent": False, "reason": "NOTIFY_CHANNEL not set"}
    try:
        if CHANNEL == "telegram":
            return await _telegram(text)
        if CHANNEL == "whatsapp":
            return await _twilio(text, "TWILIO_WHATSAPP_FROM", whatsapp=True)
        if CHANNEL == "sms":
            return await _twilio(text, "TWILIO_SMS_FROM", whatsapp=False)
        if CHANNEL == "webhook":
            return await _webhook(text)
        return {"sent": False, "reason": f"unknown channel '{CHANNEL}'"}
    except KeyError as exc:
        return {"sent": False, "reason": f"missing env {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "reason": str(exc)}
