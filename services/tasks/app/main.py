import os
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Tasks Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         SERIAL PRIMARY KEY,
    title      TEXT NOT NULL,
    done       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TEXT NOT NULL
);
"""

SEED = ["Renew passport", "Reply to landlord", "Book dentist"]


class Task(BaseModel):
    title: str


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
        cur = await conn.execute("SELECT COUNT(*) FROM tasks")
        (count,) = await cur.fetchone()
        if count == 0:
            for title in SEED:
                await conn.execute(
                    "INSERT INTO tasks (title, created_at) VALUES (%s, %s)",
                    (title, datetime.utcnow().isoformat()),
                )


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


@app.get("/health")
async def health():
    async with pool.connection() as conn:
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
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO tasks (title, created_at) VALUES (%s, %s) RETURNING id",
            (task.title, datetime.utcnow().isoformat()),
        )
        (new_id,) = await cur.fetchone()
    return {"added": {"id": new_id, "title": task.title, "done": False}}


@app.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE tasks SET done = NOT done WHERE id = %s RETURNING id, title, done",
            (task_id,),
        )
        row = await cur.fetchone()
    return {"task": row}


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
