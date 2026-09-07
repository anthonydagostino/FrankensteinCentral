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
import time
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


def _today() -> date:
    """Today in the user's timezone — the one clock this service uses.

    Every date window here goes through this function so tests can pin the
    date and exercise month boundaries. The bugs that motivated it were only
    reachable on specific days (the 1st of a month, and the UTC-vs-local
    window in the small hours), so "it worked yesterday" is not evidence.
    """
    return datetime.now(LOCAL_TZ).date()


def _connected() -> bool:
    return bool(FIREFLY_URL and FIREFLY_TOKEN)


def _headers() -> dict:
    return {"Authorization": f"Bearer {FIREFLY_TOKEN}", "Accept": "application/json"}


# Firefly III is a PHP app on a home box: every call here is real work for it,
# and the homepage aggregator gives this service an 8-second budget before it
# gives up and reports "not connected". A short TTL keeps repeated page loads
# and the budget service's 60s poll from re-querying the same window.
_TTL_CACHE: dict = {}


async def _cached(key: str, ttl: float, fn):
    hit = _TTL_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = await fn()
    _TTL_CACHE[key] = (now, val)
    return val


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


async def _read(client, path: str, params: dict, failures: list) -> dict | list | None:
    """One upstream read. Returns None instead of raising, and records what
    failed: a single bad Firefly endpoint must degrade its own section, never
    take the whole dashboard down (which the homepage then renders as the
    flatly wrong "Firefly not connected")."""
    try:
        r = await client.get(f"{FIREFLY_URL}{path}", params=params,
                             headers=_headers(), timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{path.rsplit('/', 1)[-1]}: {type(exc).__name__}")
        return None


def _month_window(today: date) -> tuple[str, str]:
    """The month-to-date range to ask Firefly for, as (start, end).

    Firefly rejects a zero-length range with 422, and start==end is exactly
    what "first of month -> today" produces on the 1st. So the end is always
    at least one day past the start. Month-to-date totals are unaffected:
    there is no future spending to include. Pure and unit-tested across a
    two-year calendar (services/firefly/tests/test_date_windows.py).
    """
    month_start = today.replace(day=1)
    return month_start.isoformat(), max(today, month_start + timedelta(days=1)).isoformat()


async def _live() -> dict:
    # LOCAL_TZ, not the container's UTC clock: for the first hours of each day
    # UTC is already tomorrow, which pointed this at the wrong month entirely.
    today = _today()
    start, end = _month_window(today)
    cat_start = (today - timedelta(days=30)).isoformat()  # pie = trailing 30 days
    failures: list[str] = []
    async with httpx.AsyncClient() as client:
        summary = await _read(client, "/api/v1/summary/basic",
                              {"start": start, "end": end}, failures)
        asset = await _read(client, "/api/v1/accounts", {"type": "asset"}, failures)
        liab = await _read(client, "/api/v1/accounts", {"type": "liability"}, failures)
        tx = await _read(client, "/api/v1/transactions", {"limit": 10}, failures)
        cat = await _read(client, "/api/v1/insight/expense/category",
                          {"start": cat_start, "end": end}, failures)

    # Everything failed => Firefly really is unreachable. Say so loudly rather
    # than returning an empty payload that would read as "you have nothing".
    if len(failures) == 5:
        raise RuntimeError("; ".join(failures))

    summary = summary or {}
    net = _pick(summary, "net-worth-in-") or {}
    accounts = _accts(asset or {}, "asset")
    liabilities = _accts(liab or {}, "liability")
    recent = []
    for g in (tx or {}).get("data", []):
        splits = g.get("attributes", {}).get("transactions", [])
        if not splits:
            continue
        t = splits[0]
        recent.append({"desc": t.get("description", ""), "amount": t.get("amount", "0"),
                       "type": t.get("type", ""), "date": (t.get("date") or "")[:10],
                       "currency": t.get("currency_code", "")})
    categories = []
    for c in (cat if isinstance(cat, list) else []):
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
        "degraded": failures or None,
    }


