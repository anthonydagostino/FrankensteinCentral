"""Push events to the real Google Calendar, color-coded by status.

Borrows the access token from the gmail service's /internal/token (same
connected account, one OAuth consent covers both — see
services/gmail/app/main.py). Best-effort throughout: if Calendar isn't
reachable or not connected yet, callers keep working off the local Postgres
copy, which stays the source of truth either way.

Color/status scheme (Google Calendar colorId + event status), chosen so a
tentative event is visually distinct even in clients that ignore `status`:

    pending    -> tentative, colorId 5  (Banana/yellow)  "⏳ Proposed: "
    countered  -> tentative, colorId 6  (Tangerine/orange) "❓ Their offer: "
    confirmed  -> confirmed, colorId 10 (Basil/dark green)
    declined   -> cancelled, colorId 11 (Tomato/red) then hard-deleted —
                  the Postgres row is the audit trail, the calendar doesn't
                  need a permanent tombstone for a slot that didn't happen.
"""
import hashlib
import os
from datetime import datetime, timedelta

import httpx

GMAIL_URL = os.environ.get("GMAIL_URL", "http://gmail:8000")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

STATUS_COLOR = {"pending": "5", "countered": "6", "confirmed": "10", "declined": "11"}
STATUS_GCAL = {"pending": "tentative", "countered": "tentative", "confirmed": "confirmed", "declined": "cancelled"}
TITLE_PREFIX = {"pending": "⏳ Proposed: ", "countered": "❓ Their offer: ", "confirmed": "", "declined": "✕ "}


def gcal_event_id(external_id: str) -> str:
    """Deterministic Google Calendar event ID from our external_id, so a
    re-sync always maps to the same Calendar event (PATCH-or-insert) instead
    of creating a duplicate. Google requires lowercase base32hex chars
    (0-9a-v); a sha1 hex digest (0-9a-f) already satisfies that."""
    return "fc" + hashlib.sha1(external_id.encode()).hexdigest()[:24]


async def _token() -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{GMAIL_URL}/internal/token", timeout=8)
        if r.status_code != 200:
            return None
        return r.json().get("access_token")
    except Exception:  # noqa: BLE001 - gmail unreachable/not connected, just skip
        return None


def _body(title: str, starts_at: str, ends_at: str | None, status: str) -> dict:
    end = ends_at or starts_at
    return {
        "summary": f"{TITLE_PREFIX.get(status, '')}{title}",
        "start": {"dateTime": starts_at},
        "end": {"dateTime": end},
        "status": STATUS_GCAL.get(status, "confirmed"),
        "colorId": STATUS_COLOR.get(status, "10"),
        "extendedProperties": {"private": {"frankenstein_status": status}},
    }


async def upsert(external_id: str, title: str, starts_at: str, ends_at: str | None, status: str,
                  known_gcal_id: str | None = None) -> str | None:
    """Create or update the Calendar event for this row. Returns the Google
    event id on success, None if Calendar isn't connected/reachable.

    Pass known_gcal_id for a row that was pulled IN from Google Calendar
    (its real event id, stored on import) — otherwise this would derive a
    synthetic id from external_id and create a second, duplicate event
    instead of updating the one that already exists there.
    """
    token = await _token()
    if not token:
        return None
    event_id = known_gcal_id or gcal_event_id(external_id)
    headers = {"Authorization": f"Bearer {token}"}
    body = _body(title, starts_at, ends_at, status)
    base = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(f"{base}/{event_id}", json=body, headers=headers, timeout=10)
            if r.status_code == 404:
                r = await client.post(base, json={**body, "id": event_id}, headers=headers, timeout=10)
            if r.status_code < 300:
                return event_id
    except Exception:  # noqa: BLE001 - best-effort push, local DB stays authoritative
        pass
    return None


def _event_start(event: dict) -> str | None:
    """Google represents timed events as start.dateTime (has an offset) and
    all-day events as start.date (bare "2026-08-01", no time). Normalize the
    latter to a plain local-midnight string — parsing a bare date as UTC
    would shift it a day in any timezone behind UTC, which is how most of
    this app's users are set up."""
    start = event.get("start", {})
    if start.get("dateTime"):
        return start["dateTime"]
    if start.get("date"):
        return f"{start['date']}T00:00:00"
    return None


async def list_upcoming(days_back: int = 7, days_forward: int = 120) -> list[dict] | None:
    """Pull events from the real Google Calendar within a window — this is
    the other half of sync: events added directly on your phone (or in
    Google Calendar's own UI) show up here too, not just the ones this app
    pushed out. None on failure (not connected / unreachable)."""
    token = await _token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    time_min = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"
    time_max = (datetime.utcnow() + timedelta(days=days_forward)).isoformat() + "Z"
    params = {
        "timeMin": time_min, "timeMax": time_max,
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
    }
    url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        out = []
        for ev in r.json().get("items", []):
            # Skip events THIS app pushed — they're already tracked under
            # their own thread:/manual: external_id, re-importing them here
            # would just create a duplicate row for the same event.
            if ev.get("extendedProperties", {}).get("private", {}).get("frankenstein_status"):
                continue
            starts_at = _event_start(ev)
            if not starts_at:
                continue
            out.append({
                "gcal_id": ev["id"],
                "title": ev.get("summary") or "(untitled)",
                "starts_at": starts_at,
                "status": "confirmed" if ev.get("status") == "confirmed" else "pending",
                "cancelled": ev.get("status") == "cancelled",
            })
        return out
    except Exception:  # noqa: BLE001 - best-effort pull, local DB stays authoritative
        return None


async def delete(external_id: str, known_gcal_id: str | None = None) -> bool:
    token = await _token()
    if not token:
        return False
    event_id = known_gcal_id or gcal_event_id(external_id)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events/{event_id}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(url, headers=headers, timeout=10)
        return r.status_code < 300 or r.status_code == 410  # 410 = already gone
    except Exception:  # noqa: BLE001
        return False
