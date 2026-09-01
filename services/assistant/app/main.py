import asyncio
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8000")
PLEX_SVC_URL = os.environ.get("PLEX_SVC_URL", "http://plex:8000")
FIREFLY_SVC_URL = os.environ.get("FIREFLY_URL_SVC", "http://firefly:8000")
CORE_URL = os.environ.get("CORE_URL", "http://core:8000")
STOCKS_URL = os.environ.get("STOCKS_URL", "http://stocks:8000")
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))
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
    {"id": "vic", "name": "Vic", "role": "worker", "station": "vault",
     "color": "#8b98a9", "blurb": "Vault guard — password health: weak, reused, no-2FA."},
    {"id": "milo", "name": "Milo", "role": "worker", "station": "plex",
     "color": "#e5a00d", "blurb": "Media runner — continue watching and what's newly added on Plex."},
    {"id": "fitz", "name": "Fitz", "role": "worker", "station": "firefly",
     "color": "#e0592a", "blurb": "Ledger keeper — Firefly III net worth, spend, transactions."},
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
-- One row per gmail scheduling thread (a "I'm available X" email you sent
-- and whatever happened after). `signature` fingerprints status + all the
-- slots involved, so a sync where nothing about the thread changed is a
-- total no-op: no duplicate schedule events, no duplicate notable/digest
-- lines, no wasted Google Calendar API calls.
CREATE TABLE IF NOT EXISTS thread_state (
    thread_id TEXT PRIMARY KEY, signature TEXT NOT NULL, status TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    """What Bones' Desk shows — things that actually need your attention.

    Deliberately NOT a status dump: routine numbers (today's workout, next
    event, net worth, "budget's fine") already live on the overview tiles,
    so repeating them here is just noise. Only alerts and things with an
    action attached to them (reply, pay, review) make the cut.
    """
    items: list[dict] = []
    async with httpx.AsyncClient() as client:
        emails = await _get(client, f"{GMAIL_URL}/needs-reply")
        powerbuy = await _get(client, f"{POWERBUY_URL}/summary")
        finance = await _get(client, f"{FINANCE_URL}/summary")
        budget = await _get(client, f"{BUDGET_URL}/summary")
        deals = await _get(client, f"{DEALS_URL}/summary")
        vault = await _get(client, f"{VAULT_URL}/summary")
        availability = await _get(client, f"{GMAIL_URL}/thread-availability")

    for e in emails.get("emails", [])[:5]:
        verb = "Interview" if e.get("category") == "interview" else "Reply needed"
        items.append(
            {"source": "gmail", "message": f"{verb}: {_short(e['subject'])} — {_sender_name(e['from'])}"}
        )
    extra_emails = len(emails.get("emails", [])) - 5
    if extra_emails > 0:
        items.append({"source": "gmail", "message": f"+{extra_emails} more email(s) need a reply."})

    s = powerbuy.get("summary")
    if s:
        alerts = []
        if s.get("unpaid_count"):
            alerts.append(f"{s['unpaid_count']} unpaid")
        if s.get("expiring_soon_count"):
            alerts.append(f"{s['expiring_soon_count']} expiring soon")
        if s.get("not_delivered_count"):
            alerts.append(f"{s['not_delivered_count']} not delivered")
        if alerts:  # only worth a line when something actually needs a look
            items.append(
                {"source": "powerbuy", "message": f"PowerBuy needs a look: {'; '.join(alerts)}."}
            )

    due = finance.get("upcoming", [])
    if due:
        names = ", ".join(f"{b['name']} (${b['amount']})" for b in due[:3])
        items.append(
            {"source": "finance", "message": f"Bills due soon: {names}."}
        )

    over = budget.get("over_budget", [])
    if over:
        items.append(
            {"source": "budget", "message": f"Over budget on: {', '.join(over)}."}
        )

    if deals.get("top"):
        items.append(
            {"source": "deals", "message": f"Deals spotted: {', '.join(deals['top'])}."}
        )

    # Reused passwords are the actionable one — flag them (weak/old live on the tile).
    if vault.get("reused"):
        items.append(
            {"source": "vault", "message": f"{vault['reused']} reused password(s) — rotate them."}
        )

    threads = availability.get("threads", [])
    awaiting = [t for t in threads if t.get("status") == "pending"]
    countered = [t for t in threads if t.get("status") == "countered"]
    if countered:  # the ball's in your court — surface these above plain "waiting"
        names = ", ".join(_sender_name(t.get("counterparty", "")) for t in countered[:3])
        items.append(
            {"source": "schedule", "message": f"{len(countered)} countered your availability, "
                                               f"needs a reply: {names}."}
        )
    if awaiting:
        names = ", ".join(_sender_name(t.get("counterparty", "")) for t in awaiting[:3])
        items.append(
            {"source": "schedule", "message": f"Awaiting reply on {len(awaiting)} proposed "
                                               f"time(s): {names}."}
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
        vault = await _get(client, f"{VAULT_URL}/summary")
    pb = powerbuy.get("summary", {})
    tp = fitness.get("today_plan") or {}
    # Only a *confirmed* event counts as "next up" — a still-pending proposal
    # you sent isn't a real commitment yet, so it shouldn't read as one.
    events = [e for e in cal.get("events", []) if e.get("status", "confirmed") == "confirmed"]
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
        "vault_score": vault.get("score"),
        "vault_reused": vault.get("reused", 0),
        "next_event": events[0]["title"] if events else None,
        "next_event_at": events[0]["starts_at"] if events else None,
        "today_focus": tp.get("focus"),
        "synced_at": STATE.get("synced_at"),
    }


@app.get("/overview")
async def overview():
    return await build_overview()


# ============================================================================
# /home — the single aggregated payload the Today command center renders from.
# One fast call: fan out concurrently to every service + core + stocks, then
# assemble greeting/mode, briefing line, unified Needs Attention feed, Do This
# Next (explainable rules), Money, Portfolio, Health, Score, Big 3, captures.
# Any one service failing contributes nothing rather than breaking the page.
# ============================================================================

_HOME_CACHE: dict = {"at": None, "data": None}
_HOME_TTL = 30  # seconds


def _home_time(settings: dict) -> dict:
    now = datetime.now(LOCAL_TZ)
    h = now.hour
    if h < 12:
        greeting = "Good morning"
    elif h < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    morning_end = settings.get("morning_end_hour", 12)
    evening_start = settings.get("evening_start_hour", 18)
    mode = "morning" if h < morning_end else ("evening" if h >= evening_start else "day")
    return {
        "now": now.isoformat(),
        "greeting": greeting,
        "mode": mode,
        "date_label": now.strftime("%A, %B %-d"),
        "hour": h,
    }


def _event_minutes_until(events, now) -> tuple | None:
    """(minutes_until, event) for the next confirmed event, if parseable."""
    for e in events:
        raw = e.get("starts_at") or e.get("start")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        mins = (dt - now).total_seconds() / 60
        return (mins, e)
    return None


def _attention(core, availability, finance, budget, firefly, spending,
               stocks, vault, settings, down) -> list[dict]:
    """Cross-domain items that need you — email lives in its own Inbox card now,
    so this is money / stocks / habits / infra only."""
    items: list[dict] = []

    for b in (finance.get("upcoming", []) if finance else [])[:4]:
        du = b.get("days_until")
        sev = "important" if (du is not None and du <= 3) else "fyi"
        when = f"in {du}d" if du is not None else "soon"
        items.append({
            "id": f"bill:{b.get('name')}", "severity": sev, "icon": "💳",
            "title": f"{b.get('name')} due {when}", "detail": f"${b.get('amount')}",
            "action": {"type": "open", "app": "finance"},
        })

    for w in (budget.get("warnings", []) if budget else [])[:2]:
        items.append({
            "id": f"budget:{w.get('budget')}", "severity": w.get("severity", "fyi"),
            "icon": "🫙", "title": f"{w.get('budget')} budget: {w.get('state')}",
            "detail": _short(w.get("text", ""), 90),
            "action": {"type": "open", "app": "budget"},
        })

    thr = (settings.get("finance", {}) or {}).get("large_txn", 200)
    big = (spending or {}).get("biggest_today")
    if big and big.get("amount", 0) >= thr:
        items.append({
            "id": f"txn:{big.get('desc')}", "severity": "fyi", "icon": "💸",
            "title": f"Large charge: {_short(big.get('desc',''), 30)}",
            "detail": f"${big['amount']:,.0f} today",
            "action": {"type": "open", "app": "firefly"},
        })

    move_thr = (settings.get("market", {}) or {}).get("move_threshold_pct", 3)
    for p in (stocks.get("positions", []) if stocks else []):
        pct = p.get("change_pct")
        if pct is not None and abs(pct) >= move_thr:
            arrow = "▲" if pct > 0 else "▼"
            items.append({
                "id": f"stock:{p['symbol']}", "severity": "fyi", "icon": "📈",
                "title": f"{p['symbol']} {arrow} {abs(pct):.1f}% today",
                "detail": f"${p.get('day_change',0):+,.0f} to your portfolio",
                "action": {"type": "open", "app": "stocks"},
            })

    for n in (core.get("nudges", []) if core else []):
        items.append({
            "id": f"nudge:{n['key']}", "severity": n.get("severity", "fyi"),
            "icon": n.get("icon", "•"), "title": n.get("title", ""),
            "detail": n.get("detail", ""), "action": n.get("action"),
        })

    if vault and vault.get("reused"):
        items.append({
            "id": "vault:reused", "severity": "fyi", "icon": "🔐",
            "title": f"{vault['reused']} reused password(s)", "detail": "rotate them",
            "action": {"type": "open", "app": "vault"},
        })

    for name in down:
        items.append({
            "id": f"sys:{name}", "severity": "important", "icon": "⚠️",
            "title": f"{name} is unavailable", "detail": "check the homelab",
            "action": {"type": "open", "app": "assistant"},
        })

    order = {"important": 0, "fyi": 1}
    items.sort(key=lambda x: order.get(x["severity"], 2))
    return items


def _study_pace_behind(study, hour) -> int:
    """Minutes behind a linear 8am→10pm pace toward today's goal (0 if ahead)."""
    goal = study.get("goal_min", 0)
    done = study.get("today_min", 0)
    if not goal:
        return 0
    frac = max(0.0, min(1.0, (hour - 8) / 14))
    expected = goal * frac
    return int(max(0, expected - done))


def _do_next(core, inbox, money, events, settings, t) -> dict:
    """ONE explainable recommendation, considering time, email, calendar,
    study pace, gym, hydration and the Big 3. First matching rule wins."""
    now = datetime.now(LOCAL_TZ)
    hour = t["hour"]
    study = (core or {}).get("study", {})
    gym = (core or {}).get("gym", {})
    water = (core or {}).get("water", {})
    big3 = (core or {}).get("big3", [])

    ev = _event_minutes_until(events, now)

    # 1) A calendar event that's imminent beats everything
    if ev and 0 <= ev[0] <= 30:
        return {"title": f"Head to {_short(ev[1].get('title',''), 40)}",
                "reason": f"It starts in {int(ev[0])} min.",
                "action": {"type": "open", "app": "schedule"}}

    # 2) An important email that needs a response
    needs = [i for i in inbox.get("items", []) if i.get("needs_reply") and i.get("important")]
    if needs:
        e = sorted(needs, key=lambda x: x.get("age_hours") or 0, reverse=True)[0]
        aged = f" — it's been sitting {e['age'].replace(' ago', '')}" if e.get("age") else ""
        return {"title": f"Reply to {e['from']}",
                "reason": f"\"{e['subject']}\" looks like it needs a response{aged}.",
                "action": {"type": "gmail"}}

    # 3) A calendar event starting soon
    if ev and 0 <= ev[0] <= 75:
        return {"title": f"Get ready for {_short(ev[1].get('title',''), 40)}",
                "reason": f"It starts in {int(ev[0])} min.",
                "action": {"type": "open", "app": "schedule"}}

    # 3) Study, if you're behind pace
    behind = _study_pace_behind(study, hour)
    goal = study.get("goal_min", 0); done = study.get("today_min", 0)
    if goal and done < goal and (behind >= 15 or hour >= 18):
        rem = goal - done
        mins = min(60, rem) or 25
        why = (f"you're {behind} min behind today's pace" if behind >= 15
               else f"{done//60}h {done%60}m / {goal//60}h done and the day's almost over")
        return {"title": f"Start a {mins}-minute study session",
                "reason": f"{why}.", "action": {"type": "focus", "minutes": mins}}

    # 4) Gym, if the week is slipping
    if gym.get("available") and gym.get("week", 0) < gym.get("goal", 0):
        rem = gym["goal"] - gym["week"]
        days_left = 7 - now.weekday()
        if rem >= days_left or hour >= settings.get("evening_start_hour", 18):
            return {"title": "Go to the gym",
                    "reason": f"{gym['week']}/{gym['goal']} workouts this week — {rem} to go and {days_left} day(s) left.",
                    "action": {"type": "gym"}}

    # 5) A Big 3 item
    undone = [b for b in big3 if not b.get("done")]
    if undone and hour >= 10:
        return {"title": f"Knock out: {_short(undone[0]['text'], 40)}",
                "reason": "It's one of your Big 3 for today.",
                "action": {"type": "big3", "id": undone[0]["id"]}}

    # 6) Hydration, late in the day
    if water.get("goal") and water.get("oz", 0) < water["goal"] and hour >= 16:
        gap = water["goal"] - water["oz"]
        return {"title": "Drink some water",
                "reason": f"{gap} oz short of your {water['goal']} oz goal.",
                "action": {"type": "water", "oz": 16}}

    return {"title": "You're on track", "reason": "Nothing urgent right now — nice.",
            "action": None}


def _briefing_line(core, inbox, stocks, money, events) -> list[str]:
    out: list[str] = []
    need = inbox.get("need_reply", 0)
    imp = sum(1 for i in inbox.get("items", []) if i.get("important"))
    if need:
        out.append(f"{need} email(s) need a reply" + (f" ({imp} important)" if imp else ""))
    elif inbox.get("mode") == "live":
        out.append("Inbox looks clear")
    if stocks and stocks.get("configured"):
        pct = stocks.get("day_change_pct", 0)
        out.append(f"Portfolio {'up' if pct >= 0 else 'down'} {abs(pct):.1f}% today")
    if money.get("today") is not None:
        out.append(f"${money['today']:,.0f} spent today")
    gym = (core or {}).get("gym", {})
    if gym.get("available"):
        out.append(f"Gym {gym.get('week',0)}/{gym.get('goal',0)} this week")
    study = (core or {}).get("study", {})
    if study.get("goal_min"):
        d = study.get("today_min", 0)
        out.append(f"{d//60}h {d%60}m / {study['goal_min']//60}h studying")
    water = (core or {}).get("water", {})
    if water.get("goal"):
        out.append(f"{water.get('oz',0)}/{water['goal']} oz water")
    return out[:6]


def _age_str(hours) -> str:
    if hours is None:
        return ""
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(round(hours))}h ago"
    return f"{int(hours // 24)}d ago"