async def _data() -> dict:
    if not _connected():
        return dict(EMPTY)
    return await _cached("live", 45, _live)


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


async def _fetch_txns(client, txn_type: str, start: str, end: str,
                      max_pages: int = 6) -> list[dict]:
    """All splits of one type in [start, end], paging through Firefly.
    Transfers are never fetched here — moving money between your own accounts
    is not spending or income."""
    out, page = [], 1
    while page <= max_pages:
        r = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                             params={"type": txn_type, "start": start, "end": end,
                                     "limit": 50, "page": page},
                             headers=_headers(), timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        for g in data:
            gat = g.get("attributes", {})
            # created_at is the Firefly-server timestamp of when this record
            # ENTERED the ledger (a CSV import of an old-dated bank transaction
            # still creates the record today) — the honest "last import" signal.
            # updated_at is deliberately NOT used: editing a category or
            # description on an old transaction bumps it, and an edit is not
            # an import — it must never revive stale budget guidance.
            ingested = (gat.get("created_at") or "")[:10]
            for t in gat.get("transactions", []):
                try:
                    amt = abs(float(t.get("amount") or 0))
                except (TypeError, ValueError):
                    amt = 0
                out.append({"desc": t.get("description", ""), "amount": round(amt, 2),
                            "date": (t.get("date") or "")[:10],
                            "type": t.get("type", txn_type),
                            "ingested": ingested,
                            "category": t.get("category_name") or "Uncategorized"})
        if len(data) < 50:
            break
        page += 1
    return out


async def _fetch_withdrawals(client, start: str, end: str) -> list[dict]:
    return await _fetch_txns(client, "withdrawal", start, end)


async def _ingest_latest(client, txns: list[dict]) -> date | None:
    """When transaction data last ENTERED the ledger (synchronization
    recency) — distinct from the newest transaction date (spending recency).

    Evidence: transaction created_at ONLY. Firefly sets created_at when the
    record is written (so importing an old-dated bank transaction today gives
    created_at=today, date=20 days ago — exactly "imported today, activity 20
    days ago"), and never changes it afterward. Reading Firefly can't touch
    it, so a query can't fake freshness. We deliberately reject the two
    look-alike signals: transaction updated_at (bumped by ordinary edits —
    recategorizing an old transaction is not an import) and account
    updated_at (bumped by metadata changes with no financial ingestion).

    Sources: created_at on the date-windowed transactions passed in, plus a
    probe of the newest-dated transactions ledger-wide — catches an import
    whose transactions are all dated outside the queried window. Firefly's
    API can't sort by created_at, so this is the strongest evidence it
    exposes; if none exists the caller falls back to conservative
    activity-based labeling rather than guessing."""
    best = None
    for t in txns:
        i = t.get("ingested")
        if i and (best is None or i > best):
            best = i
    try:
        r = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                             params={"limit": 10}, headers=_headers(), timeout=15)
        r.raise_for_status()
        for g in r.json().get("data", []):
            c = (g.get("attributes", {}).get("created_at") or "")[:10]
            if c and (best is None or c > best):
                best = c
    except Exception:  # noqa: BLE001
        pass
    try:
        return date.fromisoformat(best) if best else None
    except ValueError:
        return None


