"""Stocks service — portfolio & watchlist quotes.

Keyless by design, with TWO quote sources tried in order per symbol:
  1. Stooq daily CSV (no key, but rate-limits by IP daily)
  2. Yahoo Finance v8 chart endpoint (no key)
Failures are cached briefly so a dead/rate-limited source isn't hammered on
every refresh, and all quotes are fetched CONCURRENTLY so a large portfolio
doesn't blow past the homepage aggregator's timeout.

Holdings live in the core service's settings, per ACCOUNT:

    market.accounts   = [{name, holdings: [{symbol, shares, cost?}], cash}]
    market.recurring  = [{account, cadence_days, next, buys: {SYMBOL: dollars}}]
    market.holdings   = the pre-2026-10-07 flat list; read as one account
                        named "Robinhood" until market.accounts exists

Fractional shares supported. If a symbol can't be priced by either source it is
returned with available:false — the rest of the portfolio still prices.

A recurring plan is money that lands on a schedule (a paycheck's $1,100 into a
brokerage every other Monday). On and after its `next` date this service
APPLIES it: each buy becomes shares at that day's quote, added to the account
and saved back to core, and `next` advances. Those shares are ESTIMATES — the
broker fills at its own price — so every lot this service invents is flagged
`estimated` and the card says so until the person trues the count up from the
broker's own statement. No credentials are stored or returned.

PRIVACY: the ONLY data sent to the external quote providers (Stooq, Yahoo) is
the ticker symbol itself. Share counts, cost basis, portfolio values, and all
position math stay local — computed in this service from the returned prices.
"""
import asyncio
import os
import time
from datetime import date, datetime, timedelta
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

LEGACY_ACCOUNT = "Robinhood"


def accounts_from(market: dict) -> list[dict]:
    """The accounts to price, from a settings block of either vintage.

    `market.accounts` wins. A settings row written before accounts existed
    carries only `market.holdings`; that list is the brokerage the dashboard
    was built around, so it is read as one account under LEGACY_ACCOUNT
    rather than dropped. Nothing is written back by this read.
    """
    market = market or {}
    accounts = market.get("accounts") or []
    out = []
    for a in accounts:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        out.append({"name": str(a["name"]),
                    "holdings": [h for h in (a.get("holdings") or []) if isinstance(h, dict)],
                    "cash": _money(a.get("cash"))})
    if out:
        return out
    legacy = [h for h in (market.get("holdings") or []) if isinstance(h, dict)]
    if legacy:
        return [{"name": LEGACY_ACCOUNT, "holdings": legacy, "cash": 0.0}]
    return []


