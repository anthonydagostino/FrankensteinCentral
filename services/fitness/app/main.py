import os
from datetime import date, datetime

from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Fitness Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id      SERIAL PRIMARY KEY,
    when_at TEXT NOT NULL,
    note    TEXT NOT NULL DEFAULT ''
);
"""

WEEKLY_PLAN = {
    "Monday": {"focus": "Push", "lifts": ["Bench", "Overhead press", "Triceps"]},
    "Tuesday": {"focus": "Pull", "lifts": ["Deadlift", "Rows", "Curls"]},
    "Wednesday": {"focus": "Rest / Zone 2 cardio", "lifts": []},
    "Thursday": {"focus": "Legs", "lifts": ["Squat", "RDL", "Calves"]},
    "Friday": {"focus": "Upper", "lifts": ["Incline bench", "Pull-ups", "Lateral raise"]},
    "Saturday": {"focus": "Conditioning", "lifts": ["Sled", "Carries"]},
    "Sunday": {"focus": "Rest", "lifts": []},
}

GROCERY_LIST = [
    {"item": "Chicken breast", "qty": "2 kg", "for": "protein"},
    {"item": "Rice", "qty": "2 kg", "for": "carbs"},
    {"item": "Eggs", "qty": "24", "for": "protein"},
    {"item": "Broccoli", "qty": "1 kg", "for": "micros"},
    {"item": "Olive oil", "qty": "1 bottle", "for": "fats"},
    {"item": "Greek yogurt", "qty": "1 kg", "for": "protein"},
]


class Visit(BaseModel):
    when: datetime | None = None
    note: str = ""


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
        cur = await conn.execute("SELECT COUNT(*) FROM visits")
        (count,) = await cur.fetchone()
    return {"service": "fitness", "visits_logged": count}


@app.get("/visits")
async def get_visits():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, when_at AS when, note FROM visits ORDER BY when_at DESC"
        )
        rows = await cur.fetchall()
    return {"visits": rows, "count": len(rows)}


@app.post("/visits")
async def log_visit(visit: Visit):
    when_at = (visit.when or datetime.utcnow()).isoformat()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO visits (when_at, note) VALUES (%s, %s) RETURNING id",
            (when_at, visit.note),
        )
        (new_id,) = await cur.fetchone()
        cur = await conn.execute("SELECT COUNT(*) FROM visits")
        (total,) = await cur.fetchone()
    return {"logged": {"id": new_id, "when": when_at, "note": visit.note}, "total": total}


@app.get("/plan")
async def plan():
    """Optimal training split for the week."""
    today = date.today().strftime("%A")
    return {"today": today, "today_plan": WEEKLY_PLAN.get(today), "week": WEEKLY_PLAN}


@app.get("/nutrition")
async def nutrition():
    """What to buy and eat to support the plan."""
    return {"grocery_list": GROCERY_LIST, "target_protein_g": 180}


@app.get("/")
async def root():
    return {"app": "Fitness", "endpoints": ["/visits", "/plan", "/nutrition", "/health"]}