async def _ledger_latest(client) -> date | None:
    """Date of the newest transaction of ANY type — the ledger's true
    freshness. Firefly returns transactions newest-first."""
    try:
        r = await client.get(f"{FIREFLY_URL}/api/v1/transactions",
                             params={"limit": 1}, headers=_headers(), timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        splits = data[0].get("attributes", {}).get("transactions", [])
        raw = (splits[0].get("date") or "")[:10] if splits else ""
        return date.fromisoformat(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


async def _spending() -> dict:
    today = _today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    # last-month "to date" cutoff = same day-of-month as today (for pace)
    lm_cutoff = last_month_start + timedelta(days=(today.day - 1))
    # Rolling windows for the homepage's general spending signal. These are
    # deliberately SEPARATE from the calendar-month figures above: budgets are
    # monthly and stay monthly; "past 30 days" is a trailing window.
    w30_start = today - timedelta(days=29)          # inclusive, 30 days
    prev30_start = today - timedelta(days=59)
    prev30_end = today - timedelta(days=30)
    fetch_start = min(last_month_start, prev30_start)
    async with httpx.AsyncClient() as client:
        wd = await _fetch_withdrawals(client, fetch_start.isoformat(), today.isoformat())
        ledger_latest = await _ledger_latest(client)
        ingest_latest = await _ingest_latest(client, wd)

    def d(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    today_sum = week_sum = month_sum = lm_to_date = 0.0
    w30_sum = prev30_sum = 0.0
    biggest_today = None
    for t in wd:
        dt = d(t["date"])
        if dt is None:
            continue
        if w30_start <= dt <= today:
            w30_sum += t["amount"]
        elif prev30_start <= dt <= prev30_end:
            prev30_sum += t["amount"]
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
    latest_wd = max(all_dates) if all_dates else None
    # True ledger freshness: newest transaction of ANY type (a fresh deposit
    # means the ledger is being updated even if there are no recent withdrawals).
    freshness_ref = ledger_latest or latest_wd
    # clamp: future-dated transactions (Firefly allows them) must not go negative
    days_stale = max(0, (today - freshness_ref).days) if freshness_ref else None

    if not lm_to_date:
        baseline, pace_note = "none", "no last-month spending in the ledger to compare against"
    elif earliest and earliest > last_month_start + timedelta(days=5):
        baseline = "partial_history"
        pace_note = f"ledger history starts {earliest.isoformat()}, so last month is incomplete"
    elif days_stale is not None and days_stale >= 7:
        baseline = "stale_data"
        pace_note = f"ledger hasn't been updated in {days_stale} days, so this month is incomplete"
    else:
        baseline, pace_note = "ok", None
    pace_pct = (round(((month_sum - lm_to_date) / lm_to_date) * 100)
                if baseline == "ok" else None)

    # ---- trailing-30-day signal (homepage) -------------------------------
    # Coverage rule, same standard as the month-over-month pace: only offer the
    # vs-previous comparison when the ledger actually spans the earlier window.
    # A ledger that starts mid-window would make spending look like it fell.
    prev30_covered = bool(earliest and earliest <= prev30_start)
    if not prev30_covered:
        w30_trend = None
        w30_note = ("ledger history starts "
                    f"{earliest.isoformat() if earliest else 'unknown'}, so the "
                    "previous 30 days aren't fully covered")
    elif prev30_sum <= 0:
        w30_trend = None
        w30_note = "no spending in the previous 30 days to compare against"
    elif days_stale is not None and days_stale >= 7:
        w30_trend = None
        w30_note = (f"ledger hasn't been updated in {days_stale} days, so the "
                    "current 30 days are incomplete")
    else:
        w30_trend = round(((w30_sum - prev30_sum) / prev30_sum) * 100)
        w30_note = None

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
        "latest_txn": latest_wd.isoformat() if latest_wd else None,
        "ledger_latest_txn": ledger_latest.isoformat() if ledger_latest else None,
        "days_stale": days_stale,
        "ingest_latest": ingest_latest.isoformat() if ingest_latest else None,
        "ingest_days": (max(0, (today - ingest_latest).days) if ingest_latest else None),
        "month_ingested": bool(ingest_latest and ingest_latest >= month_start),
        "daily_avg": daily_avg, "biggest_today": biggest_today, "recent": recent,
        # Rolling window for the homepage. Calendar-month values above are what
        # the monthly budgets use; these two must never be mixed.
        "last_30": round(w30_sum, 2),
        "prev_30": round(prev30_sum, 2) if prev30_covered else None,
        "last_30_trend_pct": w30_trend,
        "last_30_note": w30_note,
        "last_30_window": {"start": w30_start.isoformat(), "end": today.isoformat()},
        # The window is only as current as the ledger; the UI qualifies rather
        # than presenting a partial window as a complete one.
        "last_30_through": (ledger_latest.isoformat() if ledger_latest else None),
    }


@app.get("/spending")
async def spending():
    if not _connected():
        return {"connected": False, "today": None, "week": None, "month": None,
                "pace_pct": None, "recent": [], "biggest_today": None}
    try:
        return await _cached("spending", 45, _spending)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)


async def _month_payload() -> dict:
    """Raw material for the budget engine: this month's withdrawals and
    categorized deposits (refunds/credits), per-category nets, income, and
    ledger freshness. Transfers are excluded entirely."""
    import calendar
    today = _today()
    month_start = today.replace(day=1)
    days_total = calendar.monthrange(today.year, today.month)[1]
    async with httpx.AsyncClient() as client:
        wd = await _fetch_txns(client, "withdrawal", month_start.isoformat(), today.isoformat())
        dep = await _fetch_txns(client, "deposit", month_start.isoformat(), today.isoformat())
        ledger_latest = await _ledger_latest(client)
        ingest_latest = await _ingest_latest(client, wd + dep)
    days_stale = max(0, (today - ledger_latest).days) if ledger_latest else None
    ingest_days = max(0, (today - ingest_latest).days) if ingest_latest else None

    categories: dict[str, dict] = {}
    for t in wd:
        c = categories.setdefault(t["category"], {"spent": 0.0, "refunds": 0.0, "count": 0})
        c["spent"] += t["amount"]
        c["count"] += 1
    # A categorized deposit is treated as a refund/credit against that
    # category. Uncategorized deposits are income, not refunds.
    income = 0.0
    for t in dep:
        if t["category"] != "Uncategorized":
            c = categories.setdefault(t["category"], {"spent": 0.0, "refunds": 0.0, "count": 0})
            c["refunds"] += t["amount"]
        else:
            income += t["amount"]
    for c in categories.values():
        c["spent"] = round(c["spent"], 2)
        c["refunds"] = round(c["refunds"], 2)
        c["net"] = round(c["spent"] - c["refunds"], 2)

    txns = sorted(
        [{**t, "amount": -t["amount"]} for t in wd]
        + [{**t, "amount": t["amount"]} for t in dep if t["category"] != "Uncategorized"],
        key=lambda t: t["date"], reverse=True)
    return {
        "connected": True,
        "tz": str(LOCAL_TZ),
        "today": today.isoformat(),
        "month": {"label": today.strftime("%B %Y"), "start": month_start.isoformat(),
                  "days_total": days_total, "days_elapsed": today.day,
                  "days_left": days_total - today.day},
        "days_stale": days_stale,
        "ledger_latest_txn": ledger_latest.isoformat() if ledger_latest else None,
        "ingest_latest": ingest_latest.isoformat() if ingest_latest else None,
        "ingest_days": ingest_days,
        # Nothing imported since the month began => we have NO evidence about
        # this month's spending. A fresh month with a stale ledger computes to
        # $0, which is arithmetic, not knowledge: consumers must show unknown.
        "month_ingested": bool(ingest_latest and ingest_latest >= month_start),
        "importer_url": FIREFLY_IMPORTER_URL or None,
        "categories": categories,
        "income_month": round(income, 2),
        "transactions": txns[:400],
    }


@app.get("/month")
async def month():
    if not _connected():
        return {"connected": False}
    try:
        return await _cached("month", 45, _month_payload)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)