def _is_important_sender(addr: str, important: list) -> bool:
    a = (addr or "").lower()
    return any(s and s.lower() in a for s in (important or []))


def _inbox(gmail_emails, availability, settings, gmail_mode) -> dict:
    """The email signal: which messages actually matter, with sender/subject/
    age — not a count of 40 unread."""
    important = settings.get("important_senders", [])
    tier = {"interview": 50, "deadline": 40, "personal": 20, "finance": 12,
            "notification": 4, "deal": 5, "fyi": 0}
    scored = []
    for e in gmail_emails:
        rank = tier.get(e.get("category"), 0)
        if _is_important_sender(e.get("from", ""), important):
            rank += 100
        if e.get("needs_reply"):
            rank += 15
        age = e.get("age_hours")
        if age is not None:
            rank += min(20, age / 6)
        scored.append((rank, e))
    scored.sort(key=lambda x: x[0], reverse=True)

    items = []
    for rank, e in scored[:5]:
        age = e.get("age_hours")
        items.append({
            "id": e.get("id"), "thread_id": e.get("thread_id"),
            "from": _sender_name(e.get("from", "")),
            "subject": _short(e.get("subject", ""), 60),
            "snippet": _short(e.get("snippet", ""), 90),
            "category": e.get("category"),
            "age": _age_str(age), "age_hours": age,
            "needs_reply": e.get("needs_reply", False),
            "important": _is_important_sender(e.get("from", ""), important)
            or e.get("category") in ("interview", "deadline"),
            "stale": age is not None and age >= 48 and e.get("needs_reply"),
        })

    replies = []
    for t in (availability.get("threads", []) if availability else []):
        if t.get("status") == "countered":
            replies.append({"counterparty": _sender_name(t.get("counterparty", "")),
                            "subject": _short(t.get("subject", ""), 50)})

    need = sum(1 for i in items if i["needs_reply"])
    if gmail_mode == "disconnected":
        empty = "Gmail not connected."
    elif gmail_mode == "error":
        empty = "Couldn't reach Gmail — token may need re-auth."
    elif not items:
        empty = "Inbox looks clear. 🎉"
    else:
        empty = None
    return {"items": items, "replies": replies, "need_reply": need,
            "total": len(gmail_emails), "empty": empty, "mode": gmail_mode}


