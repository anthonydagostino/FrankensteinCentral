import os
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Tasks Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    done        BOOLEAN NOT NULL DEFAULT FALSE,
    external_id TEXT UNIQUE,
    created_at  TEXT NOT NULL
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS external_id TEXT UNIQUE;
"""

class Task(BaseModel):
    title: str
    external_id: str | None = None  # e.g. a gmail message id, for dedupe on auto-add


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        await conn.execute(SCHEMA)


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


@app.get("/health")
async def health():
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT COUNT(*) FROM tasks WHERE NOT done")
        (open_count,) = await cur.fetchone()
    return {"service": "tasks", "open": open_count}


@app.get("/tasks")
async def get_tasks():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, title, done FROM tasks ORDER BY done, id DESC"
        )
        rows = await cur.fetchall()
    return {"tasks": rows, "count": len(rows)}


@app.post("/tasks")
async def add_task(task: Task):
    """Add a task. If external_id is set (auto-added from another app), this
    is idempotent so re-syncs don't create duplicates."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        if task.external_id:
            cur = await conn.execute(
                "SELECT id, title, done FROM tasks WHERE external_id = %s", (task.external_id,)
            )
            existing = await cur.fetchone()
            if existing:
                return {"added": existing, "created": False}
        cur = await conn.execute(
            "INSERT INTO tasks (title, external_id, created_at) VALUES (%(title)s, %(external_id)s, %(created_at)s) "
            "ON CONFLICT (external_id) DO NOTHING RETURNING id, title, done",
            {
                "title": task.title,
                "external_id": task.external_id,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        row = await cur.fetchone()
        if row is None:  # lost a race on external_id; return the existing winner
            cur = await conn.execute(
                "SELECT id, title, done FROM tasks WHERE external_id = %s", (task.external_id,)
            )
            return {"added": await cur.fetchone(), "created": False}
    return {"added": row, "created": True}


@app.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: int):
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "UPDATE tasks SET done = NOT done WHERE id = %s RETURNING id, title, done",
            (task_id,),
        )
        row = await cur.fetchone()
    return {"task": row}


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: int):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    return {"deleted": task_id}


@app.get("/summary")
async def summary():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT COUNT(*) FILTER (WHERE NOT done) AS open, "
            "COUNT(*) FILTER (WHERE done) AS done FROM tasks"
        )
        counts = await cur.fetchone()
        cur = await conn.execute(
            "SELECT title FROM tasks WHERE NOT done ORDER BY id LIMIT 3"
        )
        top = [r["title"] for r in await cur.fetchall()]
    return {"open": counts["open"], "done": counts["done"], "top": top}


@app.get("/")
async def root():
    return {"app": "Tasks", "endpoints": ["/tasks", "/summary", "/health"]}