@app.get("/bills")
async def bills():
    """Firefly's own bills, normalized — Firefly stays the source of truth for
    fixed costs. `supported:false` means the user hasn't set bills up there."""
    if not _connected():
        return {"connected": False, "supported": False, "items": []}
    try:
        today = _today()
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{FIREFLY_URL}/api/v1/bills",
                                 headers=_headers(), timeout=20)
            r.raise_for_status()
            data = r.json().get("data", [])
        items = []
        for b in data:
            at = b.get("attributes", {})
            if at.get("active") is False:
                continue
            try:
                lo = float(at.get("amount_min") or 0)
                hi = float(at.get("amount_max") or lo)
                amount = round((lo + hi) / 2, 2)
            except (TypeError, ValueError):
                amount = None
            nxt = (at.get("next_expected_match") or "")[:10]
            paid_dates = [p.get("date", "")[:10] for p in (at.get("paid_dates") or [])]
            paid_this_month = any(p[:7] == today.isoformat()[:7] for p in paid_dates if p)
            items.append({"name": at.get("name", ""), "amount": amount,
                          "next_due": nxt or None, "paid_this_month": paid_this_month})
        items.sort(key=lambda b: b.get("next_due") or "9999")
        return {"connected": True, "supported": len(items) > 0, "items": items}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "firefly unreachable", "detail": str(exc)}, status_code=502)


