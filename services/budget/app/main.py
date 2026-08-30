"""Budget service — the time-aware budgeting layer over Firefly III.

Firefly stays the financial system of record; this service turns its
transaction history into forward-looking guidance: per-budget spend vs limit,
pace, safe-per-day, month-end projection, calm rule-based warnings, and a
clearly-scoped "safe to spend" number. Stateless — budget definitions live in
core settings (budgets: [{id, name, limit, categories}]), transactions come
from the firefly service each request (with a short cache).

All math lives in engine.py (pure + unit-tested); formulas and thresholds are
documented in docs/BUDGETS.md. Stale ledgers pause current-period guidance
rather than pretending $0 = "on track".
"""
import os
import time

import httpx
from fastapi import FastAPI

from .engine import budget_status

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

    budgets_cfg = (settings or {}).get("budgets") or []

    if not month or not month.get("connected"):
        data = {
            "available": False,
            "connected": bool(month and month.get("connected")),
            "configured": bool(budgets_cfg),
            "reason": "firefly not connected" if month else "firefly unreachable",
            "budgets": [], "warnings": [], "safe_to_spend": None,
        }
        _CACHE.update(at=now, data=data)
        return data

    status = budget_status(
        budgets_cfg=budgets_cfg,
        month=month.get("month", {}),
        categories=month.get("categories", {}),
        txns=month.get("transactions", []),
        days_stale=month.get("days_stale"),
    )
    status.update({
        "available": True,
        "connected": True,
        "configured": bool(budgets_cfg),
        "income_month": month.get("income_month"),
        "ledger_latest_txn": month.get("ledger_latest_txn"),
        "bills": bills if (bills and bills.get("connected")) else
                 {"connected": False, "supported": False, "items": []},
    })
    _CACHE.update(at=now, data=status)
    return status


@app.get("/health")
async def health():
    return {"service": "budget", "ok": True, "mode": "firefly-driven"}


@app.get("/status")
async def status(fresh: int = 0):
    return await _build(fresh=bool(fresh))


@app.get("/summary")
async def summary():
    """Compat shim for older consumers (legacy lounge overview/briefing)."""
    s = await _build()
    over = [b["name"] for b in s.get("budgets", []) if b.get("state") == "over"]
    return {"remaining": s.get("safe_to_spend") or 0,
            "over_budget": over, "count": len(s.get("budgets", []))}


@app.get("/")
async def root():
    return {"app": "Budget", "endpoints": ["/status", "/summary", "/health"],
            "note": "time-aware budgets over Firefly; config lives in core settings"}
