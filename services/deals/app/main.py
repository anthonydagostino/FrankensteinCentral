import os
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Deals Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id          SERIAL PRIMARY KEY,
    merchant    TEXT NOT NULL,
    offer       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT UNIQUE,
    created_at  TEXT NOT NULL
);
"""


class Deal(BaseModel):
    merchant: str
    offer: str
    source: str = "manual"
    external_id: str | None = None  # e.g. the gmail message id, for dedupe


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


async def _deals() -> list[dict]:
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, merchant, offer, source, external_id, created_at "
            "FROM deals ORDER BY id DESC"
        )
        return await cur.fetchall()


@app.get("/health")
async def health():
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT COUNT(*) FROM deals")
        (count,) = await cur.fetchone()
    return {"service": "deals", "count": count}


@app.get("/deals")
async def get_deals():
    rows = await _deals()
    return {"deals": rows, "count": len(rows)}


@app.post("/deals")
async def add_deal(deal: Deal):
    """Add a deal. Idempotent on external_id so re-syncs don't duplicate."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        if deal.external_id:
            cur = await conn.execute(
                "SELECT * FROM deals WHERE external_id = %s", (deal.external_id,)
            )
            existing = await cur.fetchone()
            if existing:
                return {"deal": existing, "created": False}
        cur = await conn.execute(
            "INSERT INTO deals (merchant, offer, source, external_id, created_at) "
            "VALUES (%(merchant)s, %(offer)s, %(source)s, %(external_id)s, %(created_at)s) "
            "ON CONFLICT (external_id) DO NOTHING RETURNING *",
            {
                "merchant": deal.merchant,
                "offer": deal.offer,
                "source": deal.source,
                "external_id": deal.external_id,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        row = await cur.fetchone()
        if row is None:  # lost a race on external_id; return the existing winner
            cur = await conn.execute(
                "SELECT * FROM deals WHERE external_id = %s", (deal.external_id,)
            )
            return {"deal": await cur.fetchone(), "created": False}
    return {"deal": row, "created": True}


@app.delete("/deals/{deal_id}")
async def delete_deal(deal_id: int):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM deals WHERE id = %s", (deal_id,))
    return {"deleted": deal_id}


@app.get("/summary")
async def summary():
    rows = await _deals()
    return {"count": len(rows), "top": [f"{d['merchant']}: {d['offer']}" for d in rows[:3]]}


@app.get("/")
async def root():
    return {"app": "Deals", "endpoints": ["/deals", "/summary", "/health"]}
