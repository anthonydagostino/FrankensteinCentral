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

All math lives in engine.py / paycheck.py (pure + unit-tested); formulas and
thresholds are documented in docs/BUDGETS.md. Stale ledgers pause
current-period guidance rather than pretending $0 = "on track".
"""
import os
import time

import httpx
from fastapi import FastAPI

from .engine import budget_status
from .paycheck import paycheck_cycle

app = FastAPI(title="Budget Service")

FIREFLY_SVC_URL = os.environ.get("FIREFLY_SVC_URL", "http://firefly:8000").rstrip("/")
CORE_URL = os.environ.get("CORE_URL", "http://core:8000").rstrip("/")

_CACHE: dict = {"at": 0.0, "data": None}
_TTL = 60  # seconds; /status?fresh=1 bypasses


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
        settings = await _get(client, f"{CORE_URL}/settings", timeout=8)
        month = await _get(client, f"{FIREFLY_SVC_URL}/month", timeout=40)
        bills = await _get(client, f"{FIREFLY_SVC_URL}/bills", timeout=20)
        cycle = await _get(client, f"{FIREFLY_SVC_URL}/cycle", timeout=40)

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
                   "month_ingested": month.get("month_ingested")},
    )
    status.update({
        "paycheck": _paycheck(paycheck_cfg, cycle),
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
                   "ledger_latest_txn": cycle.get("ledger_latest_txn")},
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


@app.get("/summary")
async def summary():
    """Compat shim for older consumers (legacy lounge overview/briefing)."""
    s = await _build()
    over = [b["name"] for b in s.get("budgets", []) if b.get("state") == "over"]
    return {"remaining": s.get("budget_room") or 0,
            "over_budget": over, "count": len(s.get("budgets", []))}


@app.get("/")
async def root():
    return {"app": "Budget", "endpoints": ["/status", "/paycheck", "/summary", "/health"],
            "note": "time-aware budgets over Firefly; config lives in core settings"}
