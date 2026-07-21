import os
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Budget Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    limit_amount NUMERIC NOT NULL DEFAULT 0,
    spent        NUMERIC NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""

# Seed a typical monthly budget (one is intentionally over, to show the alert).
SEED = [
    ("Groceries", 500, 320),
    ("Dining out", 200, 240),
    ("Transport", 150, 90),
    ("Entertainment", 100, 60),
    ("Shopping", 200, 110),
]


class Category(BaseModel):
    name: str
    limit: float = 0


class Spend(BaseModel):
    amount: float = 0


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
        cur = await conn.execute("SELECT COUNT(*) FROM budget")
        (count,) = await cur.fetchone()
        if count == 0:
            for name, lim, spent in SEED:
                await conn.execute(
                    "INSERT INTO budget (name, limit_amount, spent, created_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (name, lim, spent, datetime.utcnow().isoformat()),
                )


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


async def _categories() -> list[dict]:
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, name, limit_amount, spent FROM budget ORDER BY id"
        )
        return await cur.fetchall()


def _summarize(cats: list[dict]) -> dict:
    total_budget = round(sum(float(c["limit_amount"]) for c in cats), 2)
    total_spent = round(sum(float(c["spent"]) for c in cats), 2)
    over = [
        c["name"] for c in cats if float(c["spent"]) > float(c["limit_amount"])
    ]
    pct = round(100 * total_spent / total_budget) if total_budget else 0
    return {
        "total_budget": total_budget,
        "total_spent": total_spent,
        "remaining": round(total_budget - total_spent, 2),
        "percent_used": pct,
        "over_budget": over,
    }


@app.get("/health")
async def health():
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM budget")
        (count,) = await cur.fetchone()
    return {"service": "budget", "categories": count}


@app.get("/categories")
async def get_categories():
    cats = await _categories()
    return {"categories": cats, "count": len(cats)}


@app.post("/categories")
async def add_category(cat: Category):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO budget (name, limit_amount, created_at) VALUES (%s, %s, %s) "
            "RETURNING id",
            (cat.name, cat.limit, datetime.utcnow().isoformat()),
        )
        (new_id,) = await cur.fetchone()
    return {"added": {"id": new_id, "name": cat.name, "limit": cat.limit, "spent": 0}}


@app.post("/categories/{cat_id}/spend")
async def add_spend(cat_id: int, spend: Spend):
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "UPDATE budget SET spent = spent + %s WHERE id = %s "
            "RETURNING id, name, limit_amount, spent",
            (spend.amount, cat_id),
        )
        row = await cur.fetchone()
    return {"category": row}


@app.get("/summary")
async def summary():
    return _summarize(await _categories())


@app.get("/")
async def root():
    return {"app": "Budget", "endpoints": ["/categories", "/summary", "/health"]}
