import os
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Finance Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL,
    amount    NUMERIC NOT NULL DEFAULT 0,
    due_day   INT NOT NULL DEFAULT 1,
    category  TEXT NOT NULL DEFAULT 'other',
    created_at TEXT NOT NULL
);
"""

class Bill(BaseModel):
    name: str
    amount: float = 0
    due_day: int = 1
    category: str = "other"


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


def days_until(due_day: int, today: int) -> int:
    """Days from today's day-of-month to the bill's due day (rough month wrap)."""
    d = due_day - today
    return d if d >= 0 else d + 30


async def _bills() -> list[dict]:
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, name, amount, due_day, category FROM bills ORDER BY due_day"
        )
        return await cur.fetchall()


@app.get("/health")
async def health():
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT COUNT(*) FROM bills")
        (count,) = await cur.fetchone()
    return {"service": "finance", "bills": count}


@app.get("/bills")
async def get_bills():
    bills = await _bills()
    return {"bills": bills, "count": len(bills)}


@app.post("/bills")
async def add_bill(bill: Bill):
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO bills (name, amount, due_day, category, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (bill.name, bill.amount, bill.due_day, bill.category, datetime.utcnow().isoformat()),
        )
        (new_id,) = await cur.fetchone()
    return {"added": {"id": new_id, **bill.model_dump()}}


@app.get("/summary")
async def summary():
    """Monthly spend + which bills are coming due soon."""
    bills = await _bills()
    today = datetime.utcnow().day
    monthly_total = round(sum(float(b["amount"]) for b in bills), 2)
    dated = sorted(
        ({**b, "days_until": days_until(b["due_day"], today)} for b in bills),
        key=lambda b: b["days_until"],
    )
    upcoming = [b for b in dated if b["days_until"] <= 7]
    return {
        "monthly_total": monthly_total,
        "bill_count": len(bills),
        "upcoming": upcoming,
        "next_due": dated[0] if dated else None,
    }


@app.get("/")
async def root():
    return {"app": "Finance", "endpoints": ["/bills", "/summary", "/health"]}
