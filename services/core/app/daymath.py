"""The core service's clock seam and score arithmetic — no I/O, no database.

Split out of `main.py` for the reason `docs/TESTING.md` gives: these are the
functions that decide what the dashboard tells you to do, and they were the
ones with no tests, because `main.py` cannot be imported without a database.
`assistant/app/dashboard.py` and `budget/app/paycheck.py` already exist for the
same reason. Nothing here touches the network, the clock at import time, or
Postgres, so every function can be driven at any date a test likes.

ONE CLOCK. All date logic goes through `today()` / `local_day()`, both anchored
to `LOCAL_TZ`. The box runs in UTC and the user does not.
"""
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))


def today(now: datetime | None = None) -> date:
    """The user's current calendar day. `now` is injectable so tests never
    depend on when they run."""
    return (now or datetime.now(EASTERN)).astimezone(EASTERN).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def local_day(raw) -> date | None:
    """The LOCAL calendar day a stored timestamp falls on.

    `.date()` used to be taken straight off the parsed timestamp, which is the
    UTC day whenever the string carries a UTC offset — and every fitness visit
    did, because they were written as naive UTC. A 9pm workout in New York is
    01:00 the next day in UTC, so it was credited to tomorrow, and a
    Sunday-evening one to next week. `today()` and `week_start()` are both
    local, so comparing a UTC day against them compared two different
    calendars: the dashboard could tell you to go to the gym you had just come
    back from, and dock your score for not having gone.

    A naive string is read as UTC, which is what the fitness service wrote
    before it started emitting offsets, so historical rows bucket correctly too
    rather than only new ones.
    """
    if not raw:
        return None
    text = str(raw).strip()
    # A DATE-ONLY string is already a calendar day and carries no instant, so it
    # must never be shifted. `datetime.fromisoformat("2026-09-06")` happily
    # returns naive midnight, and reading that as UTC would move an exam date
    # back a day in any timezone behind UTC. Harmless in the old code, which
    # took `.date()` straight back off it; not harmless now.
    if "T" not in text and " " not in text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(EASTERN).date()


def compute_score(components: dict, weights: dict) -> dict:
    """Transparent daily score. Each component is a 0..1 ratio; the score is the
    weighted average over the components that are actually TRACKED, renormalised
    to 100. Partial completion is rewarded.

    `null` means NOT YET, and is excluded from the average — it is not a zero.
    The docstring always said so; the code used to say `0.0 if ratio is None`,
    which scored a goal you never set exactly the same as a goal you set and
    missed. On a fresh morning that silently removed the full weight of every
    unset component, so the header read like a bad day before the day had
    happened — and the number people learn to ignore is the one that is wrong
    early. This is the rule `docs/BUDGETS.md` already enforces one service over:
    zero and unknown are different states.

    Excluding by renormalisation is exactly what already happens to a component
    whose weight is 0, so an unset component now behaves like a disabled one.

    `score` is None when nothing is tracked yet — "nothing tracked yet" is a
    true statement about a fresh day and `0` is not. `tracked` and `of` say what
    the score was computed over, so the UI can show "74, from 4 of 5".
    """
    active = {k: w for k, w in weights.items() if w and w > 0}
    parts = {}
    acc = 0.0
    tracked_w = 0
    tracked_n = 0
    for k, w in active.items():
        ratio = components.get(k)
        if ratio is None:
            parts[k] = {"ratio": None, "weight": w, "tracked": False}
            continue
        ratio = max(0.0, min(1.0, float(ratio)))
        parts[k] = {"ratio": round(ratio, 3), "weight": w, "tracked": True}
        acc += ratio * w
        tracked_w += w
        tracked_n += 1
    return {
        "score": round(100 * acc / tracked_w) if tracked_w else None,
        "parts": parts,
        "tracked": tracked_n,
        "of": len(active),
    }
