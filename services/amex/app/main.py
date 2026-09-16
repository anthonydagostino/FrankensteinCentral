import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from . import credits

app = FastAPI(title="Amex Rewards Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))

SCHEMA = """
-- One row per credit per period. The period is part of the key, so marking
-- September's Dunkin' credit used says nothing about October's, and marking
-- the same one twice is a no-op rather than a double count.
CREATE TABLE IF NOT EXISTS amex_redemptions (
    credit_key TEXT NOT NULL,
    period_id  TEXT NOT NULL,
    amount     NUMERIC,
    noted_at   TEXT NOT NULL,
    PRIMARY KEY (credit_key, period_id)
);
"""


class Redemption(BaseModel):
    credit_key: str
    used: bool = True
    # Optional: what you actually drew, when it was less than the full credit.
    amount: float | None = None


def _today():
    """The local calendar date. Every period boundary in credits.py is a
    calendar boundary, so the whole service turns on this one line — and it is
    the only place the clock is read."""
    return datetime.now(LOCAL_TZ).date()


# Why the startup below cannot raise, when every sibling service's can.
#
# On 2026-09-16 this service was deployed and the dashboard went down. The
# proximate cause was a missing import in the assistant, but the deploy
# machinery made it worse in a way worth fixing on its own: a container that
# dies during startup crash-loops, `stack-health.sh` correctly refuses to call
# that healthy, `deployed.json` keeps the previous commit, and `autopull.sh`
# then re-runs the entire deploy — four-minute test suite included — on every
# tick, forever.
#
# So a database this service cannot reach becomes a DEGRADED SERVICE rather
# than a dead one. That is only honest because of what /summary does with it:
# the catalogue is static and could be served happily, but which credits you
# already used lives in the table, and a card that cannot read the table but
# says "you have $340 unused" is telling you to go spend money you already
# spent. It reports `unreachable` instead, which is the state the dashboard
# already renders as "we could not look".
_DB_ERROR = None


@app.on_event("startup")
async def startup():
    global _DB_ERROR
    try:
        await pool.open(wait=True, timeout=30)
        async with pool.connection() as conn:
            conn.row_factory = tuple_row
            await conn.execute(SCHEMA)
        _DB_ERROR = None
    except Exception as exc:                            # noqa: BLE001
        # Deliberately broad: the point is that NOTHING here reaches uvicorn.
        _DB_ERROR = f"{type(exc).__name__}: {exc}"


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


@app.get("/health")
async def health():
    """Serves even when the database does not, and says which."""
    if _DB_ERROR:
        return {"service": "amex", "db": "unreachable", "reason": _DB_ERROR,
                "redemptions": None,
                "catalogue_checked": credits.CATALOGUE_CHECKED}
    try:
        async with pool.connection() as conn:
            conn.row_factory = tuple_row
            cur = await conn.execute("SELECT COUNT(*) FROM amex_redemptions")
            (count,) = await cur.fetchone()
    except Exception as exc:                            # noqa: BLE001
        return {"service": "amex", "db": "unreachable", "reason": f"{type(exc).__name__}",
                "redemptions": None,
                "catalogue_checked": credits.CATALOGUE_CHECKED}
    return {"service": "amex", "db": "ok", "redemptions": count,
            "catalogue_checked": credits.CATALOGUE_CHECKED}


async def _redemptions():
    """Every mark ever made, as {credit_key, period_id, amount}.

    Read whole rather than filtered: the table holds one row per credit per
    period and both callers below want a different slice of it, so one query
    beats two shapes to keep in step.
    """
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT credit_key, period_id, amount FROM amex_redemptions")
        return await cur.fetchall()


@app.get("/summary")
async def summary(card: str | None = None):
    """Every credit's state today, sorted by what expires soonest.

    The year-to-date block rides along rather than living at its own endpoint:
    it comes from the same rows, and the dashboard shows both together, so a
    second round trip would only buy a second thing to keep consistent.
    """
    cards = (card,) if card in credits.CARDS else ("platinum", "gold")
    try:
        rows = await _redemptions()
    except Exception:                                   # noqa: BLE001
        rows = None
    if rows is None or _DB_ERROR:
        # NOT a catalogue with everything marked unused. Which credits you have
        # already drawn lives in that table, and guessing "none of them" is the
        # one wrong answer that costs money — it sends you out to spend a
        # credit you spent last week.
        return {"state": "unreachable", "rows": [], "at_risk": None,
                "available": None, "captured_this_period": None,
                "annual_fees": None, "annual_credit_value": None,
                "ytd": None, "catalogue_checked": credits.CATALOGUE_CHECKED}

    used = {f"{r['credit_key']}:{r['period_id']}" for r in rows}
    today = _today()
    out = credits.summarise(today, used, cards)
    out["state"] = "ok"
    out["ytd"] = credits.captured_ytd(today.year, rows, cards)
    return out


@app.post("/used")
async def mark(body: Redemption):
    """Mark a credit used (or not) for the period it is in right now.

    Idempotent on (credit, period): pressing it twice is the same as once,
    which matters because this is a button on a dashboard that also refreshes
    itself.
    """
    credit = credits.BY_KEY.get(body.credit_key)
    if not credit:
        return {"error": f"unknown credit: {body.credit_key}"}
    pid = credits.period_id(credit["cadence"], _today())
    if _DB_ERROR:
        return {"error": "the redemptions store is unreachable", "saved": False,
                "credit_key": body.credit_key, "period_id": pid}
    async with pool.connection() as conn:
        if body.used:
            await conn.execute(
                """INSERT INTO amex_redemptions (credit_key, period_id, amount, noted_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (credit_key, period_id) DO UPDATE SET amount = EXCLUDED.amount""",
                (body.credit_key, pid, body.amount, datetime.now(LOCAL_TZ).isoformat()),
            )
        else:
            await conn.execute(
                "DELETE FROM amex_redemptions WHERE credit_key = %s AND period_id = %s",
                (body.credit_key, pid),
            )
    return {"credit_key": body.credit_key, "period_id": pid, "used": body.used}


@app.get("/")
async def root():
    return {"app": "Amex Rewards",
            "endpoints": ["/summary", "/used", "/health"]}