def _money(firefly, spending, finance, budget, networth, settings) -> dict:
    connected = bool(firefly) and firefly.get("connected") is not False
    sp = spending if (spending and spending.get("connected")) else {}
    thr = (settings.get("finance", {}) or {}).get("large_txn", 200)

    def _m(key):
        v = (firefly or {}).get(key)
        return v if isinstance(v, dict) else {}

    # Zero vs unknown: a ledger that hasn't been updated can't tell you what
    # you spent today. Suppress current-period figures instead of implying $0.
    # Suppression keys off INGESTION recency (when data last entered the
    # ledger), not spending recency — a synced ledger with a quiet few days
    # legitimately shows $0 today. Falls back to activity if no ingest signal.
    ingest_days = sp.get("ingest_days")
    days_stale = ingest_days if ingest_days is not None else sp.get("days_stale")
    stale = days_stale is not None and days_stale >= 2
    # A brand-new month with nothing imported yet is unknown, not $0.
    month_ingested = sp.get("month_ingested")
    month_spend = None if month_ingested is False else sp.get("month")
    today_spend = None if stale else sp.get("today")
    week_spend = None if (days_stale is not None and days_stale >= 7) else sp.get("week")

    big = sp.get("biggest_today")
    unusual = big if (not stale and big and big.get("amount", 0) >= thr) else None

    obs = []
    if stale:
        obs.append(f"Financial data hasn't been imported for {days_stale} days — "
                   "today's spending is unknown, not zero.")
    pace = sp.get("pace_pct")
    baseline = sp.get("baseline")
    if pace is not None:
        if pace >= 15:
            obs.append(f"Spending is {pace}% ahead of last month's pace.")
        elif pace <= -15:
            obs.append(f"Spending is {abs(pace)}% below last month's pace — nice.")
    elif baseline in ("partial_history", "stale_data") and sp.get("pace_note"):
        # Label, don't manufacture: partial windows produce arithmetic, not insight.
        obs.append(f"Month-over-month comparison hidden — {sp['pace_note']}.")
    if unusual:
        obs.append(f"Unusual charge today: {unusual['desc']} ${unusual['amount']:,.0f}.")
    upcoming = (finance.get("upcoming", []) if finance else [])
    soon = [b for b in upcoming if (b.get("days_until") is None or b.get("days_until", 99) <= 7)]
    if soon:
        tot = sum(float(b.get("amount") or 0) for b in soon)
        obs.append(f"{len(soon)} bill(s) totaling ${tot:,.0f} due within a week.")
    cats = (firefly or {}).get("categories", []) or []
    if not obs and cats:
        top = max(cats, key=lambda c: c.get("amount", 0))
        obs.append(f"{top['name']} leads this month at ${top['amount']:,.0f}.")
    if connected and not obs:
        obs.append("No unusual spending detected.")

    return {
        "connected": connected,
        "today": today_spend, "week": week_spend, "month": month_spend,
        "month_ingested": month_ingested,
        "stale_days": days_stale if stale else None,
        "pace_pct": pace, "baseline": baseline, "daily_avg": sp.get("daily_avg"),
        "net_worth": (_m("net_worth").get("display")) or (
            f"${networth['total']:,.0f}" if networth.get("total") is not None else None),
        "left_to_spend": _m("left_to_spend").get("display"),
        "income_month": _m("earned").get("value"),
        "top_categories": sorted(cats, key=lambda c: c.get("amount", 0), reverse=True)[:4],
        "recent": (sp.get("recent") or (firefly or {}).get("recent", []))[:6],
        "upcoming_bills": soon[:4],
        "unusual": unusual,
        "observations": obs[:3],
    }


