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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))

# Honest empty state shown until Firefly is connected — no fabricated numbers.
EMPTY = {
    "currency": "USD",
    "net_worth": None,
    "spent": None,
    "earned": None,
    "left_to_spend": None,
    "bills_unpaid": None,
    "accounts": [],
    "liabilities": [],
    "categories": [],
    "recent": [],
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
        return dict(EMPTY)
    return await _live()


@app.get("/health")
async def health():
    return {"service": "firefly", "mode": "live" if _connected() else "disconnected",
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
        "mode": "live" if _connected() else "disconnected",
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


async def _fetch_withdrawals(client, start: str, end: str) -> list[dict]:
    """All withdrawal splits in [start, end], paging through Firefly."""
    out, page = [], 1
    while page <= 6:
        r = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                             params={"type": "withdrawal", "start": start, "end": end,
                                     "limit": 50, "page": page},
                             headers=_headers(), timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        for g in data:
            for t in g.get("attributes", {}).get("transactions", []):
                try:
                    amt = abs(float(t.get("amount") or 0))
                except (TypeError, ValueError):
                    amt = 0
                out.append({"desc": t.get("description", ""), "amount": round(amt, 2),
                            "date": (t.get("date") or "")[:10],
                            "category": t.get("category_name") or "Uncategorized"})
        if len(data) < 50:
            break
        page += 1
    return out


async def _spending() -> dict:
    today = datetime.now(LOCAL_TZ).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    # last-month "to date" cutoff = same day-of-month as today (for pace)
    lm_cutoff = last_month_start + timedelta(days=(today.day - 1))
    async with httpx.AsyncClient() as client:
        wd = await _fetch_withdrawals(client, last_month_start.isoformat(), today.isoformat())

    def d(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    today_sum = week_sum = month_sum = lm_to_date = 0.0
    biggest_today = None
    for t in wd:
        dt = d(t["date"])
        if dt is None:
            continue
        if dt >= month_start:
            month_sum += t["amount"]
            if dt >= week_start:
                week_sum += t["amount"]
            if dt == today:
                today_sum += t["amount"]
                if biggest_today is None or t["amount"] > biggest_today["amount"]:
                    biggest_today = t
        elif last_month_start <= dt <= lm_cutoff:
            lm_to_date += t["amount"]
    day_n = today.day
    daily_avg = round(month_sum / day_n, 2) if day_n else 0

    # Evidentiary confidence for the month-over-month comparison. Arithmetic on
    # a partial window is not an insight: only emit pace when the ledger
    # plausibly covers BOTH sides of the comparison. Raw totals always display.
    all_dates = [x for x in (d(t["date"]) for t in wd) if x is not None]
    earliest = min(all_dates) if all_dates else None
    latest = max(all_dates) if all_dates else None
    if not lm_to_date:
        baseline, pace_note = "none", "no last-month spending in the ledger to compare against"
    elif earliest and earliest > last_month_start + timedelta(days=5):
        baseline = "partial_history"
        pace_note = f"ledger history starts {earliest.isoformat()}, so last month is incomplete"
    elif latest and (today - latest).days >= 7:
        baseline = "stale_data"
        pace_note = f"newest transaction is {(today - latest).days} days old, so this month may be incomplete"
    else:
        baseline, pace_note = "ok", None
    pace_pct = (round(((month_sum - lm_to_date) / lm_to_date) * 100)
                if baseline == "ok" else None)

    recent = sorted([t for t in wd], key=lambda t: t["date"], reverse=True)[:12]
    return {
        "connected": True,
        "currency": "USD",
        "tz": str(LOCAL_TZ),
        "txn_count": len(wd),
        "today": round(today_sum, 2), "week": round(week_sum, 2), "month": round(month_sum, 2),
        "last_month_to_date": round(lm_to_date, 2), "pace_pct": pace_pct,
        "baseline": baseline, "pace_note": pace_note,
        "earliest_txn": earliest.isoformat() if earliest else None,
        "latest_txn": latest.isoformat() if latest else None,
        "daily_avg": daily_avg, "biggest_today": biggest_today, "recent": recent,
    }


@app.get("/spending")
async def spending():
    if not _connected():
        return {"connected": False, "today": None, "week": None, "month": None,
                "pace_pct": None, "recent": [], "biggest_today": None}
    try:
        return await _spending()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)


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
