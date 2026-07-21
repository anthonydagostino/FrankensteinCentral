import asyncio
import os
from datetime import datetime

import httpx
from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import notify
from .orchestrator import extract_datetime

app = FastAPI(title="Assistant Service")

POWERBUY_URL = os.environ.get("POWERBUY_URL", "http://powerbuy:8000")
FITNESS_URL = os.environ.get("FITNESS_URL", "http://fitness:8000")
GMAIL_URL = os.environ.get("GMAIL_URL", "http://gmail:8000")
SCHEDULE_URL = os.environ.get("SCHEDULE_URL", "http://schedule:8000")
FINANCE_URL = os.environ.get("FINANCE_URL", "http://finance:8000")
TASKS_URL = os.environ.get("TASKS_URL", "http://tasks:8000")
BUDGET_URL = os.environ.get("BUDGET_URL", "http://budget:8000")
DEALS_URL = os.environ.get("DEALS_URL", "http://deals:8000")
NETWORTH_URL = os.environ.get("NETWORTH_URL", "http://networth:8000")
AUTO_SYNC_SECONDS = int(os.environ.get("AUTO_SYNC_SECONDS", "0"))
# Text a digest automatically after each sync (only when it changed). Off by default.
NOTIFY_ON_SYNC = os.environ.get("NOTIFY_ON_SYNC", "false").lower() in ("1", "true", "yes")
# Two-way Telegram: if both are set, Bones answers questions you text it.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
     "color": "#ff8a5b", "blurb": "Arbitrage desk — profit, unpaid, expiring."},
    {"id": "coach", "name": "Coach", "role": "worker", "station": "fitness",
     "color": "#c58cff", "blurb": "Gym & food — today's plan and groceries."},
    {"id": "penny", "name": "Penny", "role": "worker", "station": "finance",
     "color": "#5bd6c0", "blurb": "Money desk — bills, subscriptions, what's due."},
    {"id": "tess", "name": "Tess", "role": "worker", "station": "tasks",
     "color": "#f2b8d0", "blurb": "To-do runner — tracks what's still open."},
    {"id": "buck", "name": "Buck", "role": "worker", "station": "budget",
     "color": "#f5c542", "blurb": "Budget desk — spending by category, what's left."},
    {"id": "scout", "name": "Scout", "role": "worker", "station": "deals",
     "color": "#a3e635", "blurb": "Deal hunter — flags real discounts from your inbox."},
    {"id": "wade", "name": "Wade", "role": "worker", "station": "networth",
     "color": "#38bdf8", "blurb": "Wealth desk — account balances, applies recurring contributions."},
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


async def _telegram_listen_loop():
    """Long-poll Telegram for messages from the owner and answer them with
    the same offline Q&A the dashboard's ask box uses.

    Long-polling (not a webhook) so nothing here needs to be reachable from
    the internet — this box just makes outbound calls to Telegram's API,
    same as sending a digest already does.
    """
    offset = 0
    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                r = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 25},
                )
                for u in r.json().get("result", []):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    text = msg.get("text")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not text or chat_id != TELEGRAM_CHAT_ID:
                        continue  # ignore anyone but the owner
                    answer = (await ask(text)).get("answer", "Not sure about that one.")
                    await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": answer},
                    )
            except Exception:  # noqa: BLE001 - never let the loop die
                await asyncio.sleep(5)


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
    if AUTO_SYNC_SECONDS > 0:
        asyncio.create_task(_auto_sync_loop())
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        asyncio.create_task(_telegram_listen_loop())


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
        tasks = await _get(client, f"{TASKS_URL}/summary")
        budget = await _get(client, f"{BUDGET_URL}/summary")
        deals = await _get(client, f"{DEALS_URL}/summary")
        networth = await _get(client, f"{NETWORTH_URL}/summary")

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

    if tasks.get("open"):
        top = ", ".join(tasks.get("top", [])) or "see list"
        items.append(
            {"source": "tasks", "message": f"{tasks['open']} open to-dos: {top}."}
        )

    over = budget.get("over_budget", [])
    if over:
        items.append(
            {"source": "budget", "message": f"Over budget on: {', '.join(over)}."}
        )
    elif budget.get("remaining") is not None:
        items.append(
            {"source": "budget", "message": f"${budget['remaining']} left in this month's budget."}
        )

    if deals.get("top"):
        items.append(
            {"source": "deals", "message": f"Deals spotted: {', '.join(deals['top'])}."}
        )

    if networth.get("total") is not None:
        items.append(
            {"source": "networth", "message": f"Net worth: ${networth['total']:,.2f}."}
        )

    return items