def _budget_brief(status) -> dict:
    """The homepage's budget signal: budget room, the single worst warning,
    and whether everything is on track — never the whole budget database."""
    if not status or not status.get("available"):
        return {"available": False, "configured": bool(status and status.get("configured"))}
    warns = status.get("warnings", [])
    counts = status.get("state_counts", {})
    freshness = status.get("freshness") or {}
    fresh = freshness.get("current_ok", False)
    return {
        "available": True,
        "configured": status.get("configured", False),
        "fresh": fresh,
        "paused_reason": freshness.get("paused_reason"),
        "importer_url": status.get("importer_url"),
        "budget_room": status.get("budget_room"),
        "budget_room_scope": status.get("budget_room_scope"),
        "worst": warns[0] if warns else None,
        "on_track": fresh and not warns and bool(status.get("budgets")),
        "budget_count": len(status.get("budgets", [])),
        "days_left": (status.get("month") or {}).get("days_left"),
        "uncat_flag": (status.get("uncategorized") or {}).get("low_confidence", False),
    }


async def build_home(fresh: bool = False) -> dict:
    cached = _HOME_CACHE
    if (not fresh and cached["data"] and cached["at"]
            and (datetime.now(LOCAL_TZ) - cached["at"]).total_seconds() < _HOME_TTL):
        return cached["data"]

    async with httpx.AsyncClient() as client:
        (settings, core, emails_r, avail, finance, budget, firefly, spending,
         networth, schedule, deals, stocks, vault, captures) = await asyncio.gather(
            _get(client, f"{CORE_URL}/settings"),
            _get(client, f"{CORE_URL}/today"),
            _get(client, f"{GMAIL_URL}/needs-reply"),
            _get(client, f"{GMAIL_URL}/thread-availability"),
            _get(client, f"{FINANCE_URL}/summary"),
            _get(client, f"{BUDGET_URL}/status"),
            _get(client, f"{FIREFLY_SVC_URL}/dashboard"),
            _get(client, f"{FIREFLY_SVC_URL}/spending"),
            _get(client, f"{NETWORTH_URL}/summary"),
            _get(client, f"{SCHEDULE_URL}/events"),
            _get(client, f"{DEALS_URL}/summary"),
            _get(client, f"{STOCKS_URL}/portfolio"),
            _get(client, f"{VAULT_URL}/summary"),
            _get(client, f"{CORE_URL}/captures"),
        )

    down = [name for name, payload in (("core", core), ("email", emails_r)) if not payload]
    gmail_emails = emails_r.get("emails", []) if emails_r else []
    gmail_mode = (emails_r or {}).get("mode", "disconnected")
    events = [e for e in (schedule.get("events", []) if schedule else [])
              if e.get("status", "confirmed") == "confirmed"]
    settings = settings or {}

    t = _home_time(settings)
    inbox = _inbox(gmail_emails, avail, settings, gmail_mode)
    money = _money(firefly, spending, finance, budget, networth, settings)

    data = {
        **t,
        "briefing": _briefing_line(core, inbox, stocks, money, events),
        "big3": (core or {}).get("big3", []),
        "do_next": _do_next(core, inbox, money, events, settings, t),
        "inbox": inbox,
        "attention": _attention(core, avail, finance, budget, firefly, spending,
                                stocks, vault, settings, down),
        "money": money,
        "budget": _budget_brief(budget),
        "portfolio": stocks or {"configured": False},
        "health": {
            "study": (core or {}).get("study", {}),
            "gym": (core or {}).get("gym", {}),
            "water": (core or {}).get("water", {}),
            "nutrition": (core or {}).get("nutrition", {}),
        },
        "score": (core or {}).get("score", {"score": 0, "parts": {}}),
        "captures": (captures.get("items", []) if captures else [])[:8],
        "next_event": events[0] if events else None,
        "systems": {"healthy": not down, "down": down},
        "last_updated": t["now"],
    }
    _HOME_CACHE["data"] = data
    _HOME_CACHE["at"] = datetime.now(LOCAL_TZ)
    return data


