import os
import uuid
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Schedule Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    starts_at   TEXT NOT NULL,
    ends_at     TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT UNIQUE,
    created_at  TEXT NOT NULL
);
"""


class Event(BaseModel):
    title: str
    starts_at: str  # ISO timestamp
    ends_at: str | None = None
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


@app.get("/health")
async def health():
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT COUNT(*) FROM events")
        (count,) = await cur.fetchone()
    return {"service": "schedule", "events": count}


@app.get("/events")
async def list_events():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute("SELECT * FROM events ORDER BY starts_at")
        rows = await cur.fetchall()
    return {"events": rows, "count": len(rows)}


@app.post("/events")
async def create_event(event: Event):
    """Create an event. Idempotent on external_id so re-syncs don't duplicate."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        if event.external_id:
            cur = await conn.execute(
                "SELECT * FROM events WHERE external_id = %s", (event.external_id,)
            )
            existing = await cur.fetchone()
            if existing:
                return {"event": existing, "created": False}
        entry = {
            "id": str(uuid.uuid4()),
            "title": event.title,
            "starts_at": event.starts_at,
            "ends_at": event.ends_at,
            "source": event.source,
            "external_id": event.external_id,
            "created_at": datetime.utcnow().isoformat(),
        }
        cur = await conn.execute(
            """
            INSERT INTO events (id, title, starts_at, ends_at, source, external_id, created_at)
            VALUES (%(id)s, %(title)s, %(starts_at)s, %(ends_at)s, %(source)s,
                    %(external_id)s, %(created_at)s)
            ON CONFLICT (external_id) DO NOTHING
            RETURNING *
            """,
            entry,
        )
        row = await cur.fetchone()
        if row is None:  # lost a race on external_id; return the existing winner
            cur = await conn.execute(
                "SELECT * FROM events WHERE external_id = %s", (event.external_id,)
            )
            return {"event": await cur.fetchone(), "created": False}
    return {"event": row, "created": True}


@app.get("/")
async def root():
    return {"app": "Schedule", "endpoints": ["/events", "/health"]}
