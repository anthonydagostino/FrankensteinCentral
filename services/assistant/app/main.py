import asyncio
import os
from datetime import datetime

import httpx
from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .orchestrator import extract_datetime

app = FastAPI(title="Assistant Service")

POWERBUY_URL = os.environ.get("POWERBUY_URL", "http://powerbuy:8000")
FITNESS_URL = os.environ.get("FITNESS_URL", "http://fitness:8000")
GMAIL_URL = os.environ.get("GMAIL_URL", "http://gmail:8000")
SCHEDULE_URL = os.environ.get("SCHEDULE_URL", "http://schedule:8000")
FINANCE_URL = os.environ.get("FINANCE_URL", "http://finance:8000")
AUTO_SYNC_SECONDS = int(os.environ.get("AUTO_SYNC_SECONDS", "0"))

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

# Last computed briefing, so the dashboard can read it without re-polling.
STATE: dict = {"items": [], "summary": "Not synced yet."}

# The floor: one manager plus a worker specialised to each sub-app. The lounge
# UI renders these; each worker walks to its station when it has a job.
AGENTS = [
    {"id": "bones", "name": "Bones", "role": "manager", "station": "desk",
     "color": "#e23b5a", "blurb": "Runs the floor. Assigns jobs and keeps your notes."},
    {"id": "posty", "name": "Posty", "role": "worker", "station": "gmail",
     "color": "#4aa3ff", "blurb": "Inbox runner — triages what needs a reply."},
    {"id": "cal", "name": "Cal", "role": "worker", "station": "schedule",
     "color": "#7bd88f", "blurb": "Calendar keeper — books what Bones finds."},
    {"id": "rep", "name": "Rep", "role": "worker", "station": "powerbuy",
     "color": "#ff8a5b", "blurb": "Deals desk — profit, unpaid, expiring."},
    {"id": "coach", "name": "Coach", "role": "worker", "station": "fitness",
     "color": "#c58cff", "blurb": "Gym & food — today's plan and groceries."},
    {"id": "penny", "name": "Penny", "role": "worker", "station": "finance",
     "color": "#5bd6c0", "blurb": "Money desk — bills, subscriptions, what's due."},
]
_BY_STATION = {a["station"]: a for a in AGENTS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id SERIAL PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deadlines (
    id SERIAL PRIMARY KEY, title TEXT NOT NULL, due_at TEXT,
    source TEXT NOT NULL, external_id TEXT UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id SERIAL PRIMARY KEY, agent TEXT NOT NULL, station TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""


async def _auto_sync_loop():
    """Keep the floor and briefing fresh on their own, no browser needed."""
    await asyncio.sleep(min(15, AUTO_SYNC_SECONDS))  # let siblings boot first
    while True:
        try:
            await sync()
        except Exception:  # noqa: BLE001 - never let the loop die
            pass
        await asyncio.sleep(AUTO_SYNC_SECONDS)


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
    if AUTO_SYNC_SECONDS > 0:
        asyncio.create_task(_auto_sync_loop())


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


def _now() -> str:
    return datetime.utcnow().isoformat()


async def _log(conn, agent: str, station: str, action: str, detail: str = "") -> None:
    await conn.execute(
        "INSERT INTO activity (agent, station, action, detail, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (agent, station, action, detail, _now()),
    )


async def _remember(conn, content: str) -> None:
    await conn.execute(
        "INSERT INTO memory (content, created_at) VALUES (%s, %s)", (content, _now())
    )


async def _add_deadline(conn, title: str, due_at, source: str, external_id: str) -> None:
    await conn.execute(
        "INSERT INTO deadlines (title, due_at, source, external_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (external_id) DO NOTHING",
        (title, due_at, source, external_id, _now()),
    )


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
        finance = await _get(client, f"{FINANCE_URL}/summary")

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
            {"source": "powerbuy",
             "message": f"Expected profit ${s.get('expected_profit', 0)} — {detail}."}
        )

    upcoming = cal.get("events", [])
    if upcoming:
        nxt = upcoming[0]
        items.append(
            {"source": "schedule", "message": f"Next up: {nxt['title']} at {nxt['starts_at']}."}
        )

    due = finance.get("upcoming", [])
    if due:
        names = ", ".join(f"{b['name']} (${b['amount']})" for b in due[:3])
        items.append(
            {"source": "finance", "message": f"Bills due soon: {names}."}
        )

    return items


@app.get("/briefing")
async def briefing():
    return STATE


@app.get("/agents")
async def agents():
    """The roster the lounge renders: manager + one worker per station."""
    return {"agents": AGENTS}


@app.get("/space")
async def space():
    """The assistant's persistent memory: notes, open deadlines, recent activity."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        mem = await (await conn.execute(
            "SELECT content, created_at FROM memory ORDER BY id DESC LIMIT 8"
        )).fetchall()
        dls = await (await conn.execute(
            "SELECT title, due_at, source FROM deadlines ORDER BY due_at NULLS LAST LIMIT 20"
        )).fetchall()
        act = await (await conn.execute(
            "SELECT agent, station, action, detail, created_at "
            "FROM activity ORDER BY id DESC LIMIT 30"
        )).fetchall()
    return {"memory": mem, "deadlines": dls, "activity": act}


@app.post("/sync")
async def sync():
    """Core orchestration pass, now narrated as agent jobs for the lounge.

    Each worker "visits" its station: Posty reads the inbox, Cal books any
    interview found, Rep checks purchases, Coach checks the plan. Bones writes a
    note summarising. Everything is logged to the persistent space, and the list
    of jobs is returned so the lounge can animate the workers walking out.
    """
    jobs: list[dict] = []
    created = []
    async with pool.connection() as conn:
        async with httpx.AsyncClient() as client:
            # Posty -> Gmail
            emails = await _get(client, f"{GMAIL_URL}/needs-reply")
            mails = emails.get("emails", [])
            posty = _BY_STATION["gmail"]
            summary = f"{len(mails)} need a reply" if mails else "inbox clear"
            await _log(conn, posty["name"], "gmail", "triaged inbox", summary)
            jobs.append({"agent": posty["id"], "name": posty["name"], "station": "gmail",
                         "summary": summary})
            for e in mails:
                if e.get("category") == "deadline":
                    await _add_deadline(conn, e["subject"], None, "gmail", f"mail:{e['id']}")

            # Cal -> Schedule (book interviews Posty surfaced)
            cal_agent = _BY_STATION["schedule"]
            booked = 0
            for e in mails:
                if e.get("category") != "interview":
                    continue
                when = extract_datetime(f"{e.get('subject','')} {e.get('snippet','')}")
                if not when:
                    continue
                resp = await client.post(
                    f"{SCHEDULE_URL}/events",
                    json={"title": f"Interview — {e['from']}", "starts_at": when,
                          "source": "gmail", "external_id": e["id"]},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    created.append(resp.json()["event"])
                    booked += 1
                    await _add_deadline(conn, f"Interview — {e['from']}", when,
                                        "schedule", f"evt:{e['id']}")
            cal_summary = f"booked {booked} event(s)" if booked else "calendar up to date"
            await _log(conn, cal_agent["name"], "schedule", "checked calendar", cal_summary)
            jobs.append({"agent": cal_agent["id"], "name": cal_agent["name"],
                         "station": "schedule", "summary": cal_summary})

            # Rep -> PowerBuy
            pb = await _get(client, f"{POWERBUY_URL}/summary")
            rep = _BY_STATION["powerbuy"]
            s = pb.get("summary", {})
            rep_summary = (
                f"${s.get('expected_profit', 0)} profit, {s.get('unpaid_count', 0)} unpaid"
                if s else "no data"
            )
            await _log(conn, rep["name"], "powerbuy", "checked purchases", rep_summary)
            jobs.append({"agent": rep["id"], "name": rep["name"], "station": "powerbuy",
                         "summary": rep_summary})

            # Coach -> Fitness
            fit = await _get(client, f"{FITNESS_URL}/plan")
            coach = _BY_STATION["fitness"]
            tp = fit.get("today_plan") or {}
            coach_summary = f"{tp.get('focus', 'rest')} day" if tp else "rest day"
            await _log(conn, coach["name"], "fitness", "checked plan", coach_summary)
            jobs.append({"agent": coach["id"], "name": coach["name"], "station": "fitness",
                         "summary": coach_summary})

            # Penny -> Finance
            fin = await _get(client, f"{FINANCE_URL}/summary")
            penny = _BY_STATION["finance"]
            due = fin.get("upcoming", [])
            penny_summary = (
                f"{len(due)} bill(s) due soon" if due
                else f"${fin.get('monthly_total', 0)}/mo tracked"
            )
            await _log(conn, penny["name"], "finance", "checked bills", penny_summary)
            jobs.append({"agent": penny["id"], "name": penny["name"], "station": "finance",
                         "summary": penny_summary})
            for b in due:
                await _add_deadline(conn, f"{b['name']} bill (${b['amount']})", None,
                                    "finance", f"bill:{b['id']}")

        STATE["items"] = await build_briefing()
        STATE["summary"] = f"{len(STATE['items'])} things on your plate."
        note = f"Synced — {STATE['summary']} ({booked} booked, {len(mails)} to reply)."
        await _remember(conn, note)

    return {"synced": True, "events_created": created, "jobs": jobs, "briefing": STATE}


@app.get("/")
async def root():
    return {"app": "Assistant", "endpoints": ["/briefing", "/sync", "/agents", "/space", "/health"]}