@app.get("/home")
async def home(fresh: int = 0):
    return await build_home(fresh=bool(fresh))


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
        availability = await _get(client, f"{GMAIL_URL}/thread-availability")

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

    if has("pending", "awaiting", "waiting on", "proposed", "did they reply", "confirm"):
        threads = availability.get("threads", [])
        pend = [t for t in threads if t.get("status") in ("pending", "countered")]
        if not pend:
            return {"answer": "Nothing awaiting a reply — every proposed time has been "
                              "confirmed or fell through."}
        lines = [f"{len(pend)} thread(s) awaiting a reply:"]
        for t in pend:
            who = _sender_name(t.get("counterparty", ""))
            tag = "they countered" if t.get("status") == "countered" else "awaiting them"
            lines.append(f"• {_short(t.get('subject', ''))} — {who} ({tag})")
        return {"answer": "\n".join(lines)}

    if has("schedule", "calendar", "next", "event", "interview", "meeting", "coming up"):
        events = [e for e in cal.get("events", []) if e.get("status", "confirmed") == "confirmed"]
        if not events:
            return {"answer": "Nothing confirmed on your calendar yet."}
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


async def _sync_availability_threads(conn, client: httpx.AsyncClient) -> tuple[int, list[str]]:
    """Threads where you proposed your own availability ("I'm available
    Monday at 2pm"). Turns them into pending/confirmed/countered calendar
    events and, once one slot is confirmed, clears the other proposed slots
    for that thread so the calendar never shows three tentative holds for a
    meeting that's already locked to one time.

    Skips any thread whose state hasn't changed since the last sync (see
    thread_state table) — that's what keeps this from re-creating the same
    pending event or re-announcing the same "still waiting" status forever.
    """
    avail = await _get(client, f"{GMAIL_URL}/thread-availability")
    threads = avail.get("threads", [])
    cur = await conn.execute("SELECT thread_id, signature FROM thread_state")
    prev = {tid: sig for tid, sig in await cur.fetchall()}

    lines: list[str] = []
    changed = 0
    for t in threads:
        tid = t["thread_id"]
        proposed = t.get("proposed_slots") or []
        countered = t.get("countered_slots") or []
        confirmed = t.get("confirmed_slot")
        status = t.get("status", "pending")
        signature = f"{status}|{','.join(proposed)}|{confirmed or ''}|{','.join(countered)}"
        if prev.get(tid) == signature:
            continue  # nothing new since last time — no-op, on purpose

        changed += 1
        subject = t.get("subject", "")
        who = _sender_name(t.get("counterparty", ""))
        base_ext = f"thread:{tid}"
        label = "Interview" if "interview" in subject.lower() else "Meeting"

        # Slot external_ids are keyed by the slot's own ISO time (not a plain
        # index) so a slot that survives across rounds (e.g. still offered
        # after a counter) maps to the same calendar event, while a slot
        # that's no longer offered has no id in `keep_ids` and gets cleaned
        # up below instead of lingering as a stale "pending" hold forever.
        keep_ids: list[str] = []
        if status in ("pending", "countered"):
            slots = countered if status == "countered" else proposed
            for slot in slots:
                ext = f"{base_ext}:slot:{slot}"
                keep_ids.append(ext)
                await client.post(
                    f"{SCHEDULE_URL}/events",
                    json={"title": f"{label} — {who}", "starts_at": slot, "source": "gmail",
                          "external_id": ext, "status": status, "thread_id": tid},
                    timeout=8,
                )
            if status == "pending":
                lines.append(f"⏳ Sent availability: {_short(subject)} — {len(slots)} time(s) "
                              f"proposed, awaiting {who}'s reply")
            else:
                lines.append(f"🔁 {who} countered: {_short(subject)} — new time(s) offered, your move")
        elif status == "confirmed" and confirmed:
            ext = f"{base_ext}:confirmed"
            keep_ids = [ext]
            await client.post(
                f"{SCHEDULE_URL}/events",
                json={"title": f"{label} — {who}", "starts_at": confirmed, "source": "gmail",
                      "external_id": ext, "status": "confirmed", "thread_id": tid},
                timeout=8,
            )
            lines.append(f"✅ Confirmed: {_short(subject)} — {who} — {confirmed}")
        elif status == "declined":
            lines.append(f"🚫 No time worked out: {_short(subject)} — {who}")

        # Whatever wasn't just (re)created above — a prior round's slots
        # that got superseded, or everything if this thread just declined —
        # gets declined and pulled off the calendar right now.
        await client.post(
            f"{SCHEDULE_URL}/events/resolve-thread",
            json={"thread_id": tid, "keep_external_ids": keep_ids}, timeout=8,
        )

        await conn.execute(
            """
            INSERT INTO thread_state (thread_id, signature, status, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (thread_id) DO UPDATE
                SET signature = %s, status = %s, updated_at = %s
            """,
            (tid, signature, status, _now(), signature, status, _now()),
        )
    return changed, lines


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
    # Things actually worth telling the user about this cycle — not "I
    # synced", but real new events. Bones' Notebook and the auto-text only
    # ever get entries from this list, never a routine heartbeat.
    notable: list[str] = []
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
                # Keyed by thread, not message id — a back-and-forth ("here's
                # a time" / "confirming that works") is multiple messages in
                # one thread, and should book ONE event, not one per message.
                thread_key = e.get("thread_id") or e["id"]
                ext_id = f"thread:{thread_key}:interview"
                resp = await client.post(
                    f"{SCHEDULE_URL}/events",
                    json={"title": f"Interview — {e['from']}", "starts_at": when,
                          "source": "gmail", "external_id": ext_id, "thread_id": thread_key},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    created.append(resp.json()["event"])
                    booked += 1
                    await _add_deadline(conn, f"Interview — {e['from']}", when,
                                        "schedule", f"evt:{thread_key}")
            # Cal -> Schedule (your own sent "I'm available..." proposals —
            # pending until they reply, confirmed/countered/declined after)
            thread_changes, thread_lines = await _sync_availability_threads(conn, client)
            notable.extend(thread_lines)

            # Cal -> Schedule (pull in anything added straight to Google
            # Calendar — e.g. from your phone — that this app didn't push
            # itself; the other direction of sync from the steps above)
            try:
                pull_resp = await client.post(f"{SCHEDULE_URL}/sync-from-calendar", timeout=15)
                pull = pull_resp.json() if pull_resp.status_code < 300 else {}
            except Exception:  # noqa: BLE001 - schedule unreachable, just skip this cycle
                pull = {}
            imported = pull.get("imported", 0)
            if imported:
                notable.append(f"📱 Pulled {imported} event(s) from Google Calendar")

            cal_summary = f"booked {booked} event(s)" if booked else "calendar up to date"
            if thread_changes:
                cal_summary += f"; {thread_changes} availability thread(s) updated"
            if imported:
                cal_summary += f"; {imported} pulled from Calendar"
            await _log(conn, cal_agent["name"], "schedule", "checked calendar", cal_summary)
            jobs.append({"agent": cal_agent["id"], "name": cal_agent["name"],
                         "station": "schedule", "summary": cal_summary})
            for ev in created:
                notable.append(f"📅 Booked: {_short(ev['title'])} — {ev['starts_at']}")

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
            new_task_titles = []
            for e in mails:
                if e.get("category") != "deadline":
                    continue
                resp = await client.post(
                    f"{TASKS_URL}/tasks",
                    json={"title": e["subject"], "external_id": f"mail:{e['id']}"},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    new_task_titles.append(e["subject"])
            tsk = await _get(client, f"{TASKS_URL}/summary")
            tess_summary = f"{tsk.get('open', 0)} open" if tsk else "no tasks"
            if new_task_titles:
                tess_summary += f" ({len(new_task_titles)} new from email)"
            await _log(conn, tess["name"], "tasks", "reviewed to-dos", tess_summary)
            jobs.append({"agent": tess["id"], "name": tess["name"], "station": "tasks",
                         "summary": tess_summary})
            for t in new_task_titles[:3]:
                notable.append(f"✅ New to-do: {_short(t)}")
            if len(new_task_titles) > 3:
                notable.append(f"✅ +{len(new_task_titles) - 3} more to-dos from email")

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
            new_deal_items = []
            for d in spotted:
                resp = await client.post(
                    f"{DEALS_URL}/deals",
                    json={"merchant": d.get("merchant") or d.get("from", "Unknown"),
                          "offer": d.get("offer") or d.get("subject", ""),
                          "source": "gmail", "external_id": d["id"]},
                    timeout=8,
                )
                if resp.status_code < 300 and resp.json().get("created"):
                    new_deal_items.append(d)
            scout_summary = f"{len(new_deal_items)} new deal(s)" if new_deal_items else "no new deals"
            await _log(conn, scout["name"], "deals", "scanned for discounts", scout_summary)
            jobs.append({"agent": scout["id"], "name": scout["name"], "station": "deals",
                         "summary": scout_summary})
            for d in new_deal_items[:3]:
                notable.append(f"🏷️ Deal: {d.get('merchant', '?')} — {d.get('offer', '?')}")
            if len(new_deal_items) > 3:
                notable.append(f"🏷️ +{len(new_deal_items) - 3} more deals")

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
                notable.append(f"💰 +${a['amount']:,.0f} to {a['account']} "
                                f"(now ${a['new_balance']:,.0f})")
            jobs.append({"agent": wade["id"], "name": wade["name"], "station": "networth",
                         "summary": wade_summary})

            # Vic -> Vault (password health). Standing state, not an event, so it
            # doesn't go on `notable` (no repeated texts about the same weak pws).
            vic = _BY_STATION["vault"]
            vh = await _get(client, f"{VAULT_URL}/summary")
            if vh:
                vic_summary = (
                    f"{vh.get('weak', 0)} weak, {vh.get('reused', 0)} reused "
                    f"(score {vh.get('score', 0)})"
                )
            else:
                vic_summary = "vault not connected"
            await _log(conn, vic["name"], "vault", "checked passwords", vic_summary)
            jobs.append({"agent": vic["id"], "name": vic["name"], "station": "vault",
                         "summary": vic_summary})

            # Milo -> Plex (shared media server)
            milo = _BY_STATION["plex"]
            px = await _get(client, f"{PLEX_SVC_URL}/summary")
            if px.get("connected"):
                milo_summary = f"{px.get('continue_count', 0)} to continue watching"
            elif px:
                milo_summary = "plex not connected"
            else:
                milo_summary = "media server offline"
            await _log(conn, milo["name"], "plex", "checked media", milo_summary)
            jobs.append({"agent": milo["id"], "name": milo["name"], "station": "plex",
                         "summary": milo_summary})

            # Fitz -> Firefly (personal finances)
            fitz = _BY_STATION["firefly"]
            ff = await _get(client, f"{FIREFLY_SVC_URL}/summary")
            nw = (ff.get("net_worth") or {}).get("display")
            fitz_summary = f"net worth {nw}" if nw else "no data"
            await _log(conn, fitz["name"], "firefly", "checked finances", fitz_summary)
            jobs.append({"agent": fitz["id"], "name": fitz["name"], "station": "firefly",
                         "summary": fitz_summary})

        STATE["items"] = await build_briefing()
        STATE["summary"] = f"{len(STATE['items'])} things on your plate."
        STATE["synced_at"] = datetime.utcnow().isoformat()
        # Only ever write real events to the notebook — no routine "I synced"
        # heartbeat. If nothing notable happened, Bones says nothing.
        for n in notable:
            await _remember(conn, n)

    # Only text you when something on `notable` is actually new — not on
    # every fluctuation (email counts, etc.) like it used to.
    notified = False
    if NOTIFY_ON_SYNC and notify.configured() and notable:
        digest = "🦴 Bones — " + "; ".join(notable)
        result = await notify.send(digest)
        notified = bool(result.get("sent"))

    return {"synced": True, "events_created": created, "jobs": jobs,
            "briefing": STATE, "notified": notified, "notable": notable}


@app.get("/")
async def root():
    return {"app": "Assistant", "endpoints": ["/briefing", "/sync", "/agents", "/space", "/health"]}
