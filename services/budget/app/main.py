"""Budget service — the time-aware budgeting layer over Firefly III.

Firefly stays the financial system of record; this service turns its
transaction history into forward-looking guidance: per-budget spend vs limit,
pace, safe-per-day, month-end projection, calm rule-based warnings, and a
clearly-scoped "budget room" number. Stateless — budget definitions live in
core settings (budgets: [{id, name, limit, categories}]), transactions come
from the firefly service each request (with a short cache).

It also serves the **pay cycle** (/paycheck, and embedded in /status): what
was spent this month with savings transfers taken back out, and what is left
of the current paycheck after the savings that come out of it. That math is
in paycheck.py (also pure + unit-tested), fed by the firefly service's
/cycle endpoint; config lives in core settings (paycheck: {...}).

It also serves **recurring charges** (/recurring, summarised in /status):
subscriptions that appeared, moved price, or resumed after you thought they
were cancelled. That math is in recurring.py (pure), fed by the firefly
service's /history endpoint — a thirteen-month read, cached hard on both
sides because it is expensive and barely moves within a day.

All math lives in engine.py / paycheck.py / recurring.py (pure and
unit-tested); formulas and thresholds are documented in docs/BUDGETS.md.
Stale ledgers pause current-period guidance rather than pretending that $0
means "on track".
"""
import asyncio
import contextlib
import os
import time

import httpx
from fastapi import FastAPI

from .engine import budget_status
from .paycheck import paycheck_cycle
from .recurring import detect_recurring

app = FastAPI(title="Budget Service")

FIREFLY_SVC_URL = os.environ.get("FIREFLY_SVC_URL", "http://firefly:8000").rstrip("/")
CORE_URL = os.environ.get("CORE_URL", "http://core:8000").rstrip("/")

_CACHE: dict = {"at": 0.0, "data": None}
_TTL = 60  # seconds; /status?fresh=1 bypasses

# Recurrence is computed off a 13-month read, so it gets its own much longer
# cache: paying for that round trip on every /status would make the dashboard
# slow to answer a question whose answer changes about once a month.
_RECUR_CACHE: dict = {"at": 0.0, "data": None}
_RECUR_TTL = 900
_RECUR_TASK: dict = {"task": None}
# How many events the money card can show before it is a list rather than a
# headline. The full set is always at /recurring.
STATUS_EVENT_LIMIT = 3


async def _get(client, url, timeout=25):
    try:
        r = await client.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def _build(fresh: bool = False) -> dict:
    now = time.time()
    if not fresh and _CACHE["data"] and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]

    async with httpx.AsyncClient() as client:
        # Four independent reads. Awaited one at a time they added up to a
        # worst case of 108 seconds of timeout; concurrently the slowest one
        # is the whole wait.
        settings, month, bills, cycle = await asyncio.gather(
            _get(client, f"{CORE_URL}/settings", timeout=8),
            _get(client, f"{FIREFLY_SVC_URL}/month", timeout=40),
            _get(client, f"{FIREFLY_SVC_URL}/bills", timeout=20),
            _get(client, f"{FIREFLY_SVC_URL}/cycle", timeout=40),
        )

    budgets_cfg = (settings or {}).get("budgets") or []
    paycheck_cfg = (settings or {}).get("paycheck") or {}

    if not month or not month.get("connected"):
        data = {
            "available": False,
            "connected": bool(month and month.get("connected")),
            "configured": bool(budgets_cfg),
            "reason": "firefly not connected" if month else "firefly unreachable",
            "budgets": [], "warnings": [], "budget_room": None,
            "paycheck": {"configured": bool(paycheck_cfg), "available": False,
                         "reason": "firefly not connected", "month": None,
                         "cycle": None},
            "recurring": {"available": False, "events": [], "event_count": 0,
                          "reason": "firefly not connected" if month
                                    else "firefly unreachable"},
        }
        _CACHE.update(at=now, data=data)
        return data

    status = budget_status(
        budgets_cfg=budgets_cfg,
        month=month.get("month", {}),
        categories=month.get("categories", {}),
        txns=month.get("transactions", []),
        freshness={"ingest_days": month.get("ingest_days"),
                   "activity_days": month.get("days_stale"),
                   "month_ingested": month.get("month_ingested"),
                   # A truncated month read understates spend, so budgets
                   # would look healthier than they are.
                   "window_complete": month.get("window_complete", True)},
    )
    status.update({
        "paycheck": _paycheck(paycheck_cfg, cycle),
        "recurring": _recurring_summary(_recurring_warm()),
        "available": True,
        "connected": True,
        "configured": bool(budgets_cfg),
        "income_month": month.get("income_month"),
        "ledger_latest_txn": month.get("ledger_latest_txn"),
        "ingest_latest": month.get("ingest_latest"),
        "importer_url": month.get("importer_url"),
        "bills": bills if (bills and bills.get("connected")) else
                 {"connected": False, "supported": False, "items": []},
    })
    _CACHE.update(at=now, data=status)
    return status


