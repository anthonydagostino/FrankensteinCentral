from datetime import date, datetime

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Fitness Service")

# In-memory store for now. Swap for Postgres later.
VISITS: list[dict] = []

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


@app.get("/health")
async def health():
    return {"service": "fitness", "visits_logged": len(VISITS)}


@app.get("/visits")
async def get_visits():
    return {"visits": VISITS, "count": len(VISITS)}


@app.post("/visits")
async def log_visit(visit: Visit):
    entry = {
        "when": (visit.when or datetime.utcnow()).isoformat(),
        "note": visit.note,
    }
    VISITS.append(entry)
    return {"logged": entry, "total": len(VISITS)}


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