@app.get("/audit")
async def audit():
    """Real-data quality audit for budgeting: history span, categorization
    quality, category totals, and whether Firefly budgets/bills exist. Reads
    up to ~600 transactions over the last year; metadata only."""
    if not _connected():
        return {"connected": False}
    try:
        today = _today()
        year_ago = today - timedelta(days=365)
        async with httpx.AsyncClient() as client:
            wd = await _fetch_txns(client, "withdrawal", year_ago.isoformat(),
                                   today.isoformat(), max_pages=10)
            dep = await _fetch_txns(client, "deposit", year_ago.isoformat(),
                                    today.isoformat(), max_pages=4)
            ledger_latest = await _ledger_latest(client)
            fb = await client.get(f"{FIREFLY_URL}/api/v1/budgets",
                                  headers=_headers(), timeout=15)
            firefly_budgets = ([b.get("attributes", {}).get("name", "")
                                for b in fb.json().get("data", [])]
                               if fb.status_code == 200 else [])
            bl = await client.get(f"{FIREFLY_URL}/api/v1/bills",
                                  headers=_headers(), timeout=15)
            bill_count = (len(bl.json().get("data", []))
                          if bl.status_code == 200 else 0)
            ingest_latest = await _ingest_latest(client, wd + dep)

        dates = sorted(t["date"] for t in wd if t["date"])
        categorized = [t for t in wd if t["category"] != "Uncategorized"]
        cat_totals: dict[str, float] = {}
        for t in wd:
            cat_totals[t["category"]] = round(cat_totals.get(t["category"], 0) + t["amount"], 2)
        top = sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)
        uncat_amt = cat_totals.get("Uncategorized", 0.0)
        total_amt = sum(cat_totals.values())
        return {
            "connected": True,
            "window": {"from": year_ago.isoformat(), "to": today.isoformat()},
            "withdrawals": len(wd),
            "deposits": len(dep),
            "span": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None},
            "categorized_pct": round(100 * len(categorized) / len(wd)) if wd else None,
            "uncategorized": {"count": len(wd) - len(categorized),
                              "amount": round(uncat_amt, 2),
                              "pct_of_spend": round(100 * uncat_amt / total_amt) if total_amt else 0},
            "categories": [{"name": k, "total": v} for k, v in top[:15]],
            "category_count": len([k for k in cat_totals if k != "Uncategorized"]),
            "firefly_budgets": firefly_budgets,
            "firefly_bill_count": bill_count,
            "days_stale": max(0, (today - ledger_latest).days) if ledger_latest else None,
            "ingest_latest": ingest_latest.isoformat() if ingest_latest else None,
            "ingest_days": max(0, (today - ingest_latest).days) if ingest_latest else None,
        }
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
