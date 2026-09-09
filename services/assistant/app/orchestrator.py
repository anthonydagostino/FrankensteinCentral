import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

# The box runs in UTC. "2:00 PM" in an interview email means 2pm where Anthony
# is, so every timestamp this module produces is anchored to that zone and
# carries its offset. Same default and same env var as main.LOCAL_TZ; kept
# local so this module stays importable without pulling in main's I/O.
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# \b on both ends, matching gmail/dateparse.py. Without a leading boundary,
# "Trojan 25" contains "jan 25" and books an interview in January.
_DATE_RE = re.compile(
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})\b",
    re.IGNORECASE,
)
# `\b` cannot follow a pattern ending in "." — the boundary needs a word
# character on one side, and "p.m." ends with punctuation, so the dotted forms
# never matched at all. "2:00 p.m." silently lost its time and fell back to the
# 9am default: not a missed parse, a WRONG time on a real calendar.
_TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.m\.|p\.m\.|am|pm)(?![a-z])",
    re.IGNORECASE,
)


def extract_datetime(text: str, now: datetime | None = None) -> str | None:
    """Best-effort parse of a date/time from free text into an ISO string.

    Returns None when no date is found. Used to turn 'Interview scheduled:
    Fri Jul 25, 2:00 PM' into a calendar timestamp — which `main` posts to the
    schedule service, which pushes it to the real Google Calendar. This is a
    parser with a side effect on an external system, so its three previous
    defects each had a physical consequence (SCRUM-134):

    * The result was naive — no offset at all — leaving every consumer to
      guess what "14:00" meant. A naive timestamp handed to a calendar is the
      most reliable way to put an event hours from where it belongs.
    * The year was always the CURRENT one. In December, "Interview scheduled:
      Jan 8, 2:00 PM" booked January 8th of the year that was ending: eleven
      months in the past, behind you in the calendar, and invisible to
      `_event_minutes_until`, which only looks forward. You found out by not
      being at the interview.
    * That year came from `utcnow()`. For the last hours of December 31st in
      New York, UTC is already in January, so even "the current year" was
      wrong — in the one window where the rollover matters most.

    `now` is the clock seam `docs/TESTING.md` requires: tests sweep a calendar
    through it rather than running against today.

    The rollover rule is deliberately the same one `gmail/dateparse.py` uses —
    a date earlier in the year than today means next year. Two parsers in one
    service tree cannot be merged while each service builds from its own
    directory, but they can at least agree on what a date means.
    """
    now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    dm = _DATE_RE.search(text)
    if not dm:
        return None
    month = MONTHS[dm.group("mon").lower()[:3]]
    day = int(dm.group("day"))

    hour, minute = 9, 0
    tm = _TIME_RE.search(text)
    if tm:
        hour = int(tm.group("h")) % 12
        minute = int(tm.group("m") or 0)
        if "p" in tm.group("ap").lower():
            hour += 12
    try:
        cand = datetime(now.year, month, day, hour, minute, tzinfo=LOCAL_TZ)
    except ValueError:
        return None
    if cand.date() < now.date():
        # Compared by DATE, not by instant: an email naming today at 2pm,
        # read at 4pm, means today — not this date twelve months from now.
        # Only a date already behind us in the year rolls forward.
        try:
            cand = cand.replace(year=now.year + 1)
        except ValueError:
            return None  # Feb 29 in a year whose successor is not a leap year
    return cand.isoformat()
