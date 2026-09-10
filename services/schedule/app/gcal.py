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


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"

# SCRUM-114. gmail's /internal/token now requires this shared secret; compose
# injects the same .env value into both containers. Empty here means every
# call will 404, which is the correct and VISIBLE failure — see _credential.
INTERNAL_SECRET = os.environ.get("FC_INTERNAL_SECRET", "")


async def _credential() -> dict | None:
    """The borrowed Google credential, or None when there isn't one.

    Returns the whole payload, not just the token, because the scopes it was
    granted are what separate "Calendar said no to this credential" from
    "Calendar could not be reached" — and those two need different actions
    from the person reading the dashboard.
    """
    if not INTERNAL_SECRET:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{GMAIL_URL}/internal/token", timeout=8,
                                 headers={"X-Internal-Secret": INTERNAL_SECRET})
        if r.status_code != 200:
            return None
        payload = r.json()
        return payload if payload.get("access_token") else None
    except Exception:  # noqa: BLE001 - gmail unreachable/not connected, just skip
        return None


async def _token() -> str | None:
    cred = await _credential()
    return cred.get("access_token") if cred else None


def _stamp(value: str) -> dict:
    """One event boundary in the shape Google expects for it.

    A bare "2026-10-31" is an all-day boundary and must be sent as `date`;
    sending it as `dateTime` is rejected outright. This matters now that
    `_event_start` passes all-day dates through unpadded — an imported
    birthday whose status is later changed here would otherwise be pushed
    back in a shape the API refuses.
    """
    return {"date": value} if "T" not in value else {"dateTime": value}


def _body(title: str, starts_at: str, ends_at: str | None, status: str) -> dict:
    end = ends_at or starts_at
    # Google's all-day end is exclusive, so a single all-day event has to be
    # sent ending the FOLLOWING day — the mirror of the pull-back in
    # _event_end. Sending start == end for an all-day event is rejected.
    if "T" not in end:
        try:
            end = (datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        except ValueError:
            pass
    return {
        "summary": f"{TITLE_PREFIX.get(status, '')}{title}",
        "start": _stamp(starts_at),
        "end": _stamp(end),
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
    """Google represents timed events as start.dateTime (carries an offset)
    and all-day events as start.date (bare "2026-08-01", no time).

    The bare date is passed through AS a bare date. It used to be padded to
    "...T00:00:00", which made every all-day event indistinguishable from one
    that genuinely starts at midnight: the dashboard decides all-day by the
    absence of a time component (see dashboard._event_bounds), so a birthday
    was being filed under "Morning" and labelled "12 AM". Consumers already
    read a naive value as local, so nothing is shifted a day by this.
    """
    start = event.get("start", {})
    if start.get("dateTime"):
        return start["dateTime"]
    if start.get("date"):
        return start["date"]
    return None


def _event_end(event: dict, starts_at: str) -> str | None:
    """The end of an event, or None when Google gives no usable one.

    Google's all-day `end.date` is EXCLUSIVE — a one-day event on the 31st
    ends "2026-11-01" — so it is pulled back a day to the last day the event
    actually covers. Carrying it through untouched would stretch every
    all-day event into the following column.
    """
    end = event.get("end", {})
    if end.get("dateTime"):
        return end["dateTime"]
    if end.get("date"):
        try:
            last = datetime.strptime(end["date"], "%Y-%m-%d").date() - timedelta(days=1)
        except ValueError:
            return None
        # A same-day all-day event ends where it starts; don't invent a range.
        return None if last.isoformat() <= starts_at else last.isoformat()
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
                # Imported so the day card can show "2 PM – 3 PM" rather than
                # a bare start. Without an end, an hour-long meeting and an
                # all-afternoon one are drawn identically, and the overlap
                # detection in dashboard._mark_conflicts has nothing to work
                # with — every Google event looked zero-length and only ever
                # clashed on an exactly equal start time.
                "ends_at": _event_end(ev, starts_at),
                "location": (ev.get("location") or "").strip() or None,
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


async def probe() -> str:
    """Read-only Calendar health: `ok`, `disconnected`, `needs_consent`,
    `unreachable`.

    `list_upcoming` collapses "no token" and "the call failed" into a single
    None, which is fine for syncing but useless for reporting health — the
    dashboard has to tell a missing credential apart from a broken connection.
    This asks the same endpoint for a single event and reports which happened.

    `needs_consent` is the state this app spent its whole life unable to name,
    and it is the overwhelmingly likely one here: the credential is borrowed
    from the gmail service, whose refresh token can predate the day
    calendar.events was added to SCOPES (the deployment reuses PowerBuy's
    token via GOOGLE_REFRESH_TOKEN). A refresh token keeps the grant it was
    minted with forever, so Google answers every Calendar call with 403
    insufficient scope — permanently, and identically to a network fault
    under the old two-way split. Reported as `unreachable` it reads as
    "try again later", which is advice that can never work. It needs one
    click through /auth/login, and the dashboard can only say so if this
    function distinguishes it.

    It is a GET and it writes nothing, so it is safe on a dashboard read path.
    Deliberately NOT `POST /sync-from-calendar`: that imports events into
    Postgres, which makes it a mutation, not a probe.
    """
    # SCRUM-114: gmail's /internal/token requires a shared secret and this
    # deployment has not been given one, so the credential is unreachable for
    # a reason no amount of waiting fixes and no amount of reconnecting fixes
    # either. "unreachable" would invite waiting and "disconnected" would send
    # you to re-consent Google, which would not help; both would be a wrong
    # instruction rather than merely a vague one.
    if not INTERNAL_SECRET:
        return "not_configured"
    cred = await _credential()
    if not cred:
        return "disconnected"
    # The credential can rule itself out before a single API call: gmail
    # reports the scopes Google actually granted it. `None` means gmail has
    # not established them yet, which is not evidence of anything — fall
    # through and let the live call decide.
    if cred.get("calendar_scope") is False:
        return "needs_consent"
    params = {
        "timeMin": datetime.utcnow().isoformat() + "Z",
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 1,
    }
    url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params,
                                 headers={"Authorization": f"Bearer {cred['access_token']}"},
                                 timeout=8)
    except Exception:  # noqa: BLE001 - unreachable is a reportable state
        return "unreachable"
    if r.status_code == 200:
        return "ok"
    # 401/403: the credential exists and Calendar refused it. That is a
    # consent problem and waiting will not fix it.
    if r.status_code in (401, 403):
        return "needs_consent"
    return "unreachable"
