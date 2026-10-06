"""Assistant service — builds the home screen, and books interviews.

Two jobs:

  * `GET /home` fans out to every service the home screen reads and assembles
    the one payload gateway/static/home.js renders: the week grid, the money
    card, the portfolio, the resale book, the Amex credits, the weather pill
    and the footer's deploy/backup state. Any one service failing contributes
    an honest "unreachable" rather than breaking the page.
  * `POST /sync` reads the inbox triage from the gmail service and keeps the
    calendar honest about it: books an interview the mail names a time for,
    tracks the availability threads you started, pulls anything added straight
    to Google Calendar, and retires the speculative holds an older version of
    this file used to push. Runs on AUTO_SYNC_SECONDS, or by hand.

Until 2026-10-06 this file also ran a cast of named "agents" narrating a
lounge view that had already been retired, a briefing/overview/digest trio
over services that no longer exist, an intent-matched /ask with a Telegram
listener on top, and the deadlines/memory/activity tables those wrote. All of
it fed home-screen cards Anthony asked to have removed, so it went with them.
The old tables are untouched in Postgres; nothing reads or writes them.
"""
import asyncio
import json
import os
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from . import notify, runway
from .dashboard import (amex_brief, card_debt, data_safety, deploy_state,
                        disk_state, firefly_state, import_state,
                        on_google_calendar, portfolio_alerts, portfolio_state,
                        resale_brief, schedule_state, upcoming_events,
                        weather_brief, week_window)
from .orchestrator import extract_datetime

app = FastAPI(title="Assistant Service")

POWERBUY_URL = os.environ.get("POWERBUY_URL", "http://powerbuy:8000")
GMAIL_URL = os.environ.get("GMAIL_URL", "http://gmail:8000")
SCHEDULE_URL = os.environ.get("SCHEDULE_URL", "http://schedule:8000")
AMEX_URL = os.environ.get("AMEX_URL", "http://amex:8000")
WEATHER_URL = os.environ.get("WEATHER_URL", "http://weather:8000")
BUDGET_URL = os.environ.get("BUDGET_URL", "http://budget:8000")
FIREFLY_SVC_URL = os.environ.get("FIREFLY_URL_SVC", "http://firefly:8000")
CORE_URL = os.environ.get("CORE_URL", "http://core:8000")
STOCKS_URL = os.environ.get("STOCKS_URL", "http://stocks:8000")
# The deploy record `deploy.sh` writes on the HOST, mounted read-only. It is
# deliberately outside the repo — `git reset --hard` during a deploy would
# erase anything tracked, and the point is to still be able to say what is
# running after a deploy that failed. Absent mount reads as "unknown".
DEPLOY_RECORD = os.environ.get("FRANKENSTEIN_DEPLOY_RECORD",
                               "/var/frankenstein/deployed.json")
# Written by scripts/backup.sh and scripts/restore.sh, in the same host state
# directory and through the same read-only mount. SCRUM-67.
DATA_SAFETY_RECORD = os.environ.get("FRANKENSTEIN_DATA_SAFETY_RECORD",
                                    "/var/frankenstein/data-safety.json")
# The mount itself. Disk free is read from it: statvfs through a bind mount
# reports the host filesystem, so this is the OptiPlex's data disk — but only
# while the directory is actually there (SCRUM-67).
STATE_DIR = os.path.dirname(DATA_SAFETY_RECORD) or "/var/frankenstein"
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))
AUTO_SYNC_SECONDS = int(os.environ.get("AUTO_SYNC_SECONDS", "0"))
# Text a digest automatically after each sync (only when it changed). Off by default.
NOTIFY_ON_SYNC = os.environ.get("NOTIFY_ON_SYNC", "false").lower() in ("1", "true", "yes")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

