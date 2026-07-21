import uuid
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Schedule Service")

# In-memory calendar. Swap for Postgres later.
EVENTS: list[dict] = []


class Event(BaseModel):
    title: str
    starts_at: str  # ISO timestamp
    ends_at: str | None = None
    source: str = "manual"
    external_id: str | None = None  # e.g. the gmail message id, for dedupe


@app.get("/health")
async def health():
    return {"service": "schedule", "events": len(EVENTS)}


@app.get("/events")
async def list_events():
    ordered = sorted(EVENTS, key=lambda e: e["starts_at"])
    return {"events": ordered, "count": len(ordered)}


@app.post("/events")
async def create_event(event: Event):
    """Create an event. Idempotent on external_id so re-syncs don't duplicate."""
    if event.external_id:
        for existing in EVENTS:
            if existing.get("external_id") == event.external_id:
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
    EVENTS.append(entry)
    return {"event": entry, "created": True}


@app.get("/")
async def root():
    return {"app": "Schedule", "endpoints": ["/events", "/health"]}