@app.get("/briefing")
async def briefing():
    return STATE


async def build_overview() -> dict:
    """A glanceable set of numbers across every app — the command-center row."""
    async with httpx.AsyncClient() as client:
        emails = await _get(client, f"{GMAIL_URL}/needs-reply")
        powerbuy = await _get(client, f"{POWERBUY_URL}/summary")
        cal = await _get(client, f"{SCHEDULE_URL}/events")
        fitness = await _get(client, f"{FITNESS_URL}/plan")
        finance = await _get(client, f"{FINANCE_URL}/summary")
        tasks = await _get(client, f"{TASKS_URL}/summary")
        budget = await _get(client, f"{BUDGET_URL}/summary")
        deals = await _get(client, f"{DEALS_URL}/summary")
        networth = await _get(client, f"{NETWORTH_URL}/summary")
    pb = powerbuy.get("summary", {})
    tp = fitness.get("today_plan") or {}
    events = cal.get("events", [])
    return {
        "emails_to_reply": len(emails.get("emails", [])),
        "expected_profit": pb.get("expected_profit", 0),
        "unpaid": pb.get("unpaid_count", 0),
        "bills_due": len(finance.get("upcoming", [])),
        "monthly_bills": finance.get("monthly_total", 0),
        "open_tasks": tasks.get("open", 0),
        "budget_left": budget.get("remaining", 0),
        "budget_over": len(budget.get("over_budget", [])),
        "deals_count": deals.get("count", 0),
        "net_worth": networth.get("total", 0),
        "next_event": events[0]["title"] if events else None,
        "next_event_at": events[0]["starts_at"] if events else None,
        "today_focus": tp.get("focus"),
        "synced_at": STATE.get("synced_at"),
    }


@app.get("/overview")
async def overview():
    return await build_overview()


async def compose_digest() -> str:
    """A short, texty summary from Bones — the message the manager sends you."""
    o = await build_overview()
    bits = []
    if o.get("emails_to_reply"):
        bits.append(f"{o['emails_to_reply']} emails to reply")
    if o.get("open_tasks"):
        bits.append(f"{o['open_tasks']} open tasks")
    if o.get("bills_due"):
        bits.append(f"{o['bills_due']} bills due soon")
    if o.get("budget_over"):
        bits.append("over budget")
    if o.get("unpaid"):
        bits.append(f"{o['unpaid']} unpaid buys")
    if o.get("deals_count"):
        bits.append(f"{o['deals_count']} deals spotted")
    head = "; ".join(bits) if bits else "you're all clear"
    tail = f" Next up: {o['next_event']}." if o.get("next_event") else ""
    focus = o.get("today_focus") or "rest"
    worth = f" Net worth: ${o['net_worth']:,.2f}." if o.get("net_worth") is not None else ""
    return f"🦴 Bones here — {head}. Today is {focus} day.{tail}{worth}"


@app.post("/notify")
async def notify_now(text: str | None = None):
    """Have Bones text you now (custom text, or the current digest)."""
    msg = text or await compose_digest()
    result = await notify.send(msg)
    return {"message": msg, **result}


def _sender_name(addr: str) -> str:
    """'Yeji Jong <yeji.jong@meetelise.com>' -> 'Yeji Jong'. Falls back to the
    address itself (or its domain) when there's no display name to show."""
    name = addr.split("<")[0].strip().strip('"')
    return name or addr


