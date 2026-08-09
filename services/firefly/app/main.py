"""Firefly III sub-app — a read-only dashboard for your self-hosted Firefly III.

Stateless client (no database). Point it at your Firefly with a Personal Access
Token and it surfaces net worth, this month's spent/earned/left-to-spend, asset
account balances, and recent transactions. It never writes to Firefly and never
sends the token to the browser.

Config:
  FIREFLY_URL           internal/base URL the service reads (e.g. http://fireflyiii:8080)
  FIREFLY_TOKEN         Firefly: Options -> Profile -> OAuth -> Personal Access Tokens
  FIREFLY_WEB_URL       browser-facing URL for the "Open in Firefly" link (e.g. http://<box-ip>:8095)
  FIREFLY_IMPORTER_URL  browser-facing URL for the data importer (e.g. http://<box-ip>:8096)
"""
import os
from datetime import date, timedelta

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Firefly Service")

FIREFLY_URL = os.environ.get("FIREFLY_URL", "").rstrip("/")
FIREFLY_TOKEN = os.environ.get("FIREFLY_TOKEN", "")
# Browser-facing links. Only surfaced when explicitly set, since FIREFLY_URL is
# usually the internal docker hostname (not reachable from a browser).
FIREFLY_WEB_URL = os.environ.get("FIREFLY_WEB_URL", "").rstrip("/")
FIREFLY_IMPORTER_URL = os.environ.get("FIREFLY_IMPORTER_URL", "").rstrip("/")

MOCK = {
    "currency": "USD",
    "net_worth": {"value": 48250.00, "display": "$48,250"},
    "spent": {"value": -2140.55, "display": "-$2,140"},
    "earned": {"value": 5200.00, "display": "$5,200"},
    "left_to_spend": {"value": 1310.00, "display": "$1,310"},
    "bills_unpaid": {"value": -180.00, "display": "-$180"},
    "accounts": [
        {"name": "Checking", "balance": "4210.55", "currency": "USD", "type": "asset"},
        {"name": "Savings (Marcus)", "balance": "18800.00", "currency": "USD", "type": "asset"},
        {"name": "Brokerage", "balance": "25239.45", "currency": "USD", "type": "asset"},
    ],
    "liabilities": [
        {"name": "Credit Card", "balance": "-1240.00", "currency": "USD", "type": "liability"},
    ],
    "categories": [
        {"name": "Groceries", "amount": 612.40},
        {"name": "Rent", "amount": 1500.00},
        {"name": "Dining out", "amount": 284.10},
        {"name": "Transport", "amount": 176.30},
        {"name": "Subscriptions", "amount": 63.97},
        {"name": "Shopping", "amount": 143.20},
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


def _accts(payload: dict, kind: str) -> list[dict]:
    out = []
    for a in payload.get("data", []):
        at = a.get("attributes", {})
        out.append({"name": at.get("name", ""), "balance": at.get("current_balance", "0"),
                    "currency": at.get("currency_code", ""), "type": kind})
    return out


async def _live() -> dict:
    today = date.today()
    start = today.replace(day=1).isoformat()
    end = today.isoformat()
    cat_start = (today - timedelta(days=30)).isoformat()  # pie = trailing 30 days
    async with httpx.AsyncClient() as client:
        s = await client.get(f"{FIREFLY_URL}/api/v1/summary/basic",
                             params={"start": start, "end": end},
                             headers=_headers(), timeout=20)
        s.raise_for_status()
        summary = s.json()
        asset = await client.get(f"{FIREFLY_URL}/api/v1/accounts",
                                 params={"type": "asset"}, headers=_headers(), timeout=20)
        liab = await client.get(f"{FIREFLY_URL}/api/v1/accounts",
                                params={"type": "liability"}, headers=_headers(), timeout=20)
        tx = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                              params={"limit": 10}, headers=_headers(), timeout=20)
        cat = await client.get(f"{FIREFLY_URL}/api/v1/insight/expense/category",
                               params={"start": cat_start, "end": end},
                               headers=_headers(), timeout=20)

    net = _pick(summary, "net-worth-in-") or {}
    accounts = _accts(asset.json(), "asset")
    liabilities = _accts(liab.json(), "liability")
    recent = []
    for g in tx.json().get("data", []):
        splits = g.get("attributes", {}).get("transactions", [])
        if not splits:
            continue
        t = splits[0]
        recent.append({"desc": t.get("description", ""), "amount": t.get("amount", "0"),
                       "type": t.get("type", ""), "date": (t.get("date") or "")[:10],
                       "currency": t.get("currency_code", "")})
    categories = []
    for c in (cat.json() if isinstance(cat.json(), list) else []):
        amt = abs(float(c.get("difference_float") or 0))
        if amt <= 0:
            continue
        categories.append({"name": c.get("name") or "Uncategorized", "amount": round(amt, 2)})
    categories.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "currency": net.get("currency", "USD"),
        "net_worth": net or None,
        "spent": _pick(summary, "spent-in-"),
        "earned": _pick(summary, "earned-in-"),
        "left_to_spend": _pick(summary, "left-to-spend-in-"),
        "bills_unpaid": _pick(summary, "bills-unpaid-in-"),
        "accounts": accounts,
        "liabilities": liabilities,
        "categories": categories,
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
        "web_url": FIREFLY_WEB_URL or None,
        "importer_url": FIREFLY_IMPORTER_URL or None,
        "mode": "live" if _connected() else "mock",
        "connected": _connected(),
    }


@app.get("/dashboard")
async def dashboard():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)
    return {**d, "web_url": FIREFLY_WEB_URL or None,
            "importer_url": FIREFLY_IMPORTER_URL or None,
            "connected": _connected()}


@app.get("/networth")
async def networth():
    """Net worth as Firefly sees it: the headline number plus asset accounts and
    liabilities as line items. Consumed by the Net Worth sub-app."""
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)
    nw = d.get("net_worth") or {}
    accounts = []
    for a in d.get("accounts", []):
        accounts.append({"name": a["name"], "balance": float(a.get("balance") or 0)})
    for a in d.get("liabilities", []):
        accounts.append({"name": a["name"], "balance": float(a.get("balance") or 0)})
    total = nw.get("value")
    if total is None:
        total = sum(a["balance"] for a in accounts)
    return {
        "connected": _connected(),
        "total": round(float(total), 2),
        "total_display": nw.get("display"),
        "currency": d.get("currency", "USD"),
        "accounts": accounts,
        "web_url": FIREFLY_WEB_URL or None,
    }


@app.get("/")
async def root():
    return {"app": "Firefly", "endpoints": ["/summary", "/dashboard", "/networth", "/health"]}
