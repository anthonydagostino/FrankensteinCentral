"""Stocks service — portfolio & watchlist quotes.

Keyless by design, with TWO quote sources tried in order per symbol:
  1. Stooq daily CSV (no key, but rate-limits by IP daily)
  2. Yahoo Finance v8 chart endpoint (no key)
Failures are cached briefly so a dead/rate-limited source isn't hammered on
every refresh, and all quotes are fetched CONCURRENTLY so a large portfolio
doesn't blow past the homepage aggregator's timeout.

Holdings/watchlist live in the core service's settings (market.holdings =
[{symbol, shares, cost?}]). Fractional shares supported. If a symbol can't be
priced by either source it is returned with available:false — the rest of the
portfolio still prices. No credentials are stored or returned.

PRIVACY: the ONLY data sent to the external quote providers (Stooq, Yahoo) is
the ticker symbol itself. Share counts, cost basis, portfolio values, and all
position math stay local — computed in this service from the returned prices.
"""
import asyncio
import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI

app = FastAPI(title="Stocks Service")

CORE_URL = os.environ.get("CORE_URL", "http://core:8000").rstrip("/")
STOOQ_BASE = os.environ.get("STOOQ_BASE", "https://stooq.com").rstrip("/")
YAHOO_BASE = os.environ.get("YAHOO_BASE", "https://query1.finance.yahoo.com").rstrip("/")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) FrankensteinCentral/1.0"}
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))


def _today() -> date:
    """Today in the user's timezone — the one clock this service uses.

    docs/TESTING.md's rule, which had not reached this service: all date logic
    goes through one seam so tests can pin it and sweep a calendar, instead of
    passing on whatever day they happen to run.
    """
    return datetime.now(LOCAL_TZ).date()


def session_label(as_of, today: date) -> str | None:
    """How to honestly name the session a quote came from.

    Both quote sources are daily bars, so "Today" was a label the card could
    not support: during market hours it is usually the previous completed
    session, on a Saturday it is Friday-vs-Thursday, and over a long weekend
    it is three days old — all rendered as "Today" with no as-of date anywhere
    to contradict it. docs/BUDGETS.md would not permit that, and this is the
    same rule reaching the portfolio card.
    """
    d = as_of if isinstance(as_of, date) else None
    if d is None:
        try:
            d = date.fromisoformat(str(as_of)[:10])
        except (TypeError, ValueError):
            return None
    delta = (today - d).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday's close"
    if delta < 7:
        return f"{d.strftime('%a')} close"
    return f"{d.strftime('%-d %b')} close"

# quote cache: {SYMBOL: (ts, quote_or_None)} — successes kept 10 min,
# failures 5 min (so a rate-limited source gets retried, but not hammered).
_CACHE: dict[str, tuple[float, dict | None]] = {}
_TTL_OK = 600
_TTL_FAIL = 300
_CONCURRENCY = 10


def _norm(symbol: str) -> str:
    s = symbol.strip().lower()
    return s if "." in s else f"{s}.us"


def portfolio_session(quotes: list, today: date) -> dict:
    """Which session the PORTFOLIO headline is showing.

    A portfolio is only as current as its OLDEST quote: one stale symbol makes
    the headline a blend of sessions, so the label follows the oldest rather
    than flattering the freshest. "Today" requires every priced position to be
    genuinely intraday — a single end-of-day bar in the mix makes the whole
    number a close.
    """
    quotes = [q for q in quotes if q]
    stamps = sorted(str(q.get("as_of")) for q in quotes if q.get("as_of"))
    oldest = stamps[0] if stamps else None
    intraday = bool(quotes) and all(q.get("intraday") for q in quotes)
    return {
        "as_of": oldest,
        "intraday": intraday,
        "label": ("Today" if intraday
                  else session_label(oldest, today) if oldest else None),
    }


async def _stooq_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    url = f"{STOOQ_BASE}/q/d/l/?s={_norm(symbol)}&i=d"
    try:
        r = await client.get(url, timeout=5, headers=UA)
        r.raise_for_status()
        rows = [ln for ln in r.text.strip().splitlines() if ln]
        # header + >=1 data row; HTML or "Exceeded the daily hits limit" or
        # "N/D" all fail these checks and fall through to None.
        if len(rows) < 2 or rows[0].lower().startswith("<") or "N/D" in r.text:
            return None
        data = rows[1:]
        last = data[-1].split(",")
        prev = data[-2].split(",") if len(data) >= 2 else last
        close = float(last[4])
        prev_close = float(prev[4])
        change = round(close - prev_close, 2)
        pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
        return {"symbol": symbol.upper(), "price": round(close, 2),
                "prev_close": round(prev_close, 2), "change": change,
                "change_pct": pct, "source": "stooq",
                # Daily bars: this is a COMPLETED session, never intraday.
                "as_of": (last[0] or "").strip() or None, "intraday": False}
    except Exception:  # noqa: BLE001
        return None


