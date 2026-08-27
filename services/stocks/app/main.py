"""Stocks service — portfolio & watchlist quotes.

Keyless by design: quotes come from Stooq's public CSV endpoints (no API key,
no account). Your holdings and watchlist live in the core service's settings
(`market.holdings` = [{symbol, shares, cost?}], `market.watchlist` = [sym,...]),
so there's one place to configure them. If nothing is configured, the portfolio
reports `configured: false` instead of inventing data. If Stooq is unreachable
the service degrades gracefully rather than breaking the dashboard.

No credentials are stored or returned.
"""
import os
import time
from io import StringIO

import httpx
from fastapi import FastAPI

app = FastAPI(title="Stocks Service")

CORE_URL = os.environ.get("CORE_URL", "http://core:8000").rstrip("/")
STOOQ_BASE = os.environ.get("STOOQ_BASE", "https://stooq.com").rstrip("/")

# tiny in-process quote cache: {symbol: (ts, quote)}
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 600  # 10 minutes


def _norm(symbol: str) -> str:
    s = symbol.strip().lower()
    return s if "." in s else f"{s}.us"


async def _quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Latest close + previous close for one symbol, via Stooq daily CSV."""
    now = time.time()
    hit = _CACHE.get(symbol)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    url = f"{STOOQ_BASE}/q/d/l/?s={_norm(symbol)}&i=d"
    try:
        r = await client.get(url, timeout=8)
        r.raise_for_status()
        rows = [ln for ln in r.text.strip().splitlines() if ln]
        # header: Date,Open,High,Low,Close,Volume
        if len(rows) < 2 or rows[0].lower().startswith("<html") or "N/D" in r.text:
            return None
        data = rows[1:]
        last = data[-1].split(",")
        prev = data[-2].split(",") if len(data) >= 2 else last
        close = float(last[4])
        prev_close = float(prev[4])
        change = round(close - prev_close, 2)
        pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
        q = {"symbol": symbol.upper(), "price": round(close, 2),
             "prev_close": round(prev_close, 2), "change": change, "change_pct": pct}
        _CACHE[symbol] = (now, q)
        return q
    except Exception:  # noqa: BLE001
        return None


async def _settings_market() -> dict:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CORE_URL}/settings", timeout=5)
            r.raise_for_status()
            return r.json().get("market", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


@app.get("/health")
async def health():
    return {"service": "stocks", "ok": True}


@app.get("/quotes")
async def quotes(symbols: str = ""):
    syms = [s for s in (symbols or "").replace(" ", "").split(",") if s]
    out = []
    async with httpx.AsyncClient() as client:
        for s in syms:
            q = await _quote(client, s)
            if q:
                out.append(q)
    return {"quotes": out}


@app.get("/portfolio")
async def portfolio():
    market = await _settings_market()
    holdings = market.get("holdings", []) or []
    watch = market.get("watchlist", []) or []
    if not holdings and not watch:
        return {"configured": False, "positions": [], "watchlist": [], "movers": []}

    positions = []
    total_value = 0.0
    total_day = 0.0
    total_cost = 0.0
    async with httpx.AsyncClient() as client:
        for h in holdings:
            sym = h.get("symbol")
            shares = float(h.get("shares") or 0)
            if not sym or shares <= 0:
                continue
            q = await _quote(client, sym)
            if not q:
                positions.append({"symbol": str(sym).upper(), "shares": shares,
                                  "available": False})
                continue
            value = round(q["price"] * shares, 2)
            day = round(q["change"] * shares, 2)
            total_value += value
            total_day += day
            pos = {"symbol": q["symbol"], "shares": shares, "price": q["price"],
                   "change_pct": q["change_pct"], "value": value, "day_change": day,
                   "available": True}
            if h.get("cost"):
                cost = float(h["cost"]) * shares
                total_cost += cost
                pos["total_gain"] = round(value - cost, 2)
            positions.append(pos)
        watch_quotes = []
        for sym in watch:
            q = await _quote(client, sym)
            if q:
                watch_quotes.append(q)

    live = [p for p in positions if p.get("available")]
    movers = sorted(live, key=lambda p: p["change_pct"], reverse=True)
    top = movers[0] if movers else None
    bottom = movers[-1] if movers and len(movers) > 1 else None
    base = total_value - total_day
    return {
        "configured": True,
        "value": round(total_value, 2),
        "day_change": round(total_day, 2),
        "day_change_pct": round((total_day / base) * 100, 2) if base else 0.0,
        "total_gain": round(total_value - total_cost, 2) if total_cost else None,
        "positions": positions,
        "movers": {"up": top, "down": bottom},
        "watchlist": watch_quotes,
    }


@app.get("/")
async def root():
    return {"app": "Stocks", "endpoints": ["/portfolio", "/quotes", "/health"]}