# One row per gmail scheduling thread (a "I'm available X" email you sent and
# whatever happened after). `signature` fingerprints status + all the slots
# involved, so a sync where nothing about the thread changed is a total no-op:
# no duplicate schedule events, no duplicate notable lines, no wasted Google
# Calendar API calls.
SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_state (
    thread_id TEXT PRIMARY KEY, signature TEXT NOT NULL, status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


async def _auto_sync_loop():
    """Keep the calendar in step with the inbox on its own, no browser needed."""
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


@app.get("/health")
async def health():
    return {"service": "assistant"}


async def _get(client: httpx.AsyncClient, url: str) -> dict:
    try:
        r = await client.get(url, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001 - a down sub-app just contributes nothing
        return {}


# ============================================================================
# /home — the single aggregated payload the home screen renders from.
# One fast call: fan out concurrently to every service the cards read, then
# assemble greeting/mode, the week, money, portfolio, resale, amex, weather and
# the footer's operator facts. Any one service failing contributes nothing
# rather than breaking the page.
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


def _upcoming_events(events, now, limit=6, statuses=None):
    return upcoming_events(events, now, LOCAL_TZ, limit=limit, statuses=statuses)


def _money(firefly, spending, budget, networth, settings) -> dict:
    """The money card's payload.

    `networth` is the firefly sub-app's /networth answer: every asset and
    liability account with its `kind` and `role`, which is what the runway
    needs to tell a debt from an asset and (as far as Firefly can) cash from
    a brokerage. It used to come through a separate networth service that
    proxied the same endpoint and fell back to a hand-typed balances table;
    that service went on 2026-10-06.
    """
    # Three states, not two. `_get` swallows a timeout and returns {}, so an
    # empty payload means the service could not be reached — which is NOT the
    # same as Firefly answering "I have no credentials". Rendering both as
    # "not connected — set FIREFLY_URL" sends you to fix configuration that is
    # already correct, every time a container blinks.
    state = firefly_state(firefly)
    connected = state == "ok"
    sp = spending if (spending and spending.get("connected")) else {}
    networth = networth if isinstance(networth, dict) else {}

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
    # The pay-cycle engine computes the same month with savings transfers
    # taken back out (moving $1,100 to Fidelity is not $1,100 spent), so its
    # number is the more accurate answer to "what did I spend". Fall back to
    # the raw withdrawal total when the pay cycle isn't configured.
    pay = (budget or {}).get("paycheck") or {}
    pay_month = pay.get("month") or {}
    month_label = pay_month.get("label")
    # Month completeness is carried INDEPENDENTLY of the paycheck. A truncated
    # window with no matching paycheck takes the unavailable brief path, which
    # drops every pay-cycle field — so if the headline relied on those, it
    # would quietly print a partial read as an exact month-to-date total.
    # Either source saying "truncated" makes it truncated; they read one ledger.
    month_complete = sp.get("window_complete", True) is not False
    if pay_month.get("window_complete") is False:
        month_complete = False
    if pay.get("configured") and pay_month.get("spent") is not None:
        month_spend = pay_month.get("spent")
    today_spend = None if stale else sp.get("today")
    week_spend = None if (days_stale is not None and days_stale >= 7) else sp.get("week")
    # Homepage headline: a trailing 30-day window, NOT the calendar month the
    # budgets use. It survives a stale ledger (it is a 30-day history, not a
    # same-day claim) but the UI states how far the data actually reaches.
    last_30 = sp.get("last_30")
    last_30_trend = sp.get("last_30_trend_pct")

    cats = (firefly or {}).get("categories", []) or []
    nw_total = networth.get("total")

    return {
        "connected": connected,
        "today": today_spend, "week": week_spend, "month": month_spend,
        "month_label": month_label,
        # False => `month` is at least this much, not exactly this much.
        "month_complete": month_complete,
        "month_savings": pay_month.get("savings"),
        "month_ingested": month_ingested,
        # What's left of the current paycheck after the savings that come out
        # of it — the homepage's "left to spend". Never a bank balance.
        "paycheck": _paycheck_brief(pay),
        # Cash runway: the one forward-looking money figure on the card.
        # Fed the trailing 30-day window it is already computing, and the
        # SAME completeness flag every other figure here respects — a
        # truncated read understates burn, which overstates runway.
        "runway": runway.cash_runway(
            networth.get("accounts") or [],
            last_30, 30,
            freshness={"window_complete": month_complete,
                       "ingest_days": days_stale},
            # Which accounts are not cash. Firefly has no field that can say
            # this — see runway.py — so it is a setting, and an unset setting
            # is reported as a range rather than guessed at.
            not_spendable=(settings.get("finance", {}) or {}).get("not_spendable"),
        ),
        "last_30": last_30,
        "last_30_trend_pct": last_30_trend,
        "last_30_note": sp.get("last_30_note"),
        "last_30_through": sp.get("last_30_through"),
        "last_30_window": sp.get("last_30_window"),
        "stale_days": days_stale if stale else None,
        "pace_pct": sp.get("pace_pct"), "baseline": sp.get("baseline"),
        "daily_avg": sp.get("daily_avg"),
        "net_worth": (_m("net_worth").get("display")) or networth.get("total_display") or (
            f"${nw_total:,.0f}" if isinstance(nw_total, (int, float)) else None),
        "left_to_spend": _m("left_to_spend").get("display"),
        # What is owed across Firefly's liability accounts, read live. Firefly
        # derives left_to_spend from budgets and spending, so card balances are
        # not in it — this sits BESIDE that figure rather than altering it,
        # because silently changing someone else's number is how a dashboard
        # stops agreeing with the ledger it claims to mirror.
        "owed": card_debt(firefly),
        "income_month": _m("earned").get("value"),
        "state": state,
        # Everything the Firefly sub-app puts on screen except its recent
        # transactions, so the headline figures need no second click.
        "earned": _m("earned").get("display"),
        "spent": _m("spent").get("display"),
        "accounts": [
            {"name": a.get("name"), "balance": a.get("balance")}
            for a in ((firefly or {}).get("accounts") or [])
            if a.get("name")
        ],
        "categories": sorted(cats, key=lambda c: c.get("amount", 0), reverse=True),
        "recent": (sp.get("recent") or (firefly or {}).get("recent", []))[:6],
    }


def _paycheck_brief(pay: dict) -> dict:
    """The homepage's slice of the pay cycle — the numbers behind "left to
    spend", not the whole cycle payload. Unavailable states keep their reason
    so the card can say why rather than showing a blank."""
    pay = pay or {}
    if not pay.get("available"):
        return {"available": False, "configured": bool(pay.get("configured")),
                "reason": pay.get("reason")}
    c = pay.get("cycle") or {}
    return {
        "available": True, "configured": True,
        "fresh": pay.get("fresh"), "stale_reason": pay.get("stale_reason"),
        "as_of": pay.get("as_of"),
        "paycheck": c.get("paycheck"), "cycle_start": c.get("start"),
        "savings_total": c.get("savings_total"),
        # Money that came back OUT of savings, and any ambiguous allocation
        # config — both surfaced rather than folded silently into a total.
        "from_savings": c.get("from_savings"),
        "allocation_overlaps": c.get("allocation_overlaps", []),
        "unmatched_savings": c.get("unmatched_savings", []),
        "withheld_rule_conflicts": c.get("withheld_rule_conflicts", []),
        # One flag, not two. `figures_complete` (from the cycle block) and
        # `window_complete` (from the top) were always the same boolean, and
        # flattening both into this dict now means the same key — so they
        # collapse. The top-level one is the more reliable source: it survives
        # the degraded path where the cycle block is dropped entirely.
        "window_complete": pay.get("window_complete",
                                   c.get("window_complete", True)),
        "allocations": c.get("allocations", []),
        "spendable": c.get("spendable"), "spent": c.get("spent"),
        "left": c.get("left"), "per_day": c.get("per_day"),
        "next_payday": c.get("next_payday"), "days_to_next": c.get("days_to_next"),
        "overdue": c.get("overdue"), "state": c.get("state"), "text": c.get("text"),
    }


def _budget_brief(status) -> dict:
    """The homepage's budget signal: budget room, the single worst warning,
    and whether everything is on track — never the whole budget database."""
    if not status or not status.get("available"):
        return {"available": False,
                "configured": bool(status and status.get("configured")),
                "recurring": (status or {}).get("recurring")
                             or {"available": False, "events": []}}
    warns = status.get("warnings", [])
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
        # Subscriptions that appeared, moved price, or came back. Passed
        # through as the budget service framed it — this layer must not
        # turn an unavailable read into an empty one.
        "recurring": status.get("recurring") or {"available": False, "events": []},
    }


def _read_deploy_record(path=None):
    """The host's deploy record, or {} when we cannot read it.

    Every failure — no mount, no file, bad JSON, a JSON scalar where an object
    belongs — collapses to {}, which `deploy_state` reads as `unknown`. That is
    the point: this function must never be able to manufacture a healthy-
    looking state out of a read it did not manage to do.
    """
    try:
        with open(path or DEPLOY_RECORD) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _disk_usage():
    """{"total", "free"} for the state volume, or None when the mount is not
    there. None, not the container's own filesystem: that would be a number
    about the wrong disk, which is worse than no number."""
    if not os.path.isdir(STATE_DIR):
        return None
    try:
        u = shutil.disk_usage(STATE_DIR)
    except OSError:
        return None
    return {"total": u.total, "free": u.free}


def _read_data_safety_record(path=None):
    """The host's backup/restore record, or {} when we cannot read it.

    Same contract as _read_deploy_record for the same reason: every failure
    collapses to {}, which `data_safety` reads as `unknown`. It must not be
    possible for a read that did not happen to produce a safe-looking answer.
    """
    try:
        with open(path or DATA_SAFETY_RECORD) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


async def build_home(fresh: bool = False) -> dict:
    cached = _HOME_CACHE
    if (not fresh and cached["data"] and cached["at"]
            and (datetime.now(LOCAL_TZ) - cached["at"]).total_seconds() < _HOME_TTL):
        return cached["data"]

    async with httpx.AsyncClient() as client:
        (settings, emails_r, budget, firefly, spending, networth, schedule,
         cal_health, stocks, powerbuy_home, amex_home, weather_home) = await asyncio.gather(
            _get(client, f"{CORE_URL}/settings"),
            # Read for ONE reason: as negative evidence about the Google
            # login. gmail can establish that no OAuth consent exists, and
            # nothing more — see schedule_state. The inbox itself is not on
            # the home screen any more.
            _get(client, f"{GMAIL_URL}/needs-reply"),
            _get(client, f"{BUDGET_URL}/status"),
            _get(client, f"{FIREFLY_SVC_URL}/dashboard"),
            _get(client, f"{FIREFLY_SVC_URL}/spending"),
            # Every account with its kind and role, for the runway.
            _get(client, f"{FIREFLY_SVC_URL}/networth"),
            _get(client, f"{SCHEDULE_URL}/events"),
            # Read-only Calendar reachability. Fetched alongside the rest so it
            # costs no extra round trip; `_get` swallows failures into {}, which
            # correctly reads as "no evidence" rather than as health.
            _get(client, f"{SCHEDULE_URL}/calendar-health"),
            _get(client, f"{STOCKS_URL}/portfolio"),
            # The resale book: profit expected, money owed, and — the only
            # figure here with a deadline — buys whose window closes soon.
            _get(client, f"{POWERBUY_URL}/summary"),
            # Statement credits that expire on a calendar boundary and do not
            # roll over. The service does the period arithmetic; this only
            # carries the answer.
            _get(client, f"{AMEX_URL}/summary"),
            # Actual conditions for the place chosen in settings. The service
            # owns the parsing and the location's clock; this only carries it.
            _get(client, f"{WEATHER_URL}/current"),
        )

    # The calendar mirrors Google and shows nothing else. Anthony, 2026-09-16:
    # "i want REPEATS OFF my calendar. it should look exactly like my google
    # calendar." It used to include the pending holds this service wrote from
    # sent mail, and that is how one interview became three entries.
    all_events = [e for e in (schedule.get("events", []) if schedule else [])
                  if e.get("status", "confirmed") != "declined"
                  and on_google_calendar(e)]
    now_local = datetime.now(LOCAL_TZ)
    events = _upcoming_events(all_events, now_local, limit=None,
                              statuses=("confirmed",))
    # `state` travels with the week because an unreachable schedule service
    # and a genuinely clear week are not the same fact, and the card must not
    # render the first as the second.
    week = week_window(all_events, now_local, LOCAL_TZ)
    # gmail is passed as NEGATIVE evidence only — it can establish that no
    # OAuth consent exists, and nothing more. It cannot establish Calendar
    # health: gmail keeps mode="live" while serving cached mail after a failed
    # fetch, and a shared credential is not proof of Calendar API access.
    # `calendar_evidence` comes from the schedule service's read-only
    # /calendar-health probe, which is the only thing here that actually talks
    # to Calendar. Absent or failed, the answer is "unknown", never "ok".
    week["state"] = schedule_state(schedule, emails_r, calendar_evidence=cal_health)
    settings = settings or {}

    t = _home_time(settings)
    data = {
        **t,
        "money": _money(firefly, spending, budget, networth, settings),
        "budget": _budget_brief(budget),
        # `state` travels with it: an unreachable stocks service is not an
        # empty portfolio, and must not read as "add your stocks". `alerts`
        # is what makes the "Alert on move >= (%)" setting do something.
        "portfolio": {
            **(stocks or {}), "state": portfolio_state(stocks),
            "alerts": portfolio_alerts(
                stocks, (settings.get("market", {}) or {}).get("move_threshold_pct")),
            "move_threshold_pct": (settings.get("market", {}) or {}).get("move_threshold_pct")},
        "resale": resale_brief(powerbuy_home),
        "amex": amex_brief(amex_home),
        "weather": weather_brief(weather_home),
        "next_event": events[0] if events else None,
        "week": week,
        # What the box is actually running. A failed deploy leaves the
        # PREVIOUS build serving and is otherwise completely silent from
        # the UI, so this is the only place a stale build announces itself.
        "deploy": deploy_state(_read_deploy_record(), now_local),
        # Days since the last VERIFIED restore, and "never" until one happens.
        # A backup you have never restored is a belief, not a backup.
        "data_safety": data_safety(_read_data_safety_record(), now_local),
        # Whether the Firefly import is actually running and landing rows
        # (SCRUM-142): the script's own record, cross-read with the ledger's
        # ingest age so "importer runs, nothing enters" is named, not hidden.
        "import_run": import_state(_read_data_safety_record(),
                                   (spending or {}).get("ingest_days"),
                                   now_local),
        # Fact 1 of SCRUM-67, the half that needs no privileged access.
        "disk": disk_state(_disk_usage()),
        "last_updated": t["now"],
    }
    _HOME_CACHE["data"] = data
    _HOME_CACHE["at"] = datetime.now(LOCAL_TZ)
    return data


@app.get("/home")
async def home(fresh: int = 0):
    return await build_home(fresh=bool(fresh))


@app.post("/notify")
async def notify_now(text: str):
    """Send yourself a message on the configured channel (docs/SETUP-NOTIFICATIONS.md).
    The sync uses the same path for its digest of what changed."""
    result = await notify.send(text)
    return {"message": text, **result}


def _sender_name(addr: str) -> str:
    """'Yeji Jong <yeji.jong@meetelise.com>' -> 'Yeji Jong'. Falls back to the
    address itself (or its domain) when there's no display name to show."""
    name = addr.split("<")[0].strip().strip('"')
    return name or addr


def _short(text: str, limit: int = 48) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _sync_availability_threads(conn, client: httpx.AsyncClient) -> tuple[int, list[str]]:
    """Threads where you proposed your own availability ("I'm available
    Monday at 2pm"). Books the one confirmed slot as a real calendar event and
    clears any speculative holds for that thread, so the calendar never shows
    three tentative entries for a meeting that's locked to one time.

    Skips any thread whose state hasn't changed since the last sync (see
    thread_state table) — that's what keeps this from re-creating the same
    event or re-announcing the same "still waiting" status forever.
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
        # index) so a slot that survives across rounds maps to the same
        # calendar event, while a slot that's no longer offered has no id in
        # `keep_ids` and gets cleaned up below instead of lingering.
        keep_ids: list[str] = []
        if status in ("pending", "countered"):
            # NO calendar event per proposed slot. This used to mint one and
            # push each to Google, so offering an interviewer three times put
            # three tentative entries on the real calendar and three rows on
            # the dashboard for one interview. A time you OFFERED is not a
            # commitment; the line below reports it, and the thread is what
            # you act on. When the slot is agreed, the `confirmed` branch
            # creates the single event that belongs there.
            #
            # `keep_ids` stays empty on purpose: resolve-thread then declines
            # and deletes any holds a previous version of this code left
            # behind, including from Google Calendar.
            slots = countered if status == "countered" else proposed
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
    """Keep the calendar in step with the inbox. _auto_sync_loop calls this on
    AUTO_SYNC_SECONDS; the route is the manual trigger
    (curl -X POST /api/assistant/sync).

    Reads the gmail service's triage once and: books an interview the mail
    names a time for, tracks the availability threads you started, pulls in
    anything added straight to Google Calendar, and retires the speculative
    holds an older version of this file used to push. `notable` collects the
    real changes — never a routine heartbeat — and is what gets texted when
    NOTIFY_ON_SYNC is on.
    """
    created = []
    notable: list[str] = []
    async with pool.connection() as conn:
        async with httpx.AsyncClient() as client:
            emails = await _get(client, f"{GMAIL_URL}/needs-reply")
            mails = emails.get("emails", [])

            # Retire the speculative interview holds a previous version of
            # this file pushed to Google Calendar. Idempotent and cheap: once
            # they are gone it deletes nothing. It runs here rather than as a
            # migration because the threads that created them may never be
            # re-processed, so `resolve-thread` would never reach them.
            # Guarded: cleaning up old holds is housekeeping, and housekeeping
            # that can abort the whole sync is worse than the mess.
            try:
                r = await client.post(f"{SCHEDULE_URL}/events/purge-holds", timeout=15)
                purged = r.json() if r.status_code == 200 else {}
            except Exception:                           # noqa: BLE001
                purged = {}
            if purged.get("purged"):
                notable.append(
                    f"Removed {purged['purged']} proposed-time hold(s) from the calendar "
                    "— a time you offered is not a commitment.")

            # Book the interviews the inbox names a time for.
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

            # Your own sent "I'm available..." proposals — pending until they
            # reply, confirmed/countered/declined after.
            thread_changes, thread_lines = await _sync_availability_threads(conn, client)
            notable.extend(thread_lines)

            # Pull in anything added straight to Google Calendar — e.g. from
            # your phone — that this app didn't push itself; the other
            # direction of sync from the steps above.
            try:
                pull_resp = await client.post(f"{SCHEDULE_URL}/sync-from-calendar", timeout=15)
                pull = pull_resp.json() if pull_resp.status_code < 300 else {}
            except Exception:  # noqa: BLE001 - schedule unreachable, just skip this cycle
                pull = {}
            imported = pull.get("imported", 0)
            if imported:
                notable.append(f"📱 Pulled {imported} event(s) from Google Calendar")

            for ev in created:
                notable.append(f"📅 Booked: {_short(ev['title'])} — {ev['starts_at']}")

    # Only text you when something on `notable` is actually new — not on
    # every fluctuation (email counts, etc.).
    notified = False
    if NOTIFY_ON_SYNC and notify.configured() and notable:
        digest = "FrankensteinCentral — " + "; ".join(notable)
        result = await notify.send(digest)
        notified = bool(result.get("sent"))

    return {"synced": True, "events_created": created,
            "availability_threads_updated": thread_changes,
            "notified": notified, "notable": notable}


@app.get("/")
async def root():
    return {"app": "Assistant", "endpoints": ["/home", "/sync", "/notify", "/health"]}