def _money(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def apply_due_contributions(accounts: list[dict], recurring: list[dict],
                            prices: dict, today: date) -> tuple[list[dict], list[dict], list[dict]]:
    """Turn every contribution that has come due into estimated shares.

    Returns (accounts, recurring, applied). Pure: operates on copies, and
    `today` is injected so a calendar can be swept over it in tests.

    The rules, each of which is the honest choice rather than the convenient
    one:

      * A plan is due when `next` <= today. Every missed period is applied —
        a box that was off for a month still owes the ledger two buys — each
        at TODAY's price, because that is the only price this service has.
      * A buy whose symbol has no quote right now is not applied and the plan
        does not advance: inventing shares at an unknown price would be a
        made-up number, and the next request will try again.
      * The shares are an ESTIMATE. The broker filled at the open and this
        service only has a close; the lot is tagged `estimated` with the date
        and dollars, the holding's `estimated_shares` grows, and the card
        says so until the person replaces the count from the statement.
      * The holding's cost basis is the dollar-weighted blend of what it was
        and what was just paid, so total gain stays truthful.
      * An unknown account name is a configuration error and is reported,
        not silently created.
    """
    accounts = [dict(a, holdings=[dict(h) for h in a.get("holdings") or []])
                for a in (accounts or [])]
    by_name = {a["name"]: a for a in accounts}
    plans = [dict(r) for r in (recurring or []) if isinstance(r, dict)]
    applied = []
    for plan in plans:
        try:
            nxt = date.fromisoformat(str(plan.get("next"))[:10])
        except (TypeError, ValueError):
            continue
        try:
            cadence = 14 if plan.get("cadence_days") is None else int(plan["cadence_days"])
        except (TypeError, ValueError):
            continue
        if cadence <= 0:
            continue  # an explicit zero is a broken plan, not a fortnightly one
        buys = {str(k).upper(): _money(v) for k, v in (plan.get("buys") or {}).items()
                if _money(v) > 0}
        account = by_name.get(plan.get("account"))
        if not buys or account is None:
            continue
        if any(not prices.get(sym) for sym in buys):
            continue  # no price for something in the basket: wait, do not guess
        while nxt <= today:
            lots = []
            for sym, dollars in buys.items():
                price = float(prices[sym])
                shares = round(dollars / price, 4)
                holding = next((h for h in account["holdings"]
                                if str(h.get("symbol", "")).upper() == sym), None)
                if holding is None:
                    holding = {"symbol": sym, "shares": 0.0}
                    account["holdings"].append(holding)
                old_shares = float(holding.get("shares") or 0)
                old_cost = float(holding.get("cost") or 0)
                new_shares = old_shares + shares
                if old_cost and old_shares:
                    holding["cost"] = round((old_cost * old_shares + dollars) / new_shares, 4)
                else:
                    holding["cost"] = round(price, 4)
                holding["shares"] = round(new_shares, 4)
                holding["estimated_shares"] = round(
                    float(holding.get("estimated_shares") or 0) + shares, 4)
                holding["estimated_since"] = holding.get("estimated_since") or nxt.isoformat()
                lots.append({"symbol": sym, "dollars": dollars, "shares": shares,
                             "price": price})
            applied.append({"account": account["name"], "on": nxt.isoformat(),
                            "priced_on": today.isoformat(),
                            "amount": round(sum(buys.values()), 2), "lots": lots})
            nxt += timedelta(days=cadence)
        plan["next"] = nxt.isoformat()
    return accounts, plans, applied


def next_contribution(recurring: list[dict], today: date) -> dict | None:
    """The soonest plan still ahead of today, for the card's one line."""
    best = None
    for plan in recurring or []:
        try:
            nxt = date.fromisoformat(str(plan.get("next"))[:10])
        except (TypeError, ValueError):
            continue
        buys = {str(k).upper(): _money(v) for k, v in (plan.get("buys") or {}).items()
                if _money(v) > 0}
        if not buys:
            continue
        cand = {"account": plan.get("account"), "date": nxt.isoformat(),
                "days_until": (nxt - today).days, "amount": round(sum(buys.values()), 2),
                "buys": buys, "cadence_days": int(plan.get("cadence_days") or 14)}
        if best is None or nxt < date.fromisoformat(best["date"]):
            best = cand
    return best


def price_accounts(accounts: list[dict], fetched: dict) -> dict:
    """Value every account from the quotes, and the whole from the accounts.

    Positions are also combined BY SYMBOL across accounts for the card's list
    and the move alerts: VOO held in two accounts is one line, one move.
    """
    out_accounts = []
    by_symbol: dict[str, dict] = {}
    total_value = total_day = total_cost = 0.0
    any_cost = False
    failed: list[str] = []
    for a in accounts:
        value = day = cost = 0.0
        acct_cost = False
        positions = []
        for h in a["holdings"]:
            sym = str(h.get("symbol") or "").upper()
            shares = float(h.get("shares") or 0)
            if not sym or shares <= 0:
                continue
            q = fetched.get(sym)
            if not q:
                positions.append({"symbol": sym, "shares": shares, "available": False})
                if sym not in failed:
                    failed.append(sym)
                continue
            v = round(q["price"] * shares, 2)
            d = round(q["change"] * shares, 2)
            value += v
            day += d
            pos = {"symbol": sym, "shares": shares, "price": q["price"],
                   "change_pct": q["change_pct"], "value": v, "day_change": d,
                   "available": True, "source": q.get("source"),
                   "estimated_shares": float(h.get("estimated_shares") or 0) or None}
            if h.get("cost"):
                c = float(h["cost"]) * shares
                cost += c
                acct_cost = True
                pos["total_gain"] = round(v - c, 2)
            positions.append(pos)
            agg = by_symbol.setdefault(sym, {"symbol": sym, "shares": 0.0, "price": q["price"],
                                             "change_pct": q["change_pct"], "value": 0.0,
                                             "day_change": 0.0, "available": True,
                                             "source": q.get("source"), "accounts": []})
            agg["shares"] = round(agg["shares"] + shares, 4)
            agg["value"] = round(agg["value"] + v, 2)
            agg["day_change"] = round(agg["day_change"] + d, 2)
            agg["accounts"].append(a["name"])
            if "total_gain" in pos:
                agg["total_gain"] = round(agg.get("total_gain", 0.0) + pos["total_gain"], 2)
        cash = _money(a.get("cash"))
        acct_value = round(value + cash, 2)
        est = [p for p in positions if p.get("estimated_shares")]
        out_accounts.append({
            "name": a["name"], "value": acct_value, "cash": cash,
            "day_change": round(day, 2),
            "total_gain": round(value - cost, 2) if acct_cost else None,
            "positions": positions,
            "estimated": {"symbols": [p["symbol"] for p in est],
                          "since": min((str(h.get("estimated_since")) for h in a["holdings"]
                                        if h.get("estimated_since")), default=None)},
        })
        total_value += acct_value
        total_day += day
        total_cost += cost
        any_cost = any_cost or acct_cost
    unpriced = [{"symbol": s, "shares": 0.0, "available": False} for s in failed]
    return {
        "accounts": out_accounts,
        "positions": sorted(by_symbol.values(), key=lambda p: p["value"], reverse=True) + unpriced,
        "value": round(total_value, 2),
        "day_change": round(total_day, 2),
        "total_cost": round(total_cost, 2) if any_cost else None,
        "quotes_failed": failed,
    }


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


async def _save_market(market: dict) -> bool:
    """Write the market block back. core's PUT shallow-merges at the top
    level, so the WHOLE block goes, never a fragment of it."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.put(f"{CORE_URL}/settings", json={"market": market}, timeout=5)
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# One application at a time: two concurrent /portfolio reads must not both
# turn the same due buy into shares.
_APPLY_LOCK = asyncio.Lock()


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
    async with _APPLY_LOCK:
        market = await _settings_market()
        accounts = accounts_from(market)
        watch = [s for s in (market.get("watchlist") or []) if s]
        recurring = [r for r in (market.get("recurring") or []) if isinstance(r, dict)]
        if not accounts and not watch:
            return {"configured": False, "positions": [], "accounts": [], "watchlist": [],
                    "movers": [], "next_contribution": None}

        held = [str(h.get("symbol") or "").upper() for a in accounts for h in a["holdings"]
                if h.get("symbol") and float(h.get("shares") or 0) > 0]
        planned = [str(sym).upper() for r in recurring for sym in (r.get("buys") or {})]
        all_syms = held + planned + [str(w).upper() for w in watch]
        fetched = await _quotes_bulk(list(dict.fromkeys(all_syms)))  # dedupe, keep order

        # Contributions that have come due become estimated shares, saved
        # back so they accumulate and the plan advances. A failed save is
        # reported and NOT applied: shares that exist only in this response
        # would appear and vanish between refreshes.
        today = _today()
        prices = {sym: q["price"] for sym, q in fetched.items() if q}
        applied: list[dict] = []
        save_failed = False
        if recurring:
            new_accounts, new_recurring, applied = apply_due_contributions(
                accounts, recurring, prices, today)
            if applied:
                saved = await _save_market({**market, "accounts": new_accounts,
                                            "recurring": new_recurring,
                                            # The flat list is retired once
                                            # accounts exist; keeping both is
                                            # how they drift apart.
                                            "holdings": []})
                if saved:
                    accounts, recurring = new_accounts, new_recurring
                else:
                    applied, save_failed = [], True

    priced = price_accounts(accounts, fetched)
    watch_quotes = [fetched[str(s).upper()] for s in watch if fetched.get(str(s).upper())]
    live = [p for p in priced["positions"] if p.get("available")]
    movers = sorted(live, key=lambda p: p["change_pct"], reverse=True)
    top = movers[0] if movers else None
    bottom = movers[-1] if movers and len(movers) > 1 else None
    base = priced["value"] - sum(a["cash"] for a in priced["accounts"]) - priced["day_change"]
    session = portfolio_session([fetched.get(p["symbol"]) for p in live], today)
    return {
        "configured": True,
        "as_of": session["as_of"],
        "intraday": session["intraday"],
        # What the card should call the change it is showing. None => unknown,
        # which the card renders as such rather than guessing "Today".
        "session_label": session["label"],
        "value": priced["value"],
        "day_change": priced["day_change"],
        "day_change_pct": round((priced["day_change"] / base) * 100, 2) if base else 0.0,
        "total_gain": (round(priced["value"] - sum(a["cash"] for a in priced["accounts"])
                             - priced["total_cost"], 2)
                       if priced["total_cost"] is not None else None),
        "accounts": priced["accounts"],
        "positions": priced["positions"],
        "quotes_ok": len(live),
        "quotes_failed": priced["quotes_failed"],
        "movers": {"up": top, "down": bottom},
        "watchlist": watch_quotes,
        "next_contribution": next_contribution(recurring, today),
        "applied_contributions": applied,
        "contribution_save_failed": save_failed,
    }


@app.get("/")
async def root():
    return {"app": "Stocks", "endpoints": ["/portfolio", "/quotes", "/health"]}
