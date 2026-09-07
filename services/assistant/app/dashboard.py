"""Pure helpers for what the home screen shows.

Kept out of `main.py` deliberately: `main.py` imports psycopg and FastAPI, so
anything defined there can only be tested with a database driver installed.
These functions are the two places the dashboard was reporting things it did
not know, so they are exactly the parts that need tests that run everywhere.

Nothing here does I/O.
"""
from datetime import datetime, timedelta


def parse_event_dt(raw, local_tz):
    """An event timestamp as an aware datetime in `local_tz`, or None.

    A naive string is read as local, not UTC. The schedule service stores
    whatever the calendar handed it, and reading a naive 9pm as UTC would move
    an evening event to the following day in New York — the same class of bug
    `docs/TESTING.md` was written about.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)


def upcoming_events(events, now, local_tz, limit=6, statuses=None):
    """Events that have not finished yet, soonest first.

    `GET /events` returns every event ever stored, ascending by `starts_at`,
    with no lower bound — so `events[0]` is the OLDEST record on file, not the
    next thing happening. Every caller that wants "next" has to bound it here,
    or it reports history as if it were the future.

    An event still counts as upcoming while it is running: `ends_at` decides
    when it is present, so a meeting you are in the middle of does not vanish
    from the page halfway through. Unparseable timestamps are dropped rather
    than guessed at, and `declined` is filtered by the caller.

    `statuses`, when given, keeps only those statuses — the recommendation
    rules act on confirmed commitments, while the calendar card deliberately
    shows pending and countered holds too.
    """
    dated = []
    for e in events or []:
        if statuses is not None and e.get("status", "confirmed") not in statuses:
            continue
        start = parse_event_dt(e.get("starts_at") or e.get("start"), local_tz)
        if start is None:
            continue
        end = parse_event_dt(e.get("ends_at"), local_tz) or start
        if end < now:
            continue
        dated.append((start, e))
    dated.sort(key=lambda pair: pair[0])
    picked = [e for _, e in dated]
    return picked[:limit] if limit else picked


def firefly_state(firefly):
    """`ok`, `unreachable` or `not_configured` — three states, not two.

    The assistant's `_get` swallows a timeout and returns `{}`, so an empty
    payload means the service could not be reached. That is NOT the same as
    Firefly answering that it has no credentials. Collapsing both into "not
    connected" tells you to set FIREFLY_URL every time a container blinks,
    which sends you to repair configuration that is already correct.
    """
    if not firefly:
        return "unreachable"
    if firefly.get("connected") is False:
        return "not_configured"
    return "ok"


# --- the seven-day week window ----------------------------------------------
#
# The dashboard's schedule card used to be one flat list of the next six
# events. That answers "what is next" but not "what does my week look like",
# and it silently rendered an unreachable schedule service as a calm empty day.
# Everything below is pure and takes `now`/`local_tz` from the caller, so the
# calendar sweep in the tests can pin any date it likes — per docs/TESTING.md,
# none of this is ever exercised against the real "today".

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")

# One decorative theme key per month, consumed by CSS only. The key never
# carries meaning about the data — it is picked from the date and nothing else.
SEASON_KEYS = ("jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec")

WINDOW_DAYS = 7


def ordinal_suffix(day):
    """`st`/`nd`/`rd`/`th` for a day of the month.

    The 11/12/13 branch is the whole reason this is not `day % 10`: the
    eleventh is the 11th, not the 11st, and the same holds for 12 and 13.
    """
    if 11 <= (day % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def ordinal(day):
    return f"{day}{ordinal_suffix(day)}"


def schedule_state(schedule):
    """`ok` or `unreachable` — an outage is not an empty week.

    The assistant's `_get` swallows a timeout and returns `{}`, exactly as it
    does for Firefly. Without this distinction a schedule service that is down
    renders as "nothing coming up", which is the calmest possible way to tell
    someone they have no commitments on a day they do. Same reasoning as
    `firefly_state`, and the same three-states-not-two rule.
    """
    if not schedule:
        return "unreachable"
    return "ok"


def _event_bounds(event, local_tz):
    """(start, end, all_day) in local time, or None when unparseable.

    A date-only timestamp ("2026-10-31") is an all-day event: it has no clock
    time to show, and rendering it as midnight would file a Halloween all-dayer
    under "Morning" alongside a 7am alarm.
    """
    raw_start = event.get("starts_at") or event.get("start")
    start = parse_event_dt(raw_start, local_tz)
    if start is None:
        return None
    all_day = "T" not in str(raw_start) and " " not in str(raw_start).strip()
    end = parse_event_dt(event.get("ends_at"), local_tz) or start
    if end < start:
        end = start
    return start, end, all_day


def _slot_for(dt, all_day):
    if all_day:
        return "allday"
    hour = dt.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _time_label(dt, all_day):
    """`9 AM`, `9:30 PM`, `All day`. The `:00` is dropped on the hour."""
    if all_day:
        return "All day"
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour} {suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d} {suffix}"


def _mark_conflicts(entries):
    """Flag every entry whose time overlaps another one's.

    Two commitments in the same hour is precisely the "approaching conflict"
    the product vision asks the hub to surface, and it is invisible in a flat
    list. All-day events do not conflict with timed ones — an all-day marker
    is context, not a competing obligation.

    This runs over the WHOLE window, not one day at a time. A 23:30 call and
    a 00:15 call are a real collision even though the calendar files them
    under different dates, and a per-day pass is structurally blind to it.
    """
    timed = [e for e in entries if not e["all_day"]]
    for a_index, a in enumerate(timed):
        for b in timed[a_index + 1:]:
            # Identical starts collide even when both are zero-length, which a
            # plain interval test would miss.
            hit = (a["_start"] == b["_start"] or
                   (a["_start"] < b["_end"] and b["_start"] < a["_end"]))
            if hit:
                a["conflict"] = True
                b["conflict"] = True
                # Named so the column can say which neighbour it clashes
                # with when that neighbour is not in the same column. The
                # direction matters: telling someone a 00:15 call "overlaps
                # next day" when the other half is the night before is worse
                # than saying nothing.
                if a["_start"].date() != b["_start"].date():
                    earlier, later = ((a, b) if a["_start"] <= b["_start"]
                                      else (b, a))
                    earlier["conflict_offday"] = True
                    later["conflict_offday"] = True
                    earlier["conflict_neighbour"] = "next"
                    later["conflict_neighbour"] = "previous"


def week_window(events, now, local_tz, days=WINDOW_DAYS):
    """Today plus the next `days - 1` local days, each with its own events.

    Bucketing is done on local *dates*, never by adding 24-hour offsets to a
    timestamp: a 23-hour or 25-hour DST day would slide events into the
    neighbouring column, which is the same class of bug docs/TESTING.md was
    written about.

    An event already in progress stays on today rather than disappearing
    backwards off the grid, and anything past the last day is counted in
    `beyond` instead of being silently dropped.
    """
    today = now.astimezone(local_tz).date()
    window = [today + timedelta(days=offset) for offset in range(days)]
    index = {day: [] for day in window}
    last_day = window[-1]
    beyond = 0

    for event in events or []:
        if event.get("status", "confirmed") == "declined":
            continue
        bounds = _event_bounds(event, local_tz)
        if bounds is None:
            continue
        start, end, all_day = bounds
        if end < now and not all_day:
            continue  # already finished
        day = start.date()
        ongoing = day < today
        if ongoing:
            # Started earlier, still running: it belongs to today's column.
            if end.date() < today:
                continue
            day = today
        if day > last_day:
            beyond += 1
            continue
        if day not in index:
            continue
        index[day].append({
            **event,
            "time_label": _time_label(start, all_day),
            "end_label": None if all_day or end == start else _time_label(end, all_day),
            "slot": _slot_for(start, all_day),
            "all_day": all_day,
            "ongoing": ongoing or (start <= now <= end and not all_day),
            "needs_you": event.get("status") == "countered",
            "conflict": False,
            "conflict_offday": False,
            "conflict_neighbour": None,
            "_start": start,
            "_end": end,
        })

    # One pass over every placed event, so an overlap that straddles midnight
    # is caught. Must happen before the per-day sort strips the bounds.
    _mark_conflicts([e for day in window for e in index[day]])

    out = []
    previous_month = None
    for offset, day in enumerate(window):
        entries = sorted(index[day], key=lambda e: (not e["all_day"], e["_start"]))
        conflicts = sum(1 for e in entries if e["conflict"])
        statuses = [e.get("status", "confirmed") for e in entries]
        for entry in entries:
            del entry["_start"]
            del entry["_end"]
        out.append({
            "iso": day.isoformat(),
            "weekday": WEEKDAY_NAMES[day.weekday()],
            "weekday_short": WEEKDAY_NAMES[day.weekday()][:3],
            "month": MONTH_NAMES[day.month - 1],
            "month_short": MONTH_NAMES[day.month - 1][:3],
            "month_index": day.month,
            "day": day.day,
            "ordinal": ordinal(day.day),
            "ordinal_suffix": ordinal_suffix(day.day),
            "year": day.year,
            "season": SEASON_KEYS[day.month - 1],
            "is_today": offset == 0,
            "is_tomorrow": offset == 1,
            "is_weekend": day.weekday() >= 5,
            # True on the first card and wherever the window crosses into a new
            # month, so a week spanning Oct/Nov says so instead of restarting
            # its day numbers with no explanation.
            "starts_month": previous_month is None or previous_month != day.month,
            "relative_label": "Today" if offset == 0 else ("Tomorrow" if offset == 1 else ""),
            "long_label": (f"{WEEKDAY_NAMES[day.weekday()]}, {MONTH_NAMES[day.month - 1]} "
                           f"{ordinal(day.day)}, {day.year}"),
            "events": entries,
            "conflicts": conflicts,
            "counts": {
                "total": len(entries),
                "confirmed": statuses.count("confirmed"),
                "pending": statuses.count("pending"),
                "countered": statuses.count("countered"),
                "needs_you": statuses.count("countered"),
            },
        })
        previous_month = day.month

    return {"days": out, "beyond": beyond, "spans_months": len({d["month_index"] for d in out}) > 1}
