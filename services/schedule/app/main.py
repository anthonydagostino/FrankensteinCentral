import os
import uuid
from datetime import datetime

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from . import gcal

app = FastAPI(title="Schedule Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

VALID_STATUSES = {"pending", "countered", "confirmed", "declined"}

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
-- Additive migrations (safe to re-run): status/thread tracking so proposed
-- ("pending") slots render differently from confirmed ones, and so
-- resolving a thread (one slot confirmed) can find and clear its siblings.
ALTER TABLE events ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'confirmed';
ALTER TABLE events ADD COLUMN IF NOT EXISTS thread_id TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS gcal_event_id TEXT;
"""


class Event(BaseModel):
    title: str
    starts_at: str  # ISO timestamp
    ends_at: str | None = None
    source: str = "manual"
    external_id: str | None = None  # e.g. the gmail message id, for dedupe
    status: str = "confirmed"  # pending | countered | confirmed | declined
    thread_id: str | None = None  # gmail thread id, for resolving siblings


class StatusUpdate(BaseModel):
    status: str


class ResolveThread(BaseModel):
    thread_id: str
    # Every pending/countered event on this thread whose external_id is NOT
    # in this list gets declined + removed from the calendar. Pass the one
    # confirmed slot's id when a thread resolves, the CURRENT round's slot
    # ids when a thread moves from pending to countered (so slots from a
    # previous, now-superseded round don't linger), or [] to clear all of
    # them (the thread was declined outright).
    keep_external_ids: list[str] = []


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
async def list_events(include_declined: bool = False):
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        if include_declined:
            cur = await conn.execute("SELECT * FROM events ORDER BY starts_at")
        else:
            cur = await conn.execute(
                "SELECT * FROM events WHERE status != 'declined' ORDER BY starts_at"
            )
        rows = await cur.fetchall()
    return {"events": rows, "count": len(rows)}


@app.post("/events")
async def create_event(event: Event):
    """Create (or, if the external_id already exists, update in place) an
    event. Idempotent on external_id so re-syncs never duplicate — this is
    also how a 'pending' slot flips to 'confirmed' without becoming a second
    calendar entry: same external_id, status just changes underneath it.

    A manually-added event (no external_id given, e.g. from the "Add an
    event" box) gets one generated here — otherwise it would never get
    pushed to Google Calendar at all, since the push step is keyed on
    having an external_id. Generating one makes every event, regardless of
    source, sync out to the real calendar the same way.
    """
    if not event.external_id:
        event.external_id = f"manual:{uuid.uuid4()}"
    status = event.status if event.status in VALID_STATUSES else "confirmed"
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        existing = None
        if event.external_id:
            cur = await conn.execute(
                "SELECT * FROM events WHERE external_id = %s", (event.external_id,)
            )
            existing = await cur.fetchone()

        if existing:
            changed = existing["status"] != status or existing["starts_at"] != event.starts_at
            if changed:
                cur = await conn.execute(
                    """
                    UPDATE events SET title = %s, starts_at = %s, ends_at = %s,
                           status = %s, thread_id = COALESCE(%s, thread_id)
                    WHERE external_id = %s
                    RETURNING *
                    """,
                    (event.title, event.starts_at, event.ends_at, status,
                     event.thread_id, event.external_id),
                )
                row = await cur.fetchone()
                gcal_id = await gcal.upsert(event.external_id, row["title"], row["starts_at"],
                                             row["ends_at"], status, known_gcal_id=row.get("gcal_event_id"))
                if gcal_id:
                    await conn.execute(
                        "UPDATE events SET gcal_event_id = %s WHERE external_id = %s",
                        (gcal_id, event.external_id),
                    )
                    row["gcal_event_id"] = gcal_id
                return {"event": row, "created": False, "updated": True}
            return {"event": existing, "created": False, "updated": False}

        entry = {
            "id": str(uuid.uuid4()),
            "title": event.title,
            "starts_at": event.starts_at,
            "ends_at": event.ends_at,
            "source": event.source,
            "external_id": event.external_id,
            "status": status,
            "thread_id": event.thread_id,
            "created_at": datetime.utcnow().isoformat(),
        }
        cur = await conn.execute(
            """
            INSERT INTO events (id, title, starts_at, ends_at, source, external_id,
                                 status, thread_id, created_at)
            VALUES (%(id)s, %(title)s, %(starts_at)s, %(ends_at)s, %(source)s,
                    %(external_id)s, %(status)s, %(thread_id)s, %(created_at)s)
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
            return {"event": await cur.fetchone(), "created": False, "updated": False}

        if event.external_id:
            gcal_id = await gcal.upsert(event.external_id, row["title"], row["starts_at"],
                                         row["ends_at"], status)
            if gcal_id:
                await conn.execute(
                    "UPDATE events SET gcal_event_id = %s WHERE id = %s", (gcal_id, row["id"])
                )
                row["gcal_event_id"] = gcal_id
    return {"event": row, "created": True, "updated": False}


@app.patch("/events/{event_id}/status")
async def update_status(event_id: str, body: StatusUpdate):
    """Flip a single event's status (e.g. pending -> confirmed) and re-push
    it to Google Calendar with the new color/status."""
    if body.status not in VALID_STATUSES:
        return {"error": f"status must be one of {sorted(VALID_STATUSES)}"}
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "UPDATE events SET status = %s WHERE id = %s RETURNING *", (body.status, event_id)
        )
        row = await cur.fetchone()
        if not row:
            return {"error": "not found"}
        if row["external_id"]:
            if body.status == "declined":
                await gcal.delete(row["external_id"], known_gcal_id=row.get("gcal_event_id"))
            else:
                gcal_id = await gcal.upsert(row["external_id"], row["title"], row["starts_at"],
                                             row["ends_at"], body.status, known_gcal_id=row.get("gcal_event_id"))
                if gcal_id:
                    await conn.execute(
                        "UPDATE events SET gcal_event_id = %s WHERE id = %s", (gcal_id, row["id"])
                    )
    return {"event": row}


@app.post("/events/resolve-thread")
async def resolve_thread(body: ResolveThread):
    """Once one slot in a thread is confirmed, decline + de-list every other
    pending/countered slot from the same thread so the calendar (and the
    dashboard) never show three tentative holds for a meeting that's
    already been nailed down to one time."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            """
            SELECT * FROM events
            WHERE thread_id = %s AND status IN ('pending', 'countered')
                  AND NOT (external_id = ANY(%s))
            """,
            (body.thread_id, body.keep_external_ids),
        )
        siblings = await cur.fetchall()
        for s in siblings:
            await conn.execute("UPDATE events SET status = 'declined' WHERE id = %s", (s["id"],))
            if s["external_id"]:
                await gcal.delete(s["external_id"], known_gcal_id=s.get("gcal_event_id"))
    return {"declined": len(siblings)}


@app.post("/sync-from-calendar")
async def sync_from_calendar():
    """Pull in anything added directly to the real Google Calendar (from
    your phone, or Google Calendar's own UI) that this app didn't create
    itself — the other direction of sync from the gmail-driven push. Safe
    to call repeatedly: keyed on Google's own event id, so re-pulling the
    same event just no-ops or updates in place, never duplicates."""
    events = await gcal.list_upcoming()
    if events is None:
        return {"synced": False, "reason": "Calendar not connected/reachable"}

    imported, updated = 0, 0
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        for ev in events:
            ext_id = f"gcal:{ev['gcal_id']}"
            cur = await conn.execute("SELECT * FROM events WHERE external_id = %s", (ext_id,))
            existing = await cur.fetchone()

            if ev["cancelled"]:
                if existing and existing["status"] != "declined":
                    await conn.execute(
                        "UPDATE events SET status = 'declined' WHERE external_id = %s", (ext_id,)
                    )
                continue

            if existing:
                if existing["status"] != ev["status"] or existing["starts_at"] != ev["starts_at"]:
                    await conn.execute(
                        "UPDATE events SET title = %s, starts_at = %s, status = %s WHERE external_id = %s",
                        (ev["title"], ev["starts_at"], ev["status"], ext_id),
                    )
                    updated += 1
                continue

            await conn.execute(
                """
                INSERT INTO events (id, title, starts_at, source, external_id, status,
                                     gcal_event_id, created_at)
                VALUES (%s, %s, %s, 'google_calendar', %s, %s, %s, %s)
                ON CONFLICT (external_id) DO NOTHING
                """,
                (str(uuid.uuid4()), ev["title"], ev["starts_at"], ext_id, ev["status"],
                 ev["gcal_id"], datetime.utcnow().isoformat()),
            )
            imported += 1
    return {"synced": True, "imported": imported, "updated": updated}


@app.get("/")
async def root():
    return {"app": "Schedule", "endpoints": [
        "/events", "/events/{id}/status", "/events/resolve-thread",
        "/sync-from-calendar", "/health",
    ]}