def _short(text: str, limit: int = 48) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@app.get("/ask")
async def ask(q: str = ""):
    """A lightweight, offline Q&A over your apps — intent-matched, no LLM needed."""
    ql = q.lower().strip()
    async with httpx.AsyncClient() as client:
        emails = await _get(client, f"{GMAIL_URL}/needs-reply")
        tasks = await _get(client, f"{TASKS_URL}/summary")
        finance = await _get(client, f"{FINANCE_URL}/summary")
        fitness = await _get(client, f"{FITNESS_URL}/plan")
        pb = await _get(client, f"{POWERBUY_URL}/summary")
        cal = await _get(client, f"{SCHEDULE_URL}/events")
        budget = await _get(client, f"{BUDGET_URL}/summary")
        deals = await _get(client, f"{DEALS_URL}/summary")
        networth = await _get(client, f"{NETWORTH_URL}/summary")

    def has(*words):
        return any(w in ql for w in words)

    if has("budget", "spending", "spent", "over budget", "left to spend"):
        over = budget.get("over_budget", [])
        base = (f"You've spent ${budget.get('total_spent', 0)} of "
                f"${budget.get('total_budget', 0)} ({budget.get('percent_used', 0)}%), "
                f"${budget.get('remaining', 0)} left.")
        if over:
            base += f" Over budget on: {', '.join(over)}."
        return {"answer": base}

    if has("email", "reply", "inbox", "mail"):
        ems = emails.get("emails", [])
        if not ems:
            return {"answer": "Inbox's clear — nothing needs a reply."}
        shown = ems[:5]
        lines = [f"{len(ems)} email(s) need a reply:"]
        lines += [f"• {_short(e['subject'])} — {_sender_name(e['from'])}" for e in shown]
        if len(ems) > len(shown):
            lines.append(f"…and {len(ems) - len(shown)} more.")
        return {"answer": "\n".join(lines)}

    if has("task", "todo", "to-do", "to do"):
        top = ", ".join(tasks.get("top", []))
        n = tasks.get("open", 0)
        return {"answer": f"You have {n} open task(s)" + (f": {top}." if top else ".")}

    if has("bill", "due", "subscription", "finance", "money", "spend", "budget"):
        up = finance.get("upcoming", [])
        if not up:
            return {"answer": f"No bills due in the next week. You spend "
                              f"${finance.get('monthly_total', 0)}/month total."}
        names = ", ".join(f"{b['name']} (${b['amount']}, in {b['days_until']}d)" for b in up)
        return {"answer": f"Coming up: {names}."}

    if has("profit", "powerbuy", "arbitrage", "purchase", "unpaid"):
        s = pb.get("summary", {})
        return {"answer": f"Expected profit is ${s.get('expected_profit', 0)}, with "
                          f"{s.get('unpaid_count', 0)} unpaid and "
                          f"{s.get('expiring_soon_count', 0)} expiring soon."}

    if has("workout", "gym", "lift", "train", "exercise", "today"):
        tp = fitness.get("today_plan") or {}
        lifts = ", ".join(tp.get("lifts", [])) or "recovery"
        return {"answer": f"Today is {tp.get('focus', 'rest')} day: {lifts}."}

    if has("schedule", "calendar", "next", "event", "interview", "meeting", "coming up"):
        events = cal.get("events", [])
        if not events:
            return {"answer": "Nothing on your calendar yet."}
        nxt = events[0]
        return {"answer": f"Next up: {nxt['title']} at {nxt['starts_at']}."}

    if has("deal", "discount", "coupon", "promo", "sale", "offer"):
        top = deals.get("top", [])
        if not top:
            return {"answer": "No real deals spotted in your inbox lately."}
        return {"answer": f"Deals spotted: {'; '.join(top)}."}

    if has("net worth", "worth", "balance", "chase", "marcus", "robinhood", "fidelity", "tsp", "savings"):
        accts = networth.get("accounts", [])
        if not accts:
            return {"answer": "No accounts set up yet."}
        lines = [f"Net worth: ${networth.get('total', 0):,.2f}"]
        lines += [f"• {a['name']}: ${a['balance']:,.2f}" for a in accts]
        return {"answer": "\n".join(lines)}

    # default: a full rundown
    ov = await build_overview()
    parts = []
    if ov["emails_to_reply"]:
        parts.append(f"{ov['emails_to_reply']} emails to reply")
    if ov["open_tasks"]:
        parts.append(f"{ov['open_tasks']} open tasks")
    if ov["bills_due"]:
        parts.append(f"{ov['bills_due']} bills due soon")
    if ov["unpaid"]:
        parts.append(f"{ov['unpaid']} unpaid buys")
    if ov.get("deals_count"):
        parts.append(f"{ov['deals_count']} deals spotted")
    rundown = "; ".join(parts) if parts else "nothing urgent"
    nxt = f" Next up: {ov['next_event']}." if ov["next_event"] else ""
    return {"answer": f"Here's your plate: {rundown}. Today is "
                      f"{ov.get('today_focus') or 'rest'} day.{nxt}"}


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

            # Tess -> Tasks (turn deadline emails into real, checkable to-dos)
            tess = _BY_STATION["tasks"]
            new_tasks = 0
            for e in mails:
                if e.get("category") != "deadline":
                    continue
                resp = await client.post(
                    f"{TASKS_URL}/tasks",
                    json={"title": e["subject"], "external_id": f"mail:{e['id']}"},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    new_tasks += 1
            tsk = await _get(client, f"{TASKS_URL}/summary")
            tess_summary = f"{tsk.get('open', 0)} open" if tsk else "no tasks"
            if new_tasks:
                tess_summary += f" ({new_tasks} new from email)"
            await _log(conn, tess["name"], "tasks", "reviewed to-dos", tess_summary)
            jobs.append({"agent": tess["id"], "name": tess["name"], "station": "tasks",
                         "summary": tess_summary})

            # Buck -> Budget
            bud = await _get(client, f"{BUDGET_URL}/summary")
            buck = _BY_STATION["budget"]
            over = bud.get("over_budget", [])
            buck_summary = (
                f"over on {', '.join(over)}" if over
                else f"${bud.get('remaining', 0)} left this month"
            )
            await _log(conn, buck["name"], "budget", "checked budget", buck_summary)
            jobs.append({"agent": buck["id"], "name": buck["name"], "station": "budget",
                         "summary": buck_summary})

            # Scout -> Deals (real discounts Posty spotted while triaging)
            gmail_deals = await _get(client, f"{GMAIL_URL}/deals")
            spotted = gmail_deals.get("deals", [])
            scout = _BY_STATION["deals"]
            new_deals = 0
            for d in spotted:
                resp = await client.post(
                    f"{DEALS_URL}/deals",
                    json={"merchant": d.get("merchant") or d.get("from", "Unknown"),
                          "offer": d.get("offer") or d.get("subject", ""),
                          "source": "gmail", "external_id": d["id"]},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    new_deals += 1
            scout_summary = f"{new_deals} new deal(s)" if new_deals else "no new deals"
            await _log(conn, scout["name"], "deals", "scanned for discounts", scout_summary)
            jobs.append({"agent": scout["id"], "name": scout["name"], "station": "deals",
                         "summary": scout_summary})

            # Wade -> Net Worth (apply any recurring contributions that came due)
            wade = _BY_STATION["networth"]
            resp = await client.post(f"{NETWORTH_URL}/recurring/apply", timeout=8)
            applied = resp.json().get("applied", []) if resp.status_code < 300 else []
            if applied:
                total_in = sum(a["amount"] for a in applied)
                wade_summary = f"applied ${total_in:,.0f} across {len(applied)} contribution(s)"
            else:
                nw = await _get(client, f"{NETWORTH_URL}/summary")
                wade_summary = f"${nw.get('total', 0):,.0f} total — nothing due"
            await _log(conn, wade["name"], "networth", "checked contributions", wade_summary)
            for a in applied:
                await _remember(conn, f"💰 +${a['amount']:,.0f} contributed to {a['account']} "
                                       f"(now ${a['new_balance']:,.0f}).")
            jobs.append({"agent": wade["id"], "name": wade["name"], "station": "networth",
                         "summary": wade_summary})

        STATE["items"] = await build_briefing()
        STATE["summary"] = f"{len(STATE['items'])} things on your plate."
        STATE["synced_at"] = datetime.utcnow().isoformat()
        note = f"Synced — {STATE['summary']} ({booked} booked, {len(mails)} to reply)."
        await _remember(conn, note)

    # Optionally have Bones text a digest — but only when it actually changed,
    # so auto-sync doesn't spam the same message.
    notified = False
    if NOTIFY_ON_SYNC and notify.configured():
        digest = await compose_digest()
        if digest != STATE.get("last_digest"):
            result = await notify.send(digest)
            if result.get("sent"):
                STATE["last_digest"] = digest
                notified = True

    return {"synced": True, "events_created": created, "jobs": jobs,
            "briefing": STATE, "notified": notified}


@app.get("/")
async def root():
    return {"app": "Assistant", "endpoints": ["/briefing", "/sync", "/agents", "/space", "/health"]}
