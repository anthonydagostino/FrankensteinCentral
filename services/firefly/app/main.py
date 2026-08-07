"""Firefly III sub-app — a read-only dashboard for your self-hosted Firefly III.

Stateless client (no database). Point it at your Firefly with a Personal Access
Token and it surfaces net worth, this month's spent/earned/left-to-spend, asset
account balances, and recent transactions. It never writes to Firefly and never
sends the token to the browser.

Config:
  FIREFLY_URL       e.g. http://<homelab-ip>:8080  (your Firefly base URL)
  FIREFLY_TOKEN     Firefly: Options -> Profile -> OAuth -> Personal Access Tokens
  FIREFLY_WEB_URL   optional; browser-facing URL for "Open in Firefly" (defaults to FIREFLY_URL)
"""
import os
from datetime import date

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Firefly Service")

FIREFLY_URL = os.environ.get("FIREFLY_URL", "").rstrip("/")
FIREFLY_TOKEN = os.environ.get("FIREFLY_TOKEN", "")
FIREFLY_WEB_URL = (os.environ.get("FIREFLY_WEB_URL") or FIREFLY_URL).rstrip("/")

MOCK = {
    "currency": "USD",
    "net_worth": {"value": 48250.00, "display": "$48,250"},
    "spent": {"value": -2140.55, "display": "-$2,140"},
    "earned": {"value": 5200.00, "display": "$5,200"},
    "left_to_spend": {"value": 1310.00, "display": "$1,310"},
    "bills_unpaid": {"value": -180.00, "display": "-$180"},
    "accounts": [
        {"name": "Checking", "balance": "4210.55", "currency": "USD"},
        {"name": "Savings (Marcus)", "balance": "18800.00", "currency": "USD"},
        {"name": "Brokerage", "balance": "25239.45", "currency": "USD"},
    ],
    "recent": [
        {"desc": "Whole Foods", "amount": "-86.40", "type": "withdrawal", "date": "2026-07-26", "currency": "USD"},
        {"desc": "Paycheck", "amount": "2600.00", "type": "deposit", "date": "2026-07-25", "currency": "USD"},
        {"desc": "Spotify", "amount": "-11.99", "type": "withdrawal", "date": "2026-07-24", "currency": "USD"},
        {"desc": "Transfer to Savings", "amount": "500.00", "type": "transfer", "date": "2026-07-23", "currency": "USD"},
    ],
}


def _connected() -> bool:
    return bool(FIREFLY_URL and FIREFLY_TOKEN)


def _headers() -> dict:
    return {"Authorization": f"Bearer {FIREFLY_TOKEN}", "Accept": "application/json"}


def _pick(summary: dict, prefix: str) -> dict | None:
    """summary/basic is keyed by dynamic '<metric>-in-<CURRENCY>' names."""
    for k, v in summary.items():
        if k.startswith(prefix):
            return {"value": v.get("monetary_value"), "display": v.get("value_parsed"),
                    "currency": v.get("currency_code")}
    return None


async def _live() -> dict:
    today = date.today()
    start = today.replace(day=1).isoformat()
    end = today.isoformat()
    async with httpx.AsyncClient() as client:
        s = await client.get(f"{FIREFLY_URL}/api/v1/summary/basic",
                             params={"start": start, "end": end},
                             headers=_headers(), timeout=20)
        s.raise_for_status()
        summary = s.json()
        acc = await client.get(f"{FIREFLY_URL}/api/v1/accounts",
                               params={"type": "asset"}, headers=_headers(), timeout=20)
        tx = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                              params={"limit": 10}, headers=_headers(), timeout=20)

    net = _pick(summary, "net-worth-in-") or {}
    accounts = []
    for a in acc.json().get("data", []):
        at = a.get("attributes", {})
        accounts.append({"name": at.get("name", ""), "balance": at.get("current_balance", "0"),
                         "currency": at.get("currency_code", "")})
    recent = []
    for g in tx.json().get("data", []):
        splits = g.get("attributes", {}).get("transactions", [])
        if not splits:
            continue
        t = splits[0]
        recent.append({"desc": t.get("description", ""), "amount": t.get("amount", "0"),
                       "type": t.get("type", ""), "date": (t.get("date") or "")[:10],
                       "currency": t.get("currency_code", "")})
    return {
        "currency": net.get("currency", "USD"),
        "net_worth": net or None,
        "spent": _pick(summary, "spent-in-"),
        "earned": _pick(summary, "earned-in-"),
        "left_to_spend": _pick(summary, "left-to-spend-in-"),
        "bills_unpaid": _pick(summary, "bills-unpaid-in-"),
        "accounts": accounts,
        "recent": recent,
    }


async def _data() -> dict:
    if not _connected():
        return MOCK
    return await _live()


@app.get("/health")
async def health():
    return {"service": "firefly", "mode": "live" if _connected() else "mock",
            "connected": _connected()}


@app.get("/summary")
async def summary():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)
    return {
        "net_worth": d.get("net_worth"),
        "spent": d.get("spent"),
        "earned": d.get("earned"),
        "left_to_spend": d.get("left_to_spend"),
        "bills_unpaid": d.get("bills_unpaid"),
        "currency": d.get("currency"),
        "web_url": FIREFLY_WEB_URL if _connected() else None,
        "mode": "live" if _connected() else "mock",
        "connected": _connected(),
    }


@app.get("/dashboard")
async def dashboard():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)
    return {**d, "web_url": FIREFLY_WEB_URL if _connected() else None,
            "connected": _connected()}


@app.get("/")
async def root():
    return {"app": "Firefly", "endpoints": ["/summary", "/dashboard", "/health"]}
