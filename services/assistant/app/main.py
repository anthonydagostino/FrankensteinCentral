import os

import httpx
from fastapi import FastAPI

from .orchestrator import extract_datetime

app = FastAPI(title="Assistant Service")

POWERBUY_URL = os.environ.get("POWERBUY_URL", "http://powerbuy:8000")
FITNESS_URL = os.environ.get("FITNESS_URL", "http://fitness:8000")
GMAIL_URL = os.environ.get("GMAIL_URL", "http://gmail:8000")
SCHEDULE_URL = os.environ.get("SCHEDULE_URL", "http://schedule:8000")

# Last computed briefing, so the dashboard can read it without re-polling.
STATE: dict = {"items": [], "summary": "Not synced yet."}


@app.get("/health")
async def health():
    return {"service": "assistant", "briefing_items": len(STATE["items"])}


async def _get(client: httpx.AsyncClient, url: str) -> dict:
    try:
        r = await client.get(url, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001 - a down sub-app just contributes nothing
        return {}


async def build_briefing() -> list[dict]:
    items: list[dict] = []
    async with httpx.AsyncClient() as client:
        emails = await _get(client, f"{GMAIL_URL}/needs-reply")
        fitness = await _get(client, f"{FITNESS_URL}/plan")
        powerbuy = await _get(client, f"{POWERBUY_URL}/summary")
        cal = await _get(client, f"{SCHEDULE_URL}/events")

    for e in emails.get("emails", []):
        verb = "Interview" if e.get("category") == "interview" else "Reply needed"
        items.append(
            {"source": "gmail", "message": f"{verb}: “{e['subject']}” — from {e['from']}"}
        )

    tp = fitness.get("today_plan")
    if tp:
        lifts = ", ".join(tp.get("lifts", [])) or "recovery"
        items.append(
            {"source": "fitness", "message": f"Today is {tp['focus']} day: {lifts}."}
        )

    s = powerbuy.get("summary")
    if s:
        alerts = []
        if s.get("unpaid_count"):
            alerts.append(f"{s['unpaid_count']} unpaid")
        if s.get("expiring_soon_count"):
            alerts.append(f"{s['expiring_soon_count']} expiring soon")
        if s.get("not_delivered_count"):
            alerts.append(f"{s['not_delivered_count']} not delivered")
        detail = "; ".join(alerts) if alerts else "all clear"
        items.append(
            {
                "source": "powerbuy",
                "message": f"Expected profit ${s.get('expected_profit', 0)} — {detail}.",
            }
        )

    upcoming = cal.get("events", [])
    if upcoming:
        nxt = upcoming[0]
        items.append(
            {"source": "schedule", "message": f"Next up: {nxt['title']} at {nxt['starts_at']}."}
        )

    return items


@app.get("/briefing")
async def briefing():
    return STATE


@app.post("/sync")
async def sync():
    """The core orchestration pass.

    Reads the inbox, and for any scheduled interview it detects, drops a
    calendar event into the Schedule sub-app — the gmail -> schedule flow.
    Then rebuilds the briefing.
    """
    created = []
    async with httpx.AsyncClient() as client:
        emails = await _get(client, f"{GMAIL_URL}/needs-reply")
        for e in emails.get("emails", []):
            if e.get("category") != "interview":
                continue
            when = extract_datetime(f"{e.get('subject','')} {e.get('snippet','')}")
            if not when:
                continue
            resp = await client.post(
                f"{SCHEDULE_URL}/events",
                json={
                    "title": f"Interview — {e['from']}",
                    "starts_at": when,
                    "source": "gmail",
                    "external_id": e["id"],
                },
                timeout=8,
            )
            if resp.status_code < 300 and resp.json().get("created"):
                created.append(resp.json()["event"])

    STATE["items"] = await build_briefing()
    STATE["summary"] = f"{len(STATE['items'])} things on your plate."
    return {"synced": True, "events_created": created, "briefing": STATE}


@app.get("/")
async def root():
    return {"app": "Assistant", "endpoints": ["/briefing", "/sync", "/health"]}