async def _recurring(fresh: bool = False) -> dict:
    """Subscriptions and their changes, over a long window.

    Kept off the /status hot path by its own cache. When firefly can't answer,
    this reports unavailable — it never reports "no subscriptions found",
    which is a claim about the world rather than about the read.
    """
    now = time.time()
    if not fresh and _RECUR_CACHE["data"] and now - _RECUR_CACHE["at"] < _RECUR_TTL:
        return _RECUR_CACHE["data"]
    async with httpx.AsyncClient() as client:
        hist = await _get(client, f"{FIREFLY_SVC_URL}/history", timeout=60)
    if not hist or not hist.get("connected"):
        # Not cached: an outage should not persist as an answer for 15 minutes.
        return {"available": False, "items": [], "events": [],
                "reason": "firefly not connected" if hist else "firefly unreachable"}
    window = hist.get("window") or {}
    data = detect_recurring(
        charges=hist.get("withdrawals", []),
        today=hist.get("today"),
        window_start=window.get("start"),
        known_bills=hist.get("bills") or [],
        # A truncated read cannot support "this is new" or "this came back":
        # both are statements about what ISN'T there.
        complete=bool(hist.get("window_complete", True)),
    )
    data["lookback_days"] = window.get("lookback_days")
    _RECUR_CACHE.update(at=now, data=data)
    return data


async def _warm_recurring() -> None:
    """Fill the recurrence cache in the background. Nothing awaits this, so it
    swallows its own failures: an unretrieved task exception would surface as
    a noisy asyncio warning and nothing else, and the next request retries
    anyway (a failed read is deliberately never cached)."""
    with contextlib.suppress(Exception):
        await _recurring()


def _recurring_warm() -> dict:
    """The recurrence answer, but only if it is already computed.

    /status is the homepage's hot path and the read behind this is thirteen
    months of transactions — on a cold cache that is tens of sequential
    round trips to Firefly, and awaiting it here would hang the whole
    dashboard on the first load after a restart. So a cold cache starts the
    work in the background and this request answers "not yet" rather than
    waiting: the card shows one fewer line for one refresh instead of the
    homepage showing nothing for a minute.
    """
    now = time.time()
    if _RECUR_CACHE["data"] and now - _RECUR_CACHE["at"] < _RECUR_TTL:
        return _RECUR_CACHE["data"]
    task = _RECUR_TASK["task"]
    if task is None or task.done():
        with contextlib.suppress(RuntimeError):   # no running loop (sync test)
            _RECUR_TASK["task"] = asyncio.create_task(_warm_recurring())
    return {"available": False, "warming": True,
            "reason": "reading your transaction history"}


def _recurring_summary(rec: dict) -> dict:
    """The headline the money card shows: what changed, and how much of the
    month is already committed. The full inventory stays at /recurring."""
    if not rec.get("available"):
        return {"available": False, "reason": rec.get("reason"),
                "warming": rec.get("warming", False),
                "events": [], "event_count": 0}
    events = rec.get("events") or []
    return {
        "available": True,
        "window_complete": rec.get("window_complete", True),
        "absence_claims_suppressed": rec.get("absence_claims_suppressed", False),
        "event_count": len(events),
        "events": [{k: e[k] for k in ("event", "name", "amount", "cadence",
                                      "confidence", "last_seen")
                    if k in e} | ({"from": e["from"], "to": e["to"]}
                                  if e.get("event") == "changed" else {})
                   for e in events[:STATUS_EVENT_LIMIT]],
        "tracked": len(rec.get("items") or []),
        "monthly_equivalent": rec.get("monthly_equivalent"),
    }


def _paycheck(cfg: dict, cycle: dict | None) -> dict:
    """Pay-cycle answers: month-to-date spending and what's left of the
    current paycheck. All math is in paycheck.py (pure); this only supplies
    it with data and reports honestly when there is none."""
    if not cfg:
        return {"configured": False, "available": False,
                "reason": "no paycheck configured", "month": None, "cycle": None}
    if not cycle or not cycle.get("connected"):
        return {"configured": True, "available": False, "month": None, "cycle": None,
                "reason": "firefly not connected" if cycle else "firefly unreachable"}
    return paycheck_cycle(
        cfg=cfg,
        today=cycle.get("today"),
        month=cycle.get("month", {}),
        deposits=cycle.get("deposits", []),
        withdrawals=cycle.get("withdrawals", []),
        transfers=cycle.get("transfers", []),
        freshness={"ingest_days": cycle.get("ingest_days"),
                   "activity_days": cycle.get("days_stale"),
                   "month_ingested": cycle.get("month_ingested"),
                   "ledger_latest_txn": cycle.get("ledger_latest_txn"),
                   # A truncated fetch window cannot support a total.
                   "window_complete": cycle.get("window_complete", True)},
    )


@app.get("/health")
async def health():
    return {"service": "budget", "ok": True, "mode": "firefly-driven"}


@app.get("/status")
async def status(fresh: int = 0):
    return await _build(fresh=bool(fresh))


@app.get("/paycheck")
async def paycheck(fresh: int = 0):
    """The pay-cycle view on its own — same data /status embeds."""
    s = await _build(fresh=bool(fresh))
    return {**s.get("paycheck", {}), "importer_url": s.get("importer_url")}


@app.get("/recurring")
async def recurring(fresh: int = 0):
    """Every recurring charge detected, with the events worth telling you
    about. /status carries only the headline."""
    return await _recurring(fresh=bool(fresh))


@app.get("/summary")
async def summary():
    """Compat shim for older consumers (legacy lounge overview/briefing)."""
    s = await _build()
    over = [b["name"] for b in s.get("budgets", []) if b.get("state") == "over"]
    return {"remaining": s.get("budget_room") or 0,
            "over_budget": over, "count": len(s.get("budgets", []))}


@app.get("/")
async def root():
    return {"app": "Budget", "endpoints": ["/status", "/paycheck", "/recurring", "/summary",
                          "/health"],
            "note": "time-aware budgets over Firefly; config lives in core settings"}