async def _yahoo_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    url = f"{YAHOO_BASE}/v8/finance/chart/{symbol.upper()}"
    try:
        r = await client.get(url, params={"range": "2d", "interval": "1d"},
                             timeout=5, headers=UA)
        r.raise_for_status()
        result = (r.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None or not prev:
            return None
        change = round(float(price) - float(prev), 2)
        pct = round((change / float(prev)) * 100, 2)
        live = str(meta.get("marketState") or "").upper() == "REGULAR"
        stamp = meta.get("regularMarketTime")
        try:
            as_of = (datetime.fromtimestamp(int(stamp), LOCAL_TZ).date().isoformat()
                     if stamp else None)
        except (TypeError, ValueError, OSError):
            as_of = None
        return {"symbol": symbol.upper(), "price": round(float(price), 2),
                "prev_close": round(float(prev), 2), "change": change,
                "change_pct": pct, "source": "yahoo",
                # regularMarketPrice is live ONLY while the session is open;
                # outside it, this is the last close like everything else.
                "as_of": as_of, "intraday": live}
    except Exception:  # noqa: BLE001
        return None


async def _quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    key = symbol.strip().upper()
    if not key:
        return None
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < (_TTL_OK if hit[1] else _TTL_FAIL):
        return hit[1]
    q = await _stooq_quote(client, key)
    if q is None:
        q = await _yahoo_quote(client, key)
    _CACHE[key] = (now, q)
    return q


async def _quotes_bulk(symbols: list[str]) -> dict[str, dict | None]:
    """Fetch many quotes concurrently (bounded) — a 30-symbol portfolio must
    not take 30x one quote's latency."""
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        async def one(sym: str):
            async with sem:
                return sym, await _quote(client, sym)
        pairs = await asyncio.gather(*(one(s) for s in symbols))
    return dict(pairs)


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
    fetched = await _quotes_bulk(syms)
    return {"quotes": [q for q in (fetched.get(s) for s in syms) if q]}


@app.get("/portfolio")
async def portfolio():
    market = await _settings_market()
    holdings = market.get("holdings", []) or []
    watch = market.get("watchlist", []) or []
    if not holdings and not watch:
        return {"configured": False, "positions": [], "watchlist": [], "movers": []}

    valid = [h for h in holdings if h.get("symbol") and float(h.get("shares") or 0) > 0]
    all_syms = [h["symbol"] for h in valid] + [s for s in watch if s]
    fetched = await _quotes_bulk(list(dict.fromkeys(all_syms)))  # dedupe, keep order

    positions = []
    total_value = 0.0
    total_day = 0.0
    total_cost = 0.0
    for h in valid:
        sym = str(h["symbol"]).upper()
        shares = float(h["shares"])
        q = fetched.get(sym)
        if not q:
            positions.append({"symbol": sym, "shares": shares, "available": False})
            continue
        value = round(q["price"] * shares, 2)
        day = round(q["change"] * shares, 2)
        total_value += value
        total_day += day
        pos = {"symbol": sym, "shares": shares, "price": q["price"],
               "change_pct": q["change_pct"], "value": value, "day_change": day,
               "available": True, "source": q.get("source")}
        if h.get("cost"):
            cost = float(h["cost"]) * shares
            total_cost += cost
            pos["total_gain"] = round(value - cost, 2)
        positions.append(pos)

    watch_quotes = [fetched[s] for s in watch if fetched.get(s)]

    live = [p for p in positions if p.get("available")]
    failed = [p["symbol"] for p in positions if not p.get("available")]
    movers = sorted(live, key=lambda p: p["change_pct"], reverse=True)
    top = movers[0] if movers else None
    bottom = movers[-1] if movers and len(movers) > 1 else None
    base = total_value - total_day
    session = portfolio_session([fetched.get(p["symbol"]) for p in live], _today())
    return {
        "configured": True,
        "as_of": session["as_of"],
        "intraday": session["intraday"],
        # What the card should call the change it is showing. None => unknown,
        # which the card renders as such rather than guessing "Today".
        "session_label": session["label"],
        "value": round(total_value, 2),
        "day_change": round(total_day, 2),
        "day_change_pct": round((total_day / base) * 100, 2) if base else 0.0,
        "total_gain": round(total_value - total_cost, 2) if total_cost else None,
        "positions": positions,
        "quotes_ok": len(live),
        "quotes_failed": failed,
        "movers": {"up": top, "down": bottom},
        "watchlist": watch_quotes,
    }


@app.get("/")
async def root():
    return {"app": "Stocks", "endpoints": ["/portfolio", "/quotes", "/health"]}
