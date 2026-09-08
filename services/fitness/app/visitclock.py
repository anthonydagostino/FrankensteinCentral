"""When a workout happened — no I/O, so it can be tested at any date.

Split out for the reason `docs/TESTING.md` gives: `main.py` cannot be imported
without a database, so the one piece of date logic in this service had no tests,
and it was wrong.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))


def visit_instant(when: str | None, now: datetime | None = None) -> str:
    """The moment a workout happened, stored WITH its offset.

    This used to be `datetime.utcnow().isoformat()` — a naive UTC string. Every
    reader then had to guess a timezone, and `core` guessed wrong by taking
    `.date()` off the raw value: a workout at 9pm in New York is already
    tomorrow in UTC, so it was credited to the next day, and one on Sunday
    evening to the next WEEK. That fed the fitness score component (weight 20)
    and the gym rule in `_do_next`, so the dashboard could tell you to go to the
    gym you had just come back from, and dock your score for not having gone.

    An offset-bearing timestamp makes the instant unambiguous. A `when` supplied
    without an offset is read as LOCAL time, because someone typing "8pm" means
    8pm where they are — not 8pm UTC, which is the afternoon.

    `now` is injectable so tests never depend on when they run.
    """
    if not when:
        return (now or datetime.now(EASTERN)).astimezone(EASTERN).isoformat()
    try:
        parsed = datetime.fromisoformat(str(when).strip().replace("Z", "+00:00"))
    except ValueError:
        # Not a timestamp we understand. Stored as given rather than silently
        # replaced with "now" — a wrong value the user can see and correct beats
        # a plausible one they cannot.
        return str(when)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.isoformat()
